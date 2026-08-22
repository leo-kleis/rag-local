import fnmatch
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from rag_local.services.db import get_indexed_metadata

_RESERVED_EXCLUSIONS: set[str] = {
    "default",
    "init",
    "state",
    "action",
    "actions",
    "event",
    "events",
    "error",
    "errors",
    "success",
    "data",
    "payload",
    "to_rgb",
    "torgb",
    "r",
    "g",
    "b",
    "a",
    "x",
    "y",
    "id",
    "val",
    "value",
    "key",
    "props",
    "ctx",
    "self",
    "type",
    "null",
    "undefined",
    "true",
    "false",
}


def _normalize_event_name(raw_name: str) -> str:
    """Normaliza un nombre de evento a formato snake_case limpio."""
    clean = raw_name.strip()
    if clean.startswith("event:"):
        clean = clean[6:]
    elif clean.startswith("action:"):
        clean = clean[7:]

    if clean.endswith("Event") and len(clean) > 5:
        clean = clean[:-5]

    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", clean)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _is_valid_event_key(key: str) -> bool:
    """Valida si una clave de evento es legítima y no un falso positivo."""
    if not key or len(key) <= 2:
        return False
    if key.lower() in _RESERVED_EXCLUSIONS:
        return False
    return bool(re.match(r"^[a-z0-9_]+$", key))


def _matches_event_filter(key: str, norm_target: str, norm_entity: str) -> bool:
    """Evalúa si una clave de evento cumple con los filtros activos."""
    if norm_entity and not (
        key == norm_entity or key.startswith(f"{norm_entity}_") or norm_entity in key
    ):
        return False
    if norm_target:
        if "*" in norm_target or "?" in norm_target:
            if not fnmatch.fnmatch(key, norm_target):
                return False
        elif norm_target not in key:
            return False
    return True


def _parse_tags(tags_raw: Any) -> list[str]:
    """Parsea el campo tags que puede ser lista, JSON o CSV."""
    if isinstance(tags_raw, list):
        return [str(t) for t in tags_raw]
    if not tags_raw:
        return []
    raw_str = str(tags_raw).strip()
    if raw_str.startswith("[") and raw_str.endswith("]"):
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, list):
                return [str(t) for t in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
    return [t.strip() for t in raw_str.split(",") if t.strip()]


def trace_event_flow(
    lancedb_path: Path,
    target_event: str = "",
    entity: str = "",
    limit: int = 15,
) -> str:
    """Rastrea la cadena de flujo de eventos de extremo a extremo en el código indexado.

    Cadena de trazabilidad:
    Definición -> Emisor Backend -> Handler/WS -> Reducer -> UI/Consumidor

    Args:
        lancedb_path: Ruta al directorio .lancedb.
        target_event: Filtro opcional por evento o comodín (ej. 'follower_*').
        entity: Filtro opcional por entidad o dominio (ej. 'user', 'chat').
        limit: Límite de eventos a mostrar en ejecuciones globales (por defecto 15).

    Returns:
        Reporte formateado en texto con el mapa de trazabilidad del flujo de eventos.
    """
    rows = get_indexed_metadata(
        [
            "source",
            "scope",
            "class_name",
            "method_name",
            "tags",
            "type",
            "text",
            "start_line",
            "end_line",
            "payload_schema",
        ]
    )

    if not rows:
        return (
            "NO_INDEX: The codebase has not been indexed yet. "
            "Run ingest_codebase first."
        )

    # Identificar todos los eventos y acciones
    events: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "canonical_name": "",
            "schema": "",
            "definitions": [],
            "emitters": [],
            "handlers": [],
            "reducers": [],
            "consumers": [],
        }
    )

    norm_target = (
        _normalize_event_name(target_event)
        if target_event and not any(c in target_event for c in "*?")
        else target_event.strip().lower()
    )
    norm_entity = entity.strip().lower()

    # Primera pasada: Registrar definiciones de eventos (clases *Event o en events.py)
    for row in rows:
        source: str = row.get("source", "").replace("\\", "/")
        class_name: str = (row.get("class_name") or "").strip()
        start_line: int = int(row.get("start_line") or 1)
        schema_raw: str = (row.get("payload_schema") or "").strip()

        norm_src = source.lower()
        is_event_file = "events.py" in norm_src or "/events/" in norm_src

        if class_name and (class_name.endswith("Event") or is_event_file):
            key = _normalize_event_name(class_name)
            if not _is_valid_event_key(key):
                continue
            if not _matches_event_filter(key, norm_target, norm_entity):
                continue
            events[key]["canonical_name"] = class_name
            if schema_raw and not events[key]["schema"]:
                events[key]["schema"] = schema_raw
            entry = f"{source}:{start_line} (class {class_name})"
            if entry not in events[key]["definitions"]:
                events[key]["definitions"].append(entry)

    # Segunda pasada: Registrar emisores, handlers, reducers y consumidores
    for row in rows:
        source: str = row.get("source", "").replace("\\", "/")
        class_name: str = (row.get("class_name") or "").strip()
        method_name: str = (row.get("method_name") or "").strip()
        chunk_type: str = (row.get("type") or "").strip()
        start_line: int = int(row.get("start_line") or 1)
        tags = _parse_tags(row.get("tags"))
        text: str = row.get("text", "")
        norm_src = source.lower()
        is_event_file = "events.py" in norm_src or "/events/" in norm_src

        detected_event_keys: set[str] = set()
        for tag in tags:
            if tag.startswith("event:") or tag.startswith("action:"):
                k = _normalize_event_name(tag)
                if _is_valid_event_key(k):
                    detected_event_keys.add(k)

        if chunk_type == "reducer_case" and ":" in method_name:
            case_name = method_name.split(":", 1)[1]
            k = _normalize_event_name(case_name)
            if _is_valid_event_key(k):
                detected_event_keys.add(k)

        for k, ev_data in events.items():
            c_name = ev_data["canonical_name"]
            if c_name and c_name in text:
                detected_event_keys.add(k)

        # Referencias de eventos en componentes y configuración UI
        for k in events:
            is_ui_file = (
                "component" in norm_src
                or "components" in norm_src
                or "static" in norm_src
                or "ui" in norm_src
                or "views" in norm_src
                or "config" in norm_src
                or "tab" in norm_src
                or "drawer" in norm_src
                or "modal" in norm_src
            )
            has_event_ref = (
                f"'{k}'" in text
                or f'"{k}"' in text
                or f"{k}:" in text
                or f"{k} =" in text
            )
            if (
                is_ui_file
                and has_event_ref
                and not is_event_file
                and "reducer" not in norm_src
            ):
                detected_event_keys.add(k)

        for key in detected_event_keys:
            if not _is_valid_event_key(key):
                continue
            if not _matches_event_filter(key, norm_target, norm_entity):
                continue

            if not events[key]["canonical_name"]:
                events[key]["canonical_name"] = key

            schema_raw: str = (row.get("payload_schema") or "").strip()
            if schema_raw and not events[key]["schema"]:
                events[key]["schema"] = schema_raw

            loc_label = (
                f"{method_name}"
                if method_name
                else (class_name if class_name else "block")
            )
            entry = f"{source}:{start_line} ({loc_label})"

            # Clasificar el rol en la cadena de flujo
            if chunk_type == "reducer_case" or (
                "reducer" in norm_src and "test" not in norm_src
            ):
                if entry not in events[key]["reducers"]:
                    events[key]["reducers"].append(entry)
            elif (
                "ws" in norm_src
                or "socket" in norm_src
                or "gateway" in norm_src
                or "handler" in norm_src
            ):
                if entry not in events[key]["handlers"]:
                    events[key]["handlers"].append(entry)
            elif (
                "component" in norm_src
                or "ui" in norm_src
                or "view" in norm_src
                or "static" in norm_src
                or "config" in norm_src
            ) and not is_event_file:
                if entry not in events[key]["consumers"]:
                    events[key]["consumers"].append(entry)
            elif not is_event_file and entry not in events[key]["definitions"]:
                # Es un emisor si proviene de actions/services o emite explícitamente
                has_emit_call = any(
                    kw in text
                    for kw in (
                        ".emit(",
                        "event_bus.emit",
                        ".publish(",
                        ".dispatch(",
                        "emit(",
                    )
                )
                is_emitter_src = (
                    "actions" in norm_src
                    or "services" in norm_src
                    or "consumers" in norm_src
                    or has_emit_call
                )
                if is_emitter_src and entry not in events[key]["emitters"]:
                    events[key]["emitters"].append(entry)

    # Filtrar eventos que no tengan ninguna ocurrencia relevante
    filtered_events = {
        k: v
        for k, v in events.items()
        if _is_valid_event_key(k)
        and (
            v["definitions"]
            or v["emitters"]
            or v["handlers"]
            or v["reducers"]
            or v["consumers"]
        )
    }

    if not filtered_events:
        filter_parts = []
        if target_event:
            filter_parts.append(f"event='{target_event}'")
        if entity:
            filter_parts.append(f"entity='{entity}'")
        filter_desc = " and ".join(filter_parts) if filter_parts else ""
        if filter_desc:
            return f"No event flow found matching {filter_desc} in indexed metadata."
        return "No events detected in the indexed codebase."

    total_detected = len(filtered_events)
    sorted_items = sorted(filtered_events.items())

    # Aplicar límite si es consulta global sin filtro específico
    is_truncated = False
    if not target_event and not entity and limit > 0 and total_detected > limit:
        sorted_items = sorted_items[:limit]
        is_truncated = True

    lines: list[str] = [
        f"[Event-Flow Map — {total_detected} Event(s) Detected"
        + (f" (showing top {limit})]" if is_truncated else "]")
        + "\n"
    ]

    for key, data in sorted_items:
        canonical = data["canonical_name"] or key
        lines.append(f"Event: {canonical} (event:{key})")

        if data.get("schema"):
            lines.append(f"  ├── Schema:      {{ {data['schema']} }}")

        if data["definitions"]:
            lines.append("  ├── Definition:  " + ", ".join(data["definitions"]))
        else:
            lines.append("  ├── Definition:  (not explicitly defined in events.py)")

        if data["emitters"]:
            lines.append("  ├── Emitter:     " + ", ".join(data["emitters"]))
        else:
            lines.append("  ├── Emitter:     (no emitter found)")

        if data["handlers"]:
            lines.append("  ├── WebSocket:   " + ", ".join(data["handlers"]))
        else:
            lines.append("  ├── WebSocket:   (no ws/handler found)")

        if data["reducers"]:
            lines.append("  ├── Reducer:     " + ", ".join(data["reducers"]))
        else:
            lines.append("  ├── Reducer:     (no reducer case found)")

        if data["consumers"]:
            lines.append("  └── UI/Consumer: " + ", ".join(data["consumers"]))
        else:
            lines.append("  └── UI/Consumer: (no UI component reference found)")

        lines.append("")

    if is_truncated:
        lines.append(
            f"... [{total_detected - limit} more events omitted. "
            "Use event_name or entity parameter to inspect specific events.]"
        )

    return "\n".join(lines).rstrip()
