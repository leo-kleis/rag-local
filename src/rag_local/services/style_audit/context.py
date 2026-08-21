import json
import re
from dataclasses import dataclass, field
from typing import Any

from rag_local.core.logging import logger
from rag_local.services.style_audit.models import CSS_EXTENSIONS, STATE_MODIFIERS


@dataclass
class AuditContext:
    """Estructura contenedora de metadatos del proyecto para auditoría de estilos."""

    parsed_css_by_file: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    parsed_inline_rules_by_file: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict
    )
    component_parent_map: dict[str, set[str]] = field(default_factory=dict)
    class_dynamic_contexts: dict[str, list[tuple[str, bool, bool]]] = field(
        default_factory=dict
    )
    class_own_tags: dict[str, set[str]] = field(default_factory=dict)
    project_mitigated_classes: set[str] = field(default_factory=set)
    flex_wrap_classes: set[str] = field(default_factory=set)
    portal_mounted_classes: set[str] = field(default_factory=set)
    stacking_context_classes: dict[str, dict[str, Any]] = field(default_factory=dict)
    css_variables: dict[str, dict[str, Any]] = field(default_factory=dict)
    project_media_queries: list[dict[str, Any]] = field(default_factory=list)

    def resolve_css_value(self, val: str, max_depth: int = 5) -> str:
        """Resuelve recursivamente referencias var() a su valor computado."""
        if not val or "var(" not in val or max_depth <= 0:
            return val

        def _replace_var(match: re.Match[str]) -> str:
            inner = match.group(1).strip()
            parts = [p.strip() for p in inner.split(",", 1)]
            var_name = parts[0]
            fallback = parts[1] if len(parts) > 1 else ""
            if var_name in self.css_variables:
                resolved = self.css_variables[var_name].get("value", "")
                if resolved:
                    return self.resolve_css_value(resolved, max_depth - 1)
            if fallback:
                return self.resolve_css_value(fallback, max_depth - 1)
            return match.group(0)

        return re.sub(r"var\(\s*([^()]+)\s*\)", _replace_var, val)


def build_audit_context(rows: list[dict[str, Any]]) -> AuditContext:
    """Construye el contexto de auditoría normalizando los metadatos de LanceDB."""
    ctx = AuditContext()

    file_class_parents: dict[str, dict[str, Any]] = {}
    component_instantiations: dict[str, set[str]] = {}

    for r in rows:
        src = str(r.get("source", ""))
        if src.endswith(CSS_EXTENSIONS) and src not in ctx.parsed_css_by_file:
            raw_rules = r.get("css_rules", "")
            if raw_rules:
                try:
                    ctx.parsed_css_by_file[src] = json.loads(raw_rules)
                except Exception as ex:
                    logger.debug(f"Error deserializando css_rules de {src}: {ex}")

        # Extraer mapa de ancestros y señales de contexto dinámico
        raw_parents = r.get("class_parents", "")
        if raw_parents:
            try:
                p_map = json.loads(raw_parents)
                if isinstance(p_map, dict):
                    # Extraer reglas inline si existen
                    inline_rules = p_map.get("__inline_rules__")
                    if isinstance(inline_rules, list):
                        if src not in ctx.parsed_inline_rules_by_file:
                            ctx.parsed_inline_rules_by_file[src] = []
                        ctx.parsed_inline_rules_by_file[src].extend(inline_rules)

                    # Extraer clases montadas en portales DOM
                    portal_classes = p_map.get("__portal_classes__")
                    if isinstance(portal_classes, list):
                        ctx.portal_mounted_classes.update(portal_classes)

                    file_class_parents[src] = p_map

                    for child, data in p_map.items():
                        if child.startswith("__"):
                            continue

                        # Registrar instanciaciones de componentes
                        if child.startswith("[COMP]"):
                            if child not in component_instantiations:
                                component_instantiations[child] = set()
                            if isinstance(data, dict):
                                component_instantiations[child].update(
                                    data.get("parents", [])
                                )
                            elif isinstance(data, list):
                                component_instantiations[child].update(data)

                        if child not in ctx.class_dynamic_contexts:
                            ctx.class_dynamic_contexts[child] = []

                        # Deserialización dual: list (legacy) vs dict (enriquecido)
                        if isinstance(data, dict):
                            has_dyn = bool(data.get("has_dynamic_text", False))
                            is_col = bool(data.get("is_collection", False))
                            ctx.class_dynamic_contexts[child].append(
                                (src, has_dyn, is_col)
                            )
                            own_tags_raw = data.get("own_tags", [])
                            if own_tags_raw:
                                if child not in ctx.class_own_tags:
                                    ctx.class_own_tags[child] = set()
                                ctx.class_own_tags[child].update(own_tags_raw)
                        elif isinstance(data, list):
                            ctx.class_dynamic_contexts[child].append(
                                (src, False, False)
                            )
            except Exception as ex:
                logger.debug(f"Error deserializando class_parents de {src}: {ex}")

    # Clausura transitiva EXCLUSIVAMENTE a través de nodos de componentes
    changed = True
    passes = 0
    max_passes = 10
    while changed and passes < max_passes:
        changed = False
        passes += 1
        for comp, parents in list(component_instantiations.items()):
            new_parents = set(parents)
            for p in list(parents):
                if p.startswith("[COMP]") and p in component_instantiations:
                    ancestor_parents = component_instantiations[p]
                    before_len = len(new_parents)
                    new_parents.update(ancestor_parents)
                    if len(new_parents) > before_len:
                        changed = True
            component_instantiations[comp] = new_parents

    # Construir component_parent_map final expandiendo los ancestros locales por archivo
    for p_map in file_class_parents.values():
        for cls_name, data in p_map.items():
            if cls_name.startswith("__"):
                continue
            if cls_name not in ctx.component_parent_map:
                ctx.component_parent_map[cls_name] = set()

            local_p = set(data.get("parents", []) if isinstance(data, dict) else data)
            full_p = set()
            for lp in local_p:
                full_p.add(lp)
                if lp.startswith("[COMP]") and lp in component_instantiations:
                    full_p.update(component_instantiations[lp])

            ctx.component_parent_map[cls_name].update(full_p)

    # Identificar mitigaciones, triggers de apilamiento, variables CSS y media queries
    for src, rules in ctx.parsed_css_by_file.items():
        for rule in rules:
            props = rule.get("properties", {})
            rule_classes = rule.get("classes", [])
            selector = rule.get("selector", "")
            start_line = rule.get("start_line", 1)

            # 1. Mitigación de overflow y flex-wrap activo
            overflow = props.get("overflow", "").lower()
            overflow_x = props.get("overflow-x", "").lower()
            overflow_y = props.get("overflow-y", "").lower()
            if any(
                kw in overflow or kw in overflow_x or kw in overflow_y
                for kw in ("hidden", "auto", "scroll")
            ):
                ctx.project_mitigated_classes.update(rule_classes)

            flex_wrap = props.get("flex-wrap", "").lower()
            if flex_wrap in ("wrap", "wrap-reverse"):
                ctx.flex_wrap_classes.update(rule_classes)

            # 2. Triggers de contexto de apilamiento (Stacking Context)
            trigger_found = None
            isolation = props.get("isolation", "").lower()
            backdrop_filter = props.get("backdrop-filter", "").lower()
            filter_prop = props.get("filter", "").lower()
            transform_prop = props.get("transform", "").lower()
            contain_prop = props.get("contain", "").lower()
            perspective_prop = props.get("perspective", "").lower()
            container_type = props.get("container-type", "").lower()

            if isolation == "isolate":
                trigger_found = "isolation: isolate"
            elif backdrop_filter and backdrop_filter != "none":
                trigger_found = f"backdrop-filter: {props.get('backdrop-filter')}"
            elif filter_prop and filter_prop != "none" and "url(" not in filter_prop:
                trigger_found = f"filter: {props.get('filter')}"
            elif transform_prop and transform_prop != "none":
                trigger_found = f"transform: {props.get('transform')}"
            elif any(
                k in contain_prop for k in ("paint", "layout", "strict", "content")
            ):
                trigger_found = f"contain: {props.get('contain')}"
            elif perspective_prop and perspective_prop != "none":
                trigger_found = f"perspective: {props.get('perspective')}"
            elif container_type in ("inline-size", "size"):
                trigger_found = f"container-type: {props.get('container-type')}"

            if trigger_found:
                for c in rule_classes:
                    if c.lower() in STATE_MODIFIERS:
                        continue
                    if c not in ctx.stacking_context_classes:
                        ctx.stacking_context_classes[c] = {
                            "trigger": trigger_found,
                            "file": src,
                            "selector": selector,
                            "line": start_line,
                        }

            # 3. Variables CSS
            for k, v in props.items():
                if k.startswith("--"):
                    ctx.css_variables[k] = {
                        "value": v.strip(),
                        "file": src,
                        "selector": selector,
                        "line": start_line,
                    }

            # 4. Media queries
            mq = rule.get("media_query", "")
            if mq:
                ctx.project_media_queries.append(
                    {
                        "query": mq,
                        "file": src,
                        "line": start_line,
                        "selector": selector,
                    }
                )

    return ctx
