import re


def parse_ts_imports(lines: list[str]) -> tuple[list[str], list[str], int]:
    """Extrae las declaraciones de importación de TypeScript al inicio."""
    import_lines: list[str] = []
    imports_list: list[str] = []
    next_line_idx = 0
    in_import = False
    current_import: list[str] = []

    import_patterns = [
        re.compile(r"import\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"from\s+['\"]([^'\"]+)['\"]"),
    ]

    for idx, line in enumerate(lines):
        stripped = line.strip()
        is_comment_or_empty = (
            stripped == ""
            or stripped.startswith("//")
            or stripped.startswith("/*")
            or stripped.startswith("*")
            or stripped.endswith("*/")
        )
        is_import_start = (
            stripped.startswith("import ")
            or stripped.startswith("import{")
            or stripped.startswith("import*")
        )

        if is_import_start:
            in_import = True
            current_import.append(line)
            import_lines.append(line)
            if ";" in stripped or "from" in stripped:
                for pattern in import_patterns:
                    m = pattern.search(stripped)
                    if m:
                        imports_list.append(m.group(1))
                in_import = False
                current_import = []
            next_line_idx = idx + 1
        elif in_import:
            current_import.append(line)
            import_lines.append(line)
            if (
                ";" in stripped
                or "from" in stripped
                or ("'" in stripped or '"' in stripped)
            ):
                full_import_str = "".join(current_import)
                for pattern in import_patterns:
                    m = pattern.search(full_import_str)
                    if m:
                        imports_list.append(m.group(1))
                in_import = False
                current_import = []
            next_line_idx = idx + 1
        elif is_comment_or_empty:
            import_lines.append(line)
            next_line_idx = idx + 1
        else:
            break

    while import_lines:
        last_stripped = import_lines[-1].strip()
        if (
            last_stripped == ""
            or last_stripped.startswith("//")
            or last_stripped.startswith("/*")
            or last_stripped.startswith("*")
            or last_stripped.endswith("*/")
        ):
            import_lines.pop()
            next_line_idx -= 1
        else:
            break

    if not imports_list:
        return [], [], 0

    return import_lines, imports_list[:100], next_line_idx


def get_class_dependencies(
    class_text: str, global_dependencies: list[str]
) -> list[str]:
    """Extrae las dependencias inyectadas y los imports locales en una clase."""
    deps: list[str] = []
    constructor_match = re.search(r"constructor\s*\(([^)]*)\)", class_text)
    if constructor_match:
        params_text = constructor_match.group(1)
        types = re.findall(r"\b\w+\s*:\s*([A-Z]\w*)", params_text)
        for t in types:
            if t not in deps:
                deps.append(t)

    prop_types = re.findall(r"\bprivate\s+readonly\s+\w+\s*:\s*([A-Z]\w*)", class_text)
    for pt in prop_types:
        if pt not in deps:
            deps.append(pt)

    for imp in global_dependencies:
        if imp not in deps:
            deps.append(imp)

    return sorted(deps)
