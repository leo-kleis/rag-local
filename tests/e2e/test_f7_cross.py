import contextlib
import shutil
import pytest

from rag_local.services.db import (
    chunk_file,
    get_chroma_collection,
    get_file_hash,
    index_chunks,
    load_cache,
    query_db,
    save_cache,
    scan_files,
)
from rag_local.services.rag import process_query


# --- TIER 3: CROSS-FEATURE COMBINATIONS (6 Casos) ---


def test_f7_cross_scan_and_incremental_flow(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "schema.prisma"
    f.write_text("model User {}", encoding="utf-8")

    files = scan_files()
    assert len(files) == 1

    cache = load_cache()
    cache["backend/schema.prisma"] = get_file_hash(f)
    save_cache(cache)

    ignored_dir = repo_root / "backend" / "node_modules"
    ignored_dir.mkdir(parents=True, exist_ok=True)
    f_new = ignored_dir / "schema.prisma"
    shutil.move(str(f), str(f_new))

    files_2 = scan_files()
    assert len(files_2) == 0

    physical = {str(f.relative_to(repo_root)).replace("\\", "/") for f in files_2}
    cache = load_cache()
    deleted = [path for path in cache if path not in physical]
    assert "backend/schema.prisma" in deleted


def test_f7_cross_chunk_metadata_index_query(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "controller.ts"
    f.write_text("class AuthController { login() { return true; } }", encoding="utf-8")

    chunks = chunk_file(f)
    assert len(chunks) > 0
    assert "AuthController" in chunks[0]["metadata"]["class_name"]

    collection = get_chroma_collection()
    for c in chunks:
        c["source"] = "backend/controller.ts"
        c["scope"] = "backend"
    index_chunks(collection, chunks)

    res = process_query("login", scope="backend")
    assert "AuthController" in res["response"]


def test_f7_cross_incremental_and_chunk_fusion(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "app.ts"
    f.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6", encoding="utf-8")

    cache = load_cache()
    cache["backend/app.ts"] = get_file_hash(f)
    save_cache(cache)

    collection = get_chroma_collection()
    collection.add(
        ids=["c1", "c2"],
        embeddings=[[0.1] * 768, [0.1] * 768],
        documents=["Line 1\nLine 2\nLine 3", "Line 3\nLine 4\nLine 5"],
        metadatas=[
            {
                "source": "backend/app.ts",
                "scope": "backend",
                "start_line": 1,
                "end_line": 3,
            },
            {
                "source": "backend/app.ts",
                "scope": "backend",
                "start_line": 3,
                "end_line": 5,
            },
        ],
    )

    res = process_query("Line", scope="backend")
    assert "Line 3\nLine 3" not in res["response"]


def test_f7_cross_scope_query_and_xml_generation(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["c1"],
        embeddings=[[0.1] * 768],
        documents=["class Core {}"],
        metadatas=[
            {
                "source": "backend/core.ts",
                "scope": "backend",
                "start_line": 1,
                "end_line": 1,
            }
        ],
    )
    res = process_query("Core", scope="backend")
    assert "<context>" in res["response"]
    assert "class Core" in res["response"]


def test_f7_cross_corrupted_cache_and_db_restore(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "schema.prisma"
    f.write_text("model User {}", encoding="utf-8")

    chroma_path = setup_test_env["chroma_path"]
    (chroma_path / "ingest_cache.json").write_text("{corrupted", encoding="utf-8")

    from rag_local.cli.ingest import run_ingestion

    with contextlib.suppress(SystemExit):
        run_ingestion()

    collection = get_chroma_collection()
    assert collection.count() > 0

    res = query_db("User")
    assert len(res["ids"][0]) > 0


def test_f7_cross_incremental_revert_and_fusion(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    f = repo_root / "backend" / "app.ts"
    f.write_text("Line 1\nLine 2\nLine 3", encoding="utf-8")

    collection = get_chroma_collection()
    collection.add(
        ids=["c1", "c2"],
        embeddings=[[0.1] * 768, [0.1] * 768],
        documents=["Line 1\nLine 2", "Line 2\nLine 3"],
        metadatas=[
            {
                "source": "backend/app.ts",
                "scope": "backend",
                "start_line": 1,
                "end_line": 2,
            },
            {
                "source": "backend/app.ts",
                "scope": "backend",
                "start_line": 2,
                "end_line": 3,
            },
        ],
    )

    res1 = process_query("Line")
    assert "Line 2\nLine 2" not in res1["response"]

    collection.delete(ids=["c2"])
    collection.add(
        ids=["c2_new"],
        embeddings=[[0.1] * 768],
        documents=["Line 10\nLine 11"],
        metadatas=[
            {
                "source": "backend/app.ts",
                "scope": "backend",
                "start_line": 10,
                "end_line": 11,
            }
        ],
    )

    res2 = process_query("Line")
    assert "Line 2" in res2["response"]
    assert "Line 10" in res2["response"]
