import pytest

from rag_local.services.db import chunk_file


# --- F2: Chunking inteligente (F2-01 a F2-05) ---


def test_f2_chunking_ts_respects_functions(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "service.ts"
    lines = ["class Service {"]
    for i in range(45):
        lines.append(f"  method{i}() {{ return {i}; }}")
    lines.append("  targetMethod() {")
    lines.append("    console.log('must be unified');")
    lines.append("  }")
    lines.append("}")
    file_path.write_text("\n".join(lines), encoding="utf-8")

    chunks = chunk_file(file_path)
    assert len(chunks) > 0
    for chunk in chunks:
        text = chunk["text"].strip()
        assert text.endswith("}") or text.endswith("}")


def test_f2_chunking_prisma_respects_models(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "schema.prisma"
    lines = []
    for i in range(12):
        lines.append(f"model Model{i} {{")
        lines.append("  id Int @id @default(autoincrement())")
        lines.append("  name String")
        lines.append(f"  field{i} String")
        lines.append("}")
    file_path.write_text("\n".join(lines), encoding="utf-8")

    chunks = chunk_file(file_path)
    for chunk in chunks:
        text = chunk["text"]
        for line in text.splitlines():
            if line.strip().startswith("model"):
                model_name = line.split()[1]
                assert f"model {model_name}" in text
                assert "}" in text


def test_f2_chunking_html_respects_tags(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "frontend" / "app.html"
    lines = ["<div>"]
    for i in range(48):
        lines.append(f"  <p>Paragraph {i}</p>")
    lines.append("  <div class='footer'>")
    lines.append("    <span>Footer Content</span>")
    lines.append("  </div>")
    lines.append("</div>")
    file_path.write_text("\n".join(lines), encoding="utf-8")

    chunks = chunk_file(file_path)
    footer_chunk = next(c for c in chunks if "footer" in c["text"])
    assert "</div>" in footer_chunk["text"]


def test_f2_chunking_fallback_linear(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "plain.ts"
    lines = [f"// Line {i}" for i in range(60)]
    file_path.write_text("\n".join(lines), encoding="utf-8")

    chunks = chunk_file(file_path)
    assert len(chunks) == 2
    assert chunks[0]["start_line"] == 1
    assert chunks[0]["end_line"] == 50
    assert chunks[1]["start_line"] == 41
    assert chunks[1]["end_line"] == 60


def test_f2_chunking_single_line_file(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "single.ts"
    file_path.write_text("const a = 1;", encoding="utf-8")

    chunks = chunk_file(file_path)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "const a = 1;"
    assert chunks[0]["start_line"] == 1
    assert chunks[0]["end_line"] == 1


# --- F2: Chunking inteligente (F2-06 a F2-10) ---


def test_f2_boundary_extremely_large_file_lines(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "huge.ts"
    file_path.write_text(
        "\n".join(f"const line{i} = {i};" for i in range(1000)), encoding="utf-8"
    )
    chunks = chunk_file(file_path)
    assert len(chunks) > 10


def test_f2_boundary_empty_file_chunking(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "empty.ts"
    file_path.write_text("", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert chunks == []


def test_f2_boundary_unicode_and_emojis_in_file(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "unicode.ts"
    file_path.write_text("const saludo = 'Hola 🌐'; // 🚀", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) == 1
    assert "🌐" in chunks[0]["text"]


def test_f2_boundary_lines_without_newline_at_eof(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "no_eof_newline.ts"
    file_path.write_text("const a = 1;\nconst b = 2;", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "const a = 1;\nconst b = 2;"


def test_f2_boundary_only_comments_and_whitespace(setup_test_env):
    repo_root = setup_test_env["repo_root"]
    file_path = repo_root / "backend" / "comments.ts"
    file_path.write_text("// Comentario 1\n\n\n// Comentario 2\n", encoding="utf-8")
    chunks = chunk_file(file_path)
    assert len(chunks) == 0
