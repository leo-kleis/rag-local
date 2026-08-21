import re
from typing import Any

CSS_EXTENSIONS = (".css", ".scss", ".less", ".sass")

RESET_SELECTORS = {
    "*",
    "*::before",
    "*::after",
    "*:before",
    "*:after",
    "html",
    "body",
    ":root",
    ":before",
    ":after",
}

NATIVE_TEXT_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "blockquote",
    "article",
}

NON_TEXT_ELEMENTS = {
    "svg",
    "path",
    "circle",
    "rect",
    "line",
    "polygon",
    "canvas",
    "img",
    "video",
    "audio",
    "hr",
    "br",
    "i",
    "input",
    "textarea",
    "select",
    "button",
    "th",
    "td",
}

FIXED_CONTROL_KEYWORDS = {
    "toggle",
    "switch",
    "checkbox",
    "slider",
    "knob",
    "badge",
    "pill",
    "indicator",
    "dot",
    "avatar",
    "icon",
    "btn",
    "button",
}

INTRINSIC_INPUT_TAGS = {
    "input",
    "select",
    "textarea",
    "date",
    "dates",
    "datepicker",
    "filter",
    "filter-date",
    "filter-dates",
}

ATOMIC_UI_KEYWORDS = {
    "icon",
    "arrow",
    "spinner",
    "dot",
    "sep",
    "separator",
    "caret",
    "chevron",
    "indicator",
    "badge",
    "badges",
    "pill",
    "avatar",
    "close",
    "logo",
    "mark",
    "symbol",
    "btn",
    "button",
    "toggle",
    "switch",
    "checkbox",
    "radio",
    "tab",
    "pagination",
    "counter",
    "count",
    "stat",
    "status",
    "val",
    "price",
    "meta",
    "time",
    "date",
    "rpm",
    "label",
    "title",
    "header",
    "header-title",
    "header-meta",
    "alert",
    "alert-title",
    "alert-desc",
    "empty",
    "empty-icon",
    "live-dot",
    "stepper",
    "trigger",
    "select-arrow",
    "custom-select",
    "option",
    "preview",
    "preview-box",
    "subtext",
    "hint",
    "help",
    "progress",
    "author",
    "display",
    "username",
    "role",
    "banner",
    "th",
    "td",
    "input-area",
    "input-form",
    "input-bar",
    "input-group",
    "input-wrap",
    "input-container",
    "search-bar",
    "search-box",
    "search-form",
    "chat-input",
    "search-input",
    "toolbar",
}

STATE_MODIFIERS = {
    "open",
    "active",
    "show",
    "disabled",
    "selected",
    "hover",
    "focus",
    "loading",
    "hidden",
    "visible",
    "collapsed",
    "expanded",
    "closing",
    "entering",
    "leaving",
    "is-parted",
    "clickable",
    "checked",
    "valid",
    "invalid",
}

OVERLAY_CONTAINER_KEYWORDS = {
    "backdrop",
    "modal-backdrop",
    "dialog-backdrop",
    "toast-overlay",
    "toast-container",
    "toast-card",
    "history-backdrop",
    "drawer-backdrop",
    "popover",
    "conn-popover",
    "tooltip",
    "drawer",
    "overlay",
}

INTERNAL_OVERLAY_PARTS = {
    "dialog",
    "card",
    "body",
    "header",
    "footer",
    "title",
    "close",
    "icon",
    "text",
    "actions",
    "field",
    "label",
    "alert",
    "content",
    "item",
    "row",
    "meta",
    "dot",
    "status",
    "indicator",
    "badge",
    "badges",
    "pill",
    "spinner",
    "arrow",
    "sep",
    "separator",
    "button",
    "btn",
    "avatar",
    "trigger",
}

INPUT_TOOLBAR_KEYWORDS = {
    "input-area",
    "input-form",
    "input-bar",
    "input-group",
    "input-wrap",
    "input-container",
    "search-bar",
    "search-box",
    "search-form",
    "chat-input",
    "form-row",
    "search-input",
    "toolbar",
}

MENU_ITEM_KEYWORDS = {
    "menu-item",
    "context-menu",
    "dropdown-item",
    "select-item",
    "nav-item",
    "action-item",
    "list-option",
}

PROSE_TEXT_KEYWORDS = {
    "msg",
    "message",
    "comment",
    "desc",
    "description",
    "content",
    "text",
    "bio",
    "notes",
    "summary",
    "review",
    "article",
    "post",
    "body",
    "question",
    "answer",
    "response",
    "prompt",
    "feedback",
    "convo-q",
    "convo-a",
    "sys-text",
    "quote",
    "paragraph",
    "caption",
}

COLLECTION_CLASS_KEYWORDS = {
    "tags",
    "chips",
    "badges",
    "items",
    "list",
    "grid",
    "gallery",
    "actions-group",
    "sub-names",
    "breadcrumbs",
}

_RE_PSEUDO = re.compile(
    r":(hover|active|focus|disabled|visited|first-child|last-child|nth-child|placeholder)"
)
_RE_ICON_CLASS = re.compile(
    r"\b(?:fa[srbld]?|fa-[a-z0-9-]+|icon-[a-z0-9-]+|lucide|feather|tabler)\b"
)


def is_reset_selector(selector: str) -> bool:
    """Verifica si todos los tokens de un selector agrupado son resets CSS."""
    clean_sel = selector.lower()
    if any(p.strip().startswith("*") for p in clean_sel.split(",")):
        return True
    parts = {p.strip() for p in clean_sel.split(",") if p.strip()}
    return bool(parts) and parts.issubset(RESET_SELECTORS)


def is_pseudo_class(selector: str) -> bool:
    """Verifica si el selector contiene pseudo-clases de interacción o estado."""
    return bool(_RE_PSEUDO.search(selector.lower()))


def extract_terminal_tag(selector: str) -> str:
    """Extrae el tag terminal de un selector CSS."""
    clean_sel = selector.lower()
    terminal_part = re.split(r"[\s>+~]", clean_sel)[-1].strip()
    return terminal_part.split(".")[0].split("#")[0].split(":")[0].split("[")[0]


def is_atomic_or_micro_ui(selector: str, classes: list[str]) -> bool:
    """Detecta si un selector representa un componente atómico o micro-UI."""
    clean_sel = selector.lower()
    if _RE_ICON_CLASS.search(clean_sel):
        return True

    # Tokens individuales separados por guiones, puntos, clases y espacios
    tokens = set(re.findall(r"[a-z0-9]+", clean_sel))
    for c in classes:
        tokens.update(re.findall(r"[a-z0-9]+", c.lower()))

    return bool(tokens & ATOMIC_UI_KEYWORDS)


def is_modal_or_overlay(props: dict[str, Any]) -> bool:
    """Verifica si las propiedades CSS definen un overlay, modal o backdrop centrado."""
    pos = props.get("position", "").lower()
    inset = props.get("inset", "").lower()
    top = props.get("top", "").lower()
    left = props.get("left", "").lower()
    right = props.get("right", "").lower()
    bottom = props.get("bottom", "").lower()
    width = props.get("width", "").lower()
    height = props.get("height", "").lower()

    if pos in ("fixed", "absolute"):
        if inset in ("0", "0px"):
            return True
        if top in ("0", "0px") and left in ("0", "0px"):
            if width in ("100%", "100vw") and height in ("100%", "100vh"):
                return True
            if right in ("0", "0px") and bottom in ("0", "0px"):
                return True
    return False


def is_break_protected(props: dict[str, Any]) -> bool:
    """Verifica si la regla posee protección de rotura o truncamiento elíptico."""
    word_break = props.get("word-break", "").lower()
    overflow_wrap = props.get("overflow-wrap", "").lower()
    white_space = props.get("white-space", "").lower()
    text_overflow = props.get("text-overflow", "").lower()

    return (
        word_break in ("break-word", "break-all")
        or overflow_wrap in ("break-word", "anywhere")
        or "ellipsis" in text_overflow
        or "nowrap" in white_space
        or bool(props.get("-webkit-line-clamp", "") or props.get("line-clamp", ""))
    )
