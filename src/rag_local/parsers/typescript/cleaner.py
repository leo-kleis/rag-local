import re


def clean_typescript_code(text: str) -> str:
    """Reemplaza comentarios, strings y expresiones regulares literales por espacios,
    preservando los saltos de línea para mantener la estructura original.
    """
    pattern = re.compile(
        r"(?P<single_comment>//[^\r\n]*)"
        r"|(?P<multi_comment>/\*.*?\*/)"
        r'|(?P<double_string>"(?:\\.|[^"\\])*")'
        r"|(?P<single_string>\'(?:\\.|[^\'\\])*\')"
        r"|(?P<template_string>`(?:\\.|[^`\\])*`)"
        r"|(?P<regex_literal>/(?:\\.|[^/\\\r\n])+/[gimyus]*)",
        re.DOTALL,
    )

    def replacer(match: re.Match) -> str:
        val = match.group(0)
        res = []
        for char in val:
            if char == "\n":
                res.append("\n")
            else:
                res.append(" ")
        return "".join(res)

    return pattern.sub(replacer, text)


def count_braces(line: str) -> tuple[int, int]:
    """Cuenta las llaves de apertura y cierre en una línea de forma segura."""
    # Eliminar comentarios de una línea
    s = re.sub(r"//.*", "", line)
    # Eliminar comentarios multilínea de una sola línea
    s = re.sub(r"/\*.*?\*/", "", s)
    # Eliminar cadenas de texto para evitar llaves falsas
    s = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', "", s)
    s = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "", s)
    s = re.sub(r"`[^`\\]*(?:\\.[^`\\]*)*`", "", s)
    # Eliminar expresiones regulares literales
    s = re.sub(r"/(?:\\.|[^/\\\r\n])+/[gimyus]*", "", s)
    return s.count("{"), s.count("}")
