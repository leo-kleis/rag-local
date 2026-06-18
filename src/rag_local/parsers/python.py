import re

from rag_local.core.models import Chunk, ChunkMetadata


def chunk_python(lines: list[str]) -> list[Chunk]:
    """Divide un archivo de código de Python (.py) en bloques (clases y funciones)."""
    chunks: list[Chunk] = []

    # 1. Extraer todos los imports del archivo para asociarlos
    # a los metadatos de cada chunk
    import_re = re.compile(
        r"^\s*(?:import\s+[\w\s,]+|from\s+[\w\.]+\s+import\s+[\w\s,\*\(\)]+)"
    )
    global_imports = []
    for line in lines:
        stripped = line.strip()
        if import_re.match(stripped):
            # Limpiar y guardar importaciones
            global_imports.append(stripped)

    # 2. Identificar y agrupar bloques lógicos basados en
    # firmas de nivel superior (class y def)
    class_def_re = re.compile(r"^(class|def)\s+(\w+)")
    method_def_re = re.compile(r"^\s{4}def\s+(\w+)")

    current_class = ""
    current_method = ""
    current_type = ""
    block_lines: list[tuple[int, str]] = []

    def save_chunk(
        blines: list[tuple[int, str]],
        c_class: str,
        c_method: str,
        c_type: str,
    ) -> None:
        if not blines:
            return
        text = "".join(line_text for _, line_text in blines)
        if not text.strip():
            return

        start_line = blines[0][0]
        end_line = blines[-1][0]

        # Extraer dependencias sencillas
        # (palabras que parezcan llamadas a otras clases/funciones)
        dependencies_set = set()
        words = re.findall(r"\b[A-Za-z_]\w*\b", text)
        for w in words:
            if w not in (c_class, c_method) and len(w) > 3:
                dependencies_set.add(w)

        # Crear y añadir el Chunk
        chunks.append(
            Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                metadata=ChunkMetadata(
                    class_name=c_class,
                    method_name=c_method,
                    type=c_type,
                    imports=global_imports,
                    dependencies=sorted(dependencies_set),
                ),
            )
        )

    for idx, line in enumerate(lines):
        line_num = idx + 1

        # Comprobar si comienza una nueva definición de nivel 0
        # (Clase o Función Global)
        class_def_match = class_def_re.match(line)
        method_def_match = method_def_re.match(line)

        if class_def_match:
            # Guardar bloque anterior si existe
            save_chunk(block_lines, current_class, current_method, current_type)

            # Inicializar nuevo bloque de Clase
            current_type = class_def_match.group(1)
            name = class_def_match.group(2)
            if current_type == "class":
                current_class = name
                current_method = ""
            else:
                current_class = ""
                current_method = name

            block_lines = [(line_num, line)]

        elif method_def_match and current_class:
            # Si estamos dentro de una clase y encontramos un método
            # (indentación de 4 espacios)
            save_chunk(block_lines, current_class, current_method, current_type)

            current_method = method_def_match.group(1)
            current_type = "method"
            block_lines = [(line_num, line)]

        else:
            block_lines.append((line_num, line))

    # Guardar el último fragmento pendiente
    save_chunk(block_lines, current_class, current_method, current_type)

    return chunks
