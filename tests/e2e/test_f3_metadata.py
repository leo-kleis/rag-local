import pytest

from rag_local.services.db import chunk_file


# --- F3: Extracción de metadatos (F3-01 a F3-05) ---


def test_f3_metadata_presence_in_chunk_file_output(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "test.ts"
    file_path.write_text("class Test {}", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert "metadata" in chunks[0]


def test_f3_ts_metadata_class_extraction(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "controller.ts"
    file_path.write_text("class UserController { constructor() {} }", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert chunks[0]["metadata"]["class_name"] == "UserController"


def test_f3_ts_metadata_method_extraction(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "controller.ts"
    file_path.write_text(
        "class UserController { getUser(id: number) { return id; } }", encoding="utf-8"
    )
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert "getUser" in chunks[0]["metadata"]["method_name"]


def test_f3_ts_metadata_imports_extraction(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "controller.ts"
    file_path.write_text(
        "import { Controller, Get } from '@nestjs/common';\nclass UserController {}",
        encoding="utf-8",
    )
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert "@nestjs/common" in chunks[0]["metadata"]["imports"]


def test_f3_prisma_metadata_model_extraction(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "schema.prisma"
    file_path.write_text("model Account {\n  id String @id\n}", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert chunks[0]["metadata"]["models"] == ["Account"]


# --- F3: Extracción de metadatos (F3-06 a F3-10) ---


def test_f3_boundary_invalid_syntax_ts_file(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "broken.ts"
    file_path.write_text("class Broken { constructor( }", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert "metadata" in chunks[0]


def test_f3_boundary_nested_class_metadata_extraction(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "nested.ts"
    file_path.write_text("class Outer { class Inner {} }", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert "Outer" in chunks[0]["metadata"]["class_name"]
    assert "Inner" in chunks[0]["metadata"]["class_name"]


def test_f3_boundary_prisma_unsupported_blocks(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "schema.prisma"
    file_path.write_text(
        "datasource db {\n"
        '  provider = "postgresql"\n'
        "}\n"
        "generator client {\n"
        '  provider = "prisma-client-js"\n'
        "}",
        encoding="utf-8",
    )
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert (
        "models" not in chunks[0]["metadata"] or chunks[0]["metadata"]["models"] == []
    )


def test_f3_boundary_html_nested_custom_tags(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "frontend" / "app.html"
    file_path.write_text(
        '<app-card [header]="title">'
        '<app-button (click)="do()"></app-button>'
        "</app-card>",
        encoding="utf-8",
    )
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert "app-card" in chunks[0]["metadata"]["directives"]


def test_f3_boundary_extremely_large_metadata_payload(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "bloated.ts"
    content = "\n".join(f"import {{ Dep{i} }} from './dep{i}';" for i in range(500))
    file_path.write_text(content, encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    assert len(chunks[0]["metadata"]["imports"]) <= 100
