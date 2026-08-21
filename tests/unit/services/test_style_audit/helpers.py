"""Funciones utilitarias y generadores de datos simulados para auditoría."""

from __future__ import annotations

import json
from typing import Any


def make_css_entry(source: str, rules: list[dict[str, Any]]) -> dict[str, str]:
    """Crea una fila de metadatos LanceDB para reglas CSS."""
    return {
        "source": source,
        "css_rules": json.dumps(rules),
        "class_parents": "",
    }


def make_markup_entry(
    source: str, class_parents: dict[str, Any] | list[Any]
) -> dict[str, str]:
    """Crea una fila de metadatos LanceDB para datos de markup."""
    return {
        "source": source,
        "css_rules": "",
        "class_parents": json.dumps(class_parents),
    }


def make_audit_mock_data(
    css_entries: list[tuple[str, list[dict[str, Any]]]] | None = None,
    markup_entries: list[tuple[str, dict[str, Any] | list[Any]]] | None = None,
) -> list[dict[str, str]]:
    """Construye una lista combinada de filas simuladas de metadatos LanceDB."""
    entries: list[dict[str, str]] = []
    if css_entries:
        for source, rules in css_entries:
            entries.append(make_css_entry(source, rules))
    if markup_entries:
        for source, parents in markup_entries:
            entries.append(make_markup_entry(source, parents))
    return entries
