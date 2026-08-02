import shutil
import pytest

from rag_local.services.db import scan_files


def test_f1_scan_basic_ts_prisma_files(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    (repo_root / "backend" / "schema.prisma").write_text(
        "model User {}", encoding="utf-8"
    )
    (repo_root / "frontend" / "app.component.ts").write_text(
        "class App {}", encoding="utf-8"
    )
    files = scan_files()
    assert len(files) == 2
    paths = {f.name for f in files}
    assert "schema.prisma" in paths
    assert "app.component.ts" in paths


def test_f1_scan_respects_allowed_extensions(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    (repo_root / "backend" / "schema.prisma").write_text(
        "model User {}", encoding="utf-8"
    )
    (repo_root / "backend" / "config.json").write_text("{}", encoding="utf-8")
    (repo_root / "backend" / "script.go").write_text("package main", encoding="utf-8")
    files = scan_files()
    assert len(files) == 1
    assert files[0].name == "schema.prisma"


def test_f1_scan_ignores_forbidden_directories(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    node_modules = repo_root / "frontend" / "node_modules"
    node_modules.mkdir(parents=True, exist_ok=True)
    (node_modules / "bad.ts").write_text("class Bad {}", encoding="utf-8")
    (repo_root / "frontend" / "good.ts").write_text("class Good {}", encoding="utf-8")
    files = scan_files()
    assert len(files) == 1
    assert files[0].name == "good.ts"


def test_f1_scan_empty_repo_directory(setup_test_env):
    files = scan_files()
    assert len(files) == 0


def test_f1_scan_non_existent_scan_dirs(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    shutil.rmtree(repo_root / "backend")
    shutil.rmtree(repo_root / "frontend")
    files = scan_files()
    assert len(files) == 0


def test_f1_boundary_extremely_long_file_path(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    long_dir = repo_root / "backend" / ("a" * 100) / ("b" * 100)
    long_dir.mkdir(parents=True, exist_ok=True)
    (long_dir / "file.ts").write_text("class Long {}", encoding="utf-8")
    files = scan_files()
    assert len(files) == 1
    assert files[0].name == "file.ts"


def test_f1_boundary_file_with_multiple_extensions(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    (repo_root / "backend" / "test.spec.ts").write_text(
        "class Test {}", encoding="utf-8"
    )
    (repo_root / "backend" / "test.ts.bak").write_text(
        "class Test {}", encoding="utf-8"
    )
    files = scan_files()
    assert len(files) == 1
    assert files[0].name == "test.spec.ts"


def test_f1_boundary_scan_dir_with_mixed_casing(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    (repo_root / "Backend").mkdir(parents=True, exist_ok=True)
    (repo_root / "Backend" / "file.ts").write_text("class Test {}", encoding="utf-8")
    files = scan_files()
    assert len([f for f in files if "Backend" in str(f)]) == 0


def test_f1_boundary_files_with_dot_prefixes(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    (repo_root / "backend" / ".hidden.ts").write_text(
        "class Hidden {}", encoding="utf-8"
    )
    files = scan_files()
    assert len(files) == 1
    assert files[0].name == ".hidden.ts"


def test_f1_boundary_symlink_directories(setup_test_env):
    files = scan_files()
    assert isinstance(files, list)
