import pytest
from rag_local.services.rag import compress_code


def test_compress_code_typescript():
    """Prueba la compresión en archivos TypeScript (comentarios, directivas, espacios)."""
    code = (
        "// Este comentario debe eliminarse\n"
        "const x = 1;\n"
        "\n"
        "\n"
        "// @ts-ignore: ignora esta línea\n"
        "const y = x + 2; // comentario al final de la línea\n"
        "/* Comentario de bloque en una sola línea debe eliminarse */\n"
        "const z = 3;   \n"
    )
    expected = (
        "const x = 1;\n"
        "\n"
        "// @ts-ignore: ignora esta línea\n"
        "const y = x + 2; // comentario al final de la línea\n"
        "const z = 3;"
    )
    res = compress_code(code, "test.ts")
    assert res == expected


def test_compress_code_python():
    """Prueba la compresión en archivos Python (comentarios, directivas, espacios)."""
    code = (
        "# Comentario general para eliminar\n"
        "def main():\n"
        "    # type: ignore\n"
        "    x = 10  # comentario final\n"
        "\n"
        "\n"
        "    return x\n"
    )
    expected = (
        "def main():\n"
        "    # type: ignore\n"
        "    x = 10  # comentario final\n"
        "\n"
        "    return x"
    )
    res = compress_code(code, "main.py")
    assert res == expected


def test_compress_code_html():
    """Prueba la compresión en archivos HTML (comentarios y espacios)."""
    code = "<!-- Comentario HTML -->\n<div>\n  <span>Hola</span>\n</div>\n"
    expected = "<div>\n  <span>Hola</span>\n</div>"
    res = compress_code(code, "index.html")
    assert res == expected


def test_compress_code_prisma():
    """Prueba la compresión en archivos Prisma (comentarios y espacios)."""
    code = "// Comentario Prisma\nmodel User {\n  id Int @id\n}\n"
    expected = "model User {\n  id Int @id\n}"
    res = compress_code(code, "schema.prisma")
    assert res == expected
