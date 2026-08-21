import json
import re
from typing import Any


def parse_str_list(val: Any) -> list[str]:
    """Deserializa de forma uniforme valores a listas de strings.

    Soporta listas nativas, strings con formato JSON, y strings separados por comas.
    """
    if val is None:
        return []
    if isinstance(val, list):
        return [str(item).strip() for item in val if str(item).strip()]
    if isinstance(val, str):
        val_clean = val.strip()
        if not val_clean:
            return []
        if val_clean.startswith("[") and val_clean.endswith("]"):
            try:
                parsed = json.loads(val_clean)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except (json.JSONDecodeError, ValueError):
                pass
        return [item.strip() for item in val_clean.split(",") if item.strip()]
    return [str(val).strip()] if str(val).strip() else []


def parse_css_dimension_px(
    val: str | None,
    base_font_size: float = 16.0,
) -> float | None:
    """Parsea un valor CSS dimensional (px, rem, em) a píxeles numéricos."""
    if not val:
        return None
    match = re.search(r"^([\d.]+)\s*(px|rem|em)$", val.strip(), re.IGNORECASE)
    if not match:
        return None
    num_val = float(match.group(1))
    unit = match.group(2).lower()
    if unit in ("rem", "em"):
        return num_val * base_font_size
    return num_val
