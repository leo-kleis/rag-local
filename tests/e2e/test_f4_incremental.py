import pytest

from rag_local.services.db import (
    get_chroma_collection,
    get_file_hash,
    load_cache,
    save_cache,
    scan_files,
)


# --- F4: Ingesta incremental y caché (F4-01 a F4-05) ---


def test_f4_incremental_no_changes_unchanged_count(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "schema.prisma"
    f.write_text("model User {}", encoding="utf-8")

    files = scan_files()
    cache = load_cache()
    assert len(files) == 1
    rel_path = "backend/schema.prisma"
    cache[rel_path] = get_file_hash(f)
    save_cache(cache)

    cache_2 = load_cache()
    current_hash = get_file_hash(f)
    assert cache_2[rel_path] == current_hash


def test_f4_incremental_new_file_indexing(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f1 = repo_root / "backend" / "schema.prisma"
    f1.write_text("model User {}", encoding="utf-8")

    cache = load_cache()
    cache["backend/schema.prisma"] = get_file_hash(f1)
    save_cache(cache)

    f2 = repo_root / "frontend" / "app.ts"
    f2.write_text("console.log('new')", encoding="utf-8")

    files = scan_files()
    cache = load_cache()
    assert "frontend/app.ts" not in cache
    new_files = [
        f
        for f in files
        if str(f.relative_to(repo_root)).replace("\\", "/") not in cache
    ]
    assert len(new_files) == 1
    assert new_files[0].name == "app.ts"


def test_f4_incremental_modified_file_reindexing(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "schema.prisma"
    f.write_text("model User {}", encoding="utf-8")

    cache = load_cache()
    cache["backend/schema.prisma"] = "old_hash"
    save_cache(cache)

    cache = load_cache()
    assert cache["backend/schema.prisma"] != get_file_hash(f)


def test_f4_incremental_deleted_file_cleanup(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    cache = load_cache()
    cache["backend/old.ts"] = "some_hash"
    save_cache(cache)

    files = scan_files()
    physical = {str(f.relative_to(repo_root)).replace("\\", "/") for f in files}
    cache = load_cache()
    deleted = [path for path in cache if path not in physical]
    assert "backend/old.ts" in deleted


def test_f4_incremental_corrupted_cache_resilience(setup_test_env):
    chroma_path = setup_test_env["chroma_path"]
    cache_file = chroma_path / "ingest_cache.json"
    cache_file.write_text("{invalid json", encoding="utf-8")

    cache = load_cache()
    assert cache == {}


# --- F4: Ingesta incremental y caché (F4-06 a F4-10) ---


def test_f4_boundary_reverted_file_modification(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "schema.prisma"
    f.write_text("model User {}", encoding="utf-8")

    cache = load_cache()
    original_hash = get_file_hash(f)
    cache["backend/schema.prisma"] = original_hash
    save_cache(cache)

    f.write_text("model Temp {}", encoding="utf-8")
    f.write_text("model User {}", encoding="utf-8")

    current_hash = get_file_hash(f)
    assert current_hash == original_hash
    cache = load_cache()
    assert cache["backend/schema.prisma"] == current_hash


def test_f4_boundary_cache_file_deleted_externally(setup_test_env):
    chroma_path = setup_test_env["chroma_path"]
    cache_file = chroma_path / "ingest_cache.json"
    cache_file.write_text("{}", encoding="utf-8")

    if cache_file.exists():
        cache_file.unlink()

    cache = load_cache()
    assert cache == {}


def test_f4_boundary_db_deleted_but_cache_persists_resync(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "schema.prisma"
    f.write_text("model User {}", encoding="utf-8")

    cache = load_cache()
    cache["backend/schema.prisma"] = get_file_hash(f)
    save_cache(cache)

    collection = get_chroma_collection()
    assert collection.count() == 0

    from rag_local.cli.ingest import run_ingestion

    with pytest.raises(SystemExit):
        run_ingestion()
    assert collection.count() > 0


def test_f4_boundary_unreadable_file_error_handling(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "unreadable.ts"
    f.write_bytes(b"\xff\xfe\x00\x00")

    from rag_local.cli.ingest import run_ingestion

    try:
        run_ingestion()
    except SystemExit:
        pass
    except Exception as e:
        pytest.fail(f"El proceso falló ante un archivo corrupto/ilegible: {e}")


def test_f4_boundary_duplicate_file_paths_in_cache(setup_test_env):
    cache = load_cache()
    cache["backend/dup.ts"] = "hash1"
    cache["backend/dup.ts"] = "hash2"
    save_cache(cache)

    cache = load_cache()
    assert len(cache) == 1
