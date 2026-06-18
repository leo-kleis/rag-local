import re

from rag_local.core.models import Chunk, ChunkMetadata


def chunk_prisma(lines: list[str]) -> list[Chunk]:
    """Divide un archivo de esquema Prisma en bloques (model, enum, etc.)."""
    chunks: list[Chunk] = []

    block_start_re = re.compile(r"^(model|enum|datasource|generator|type)\s+(\w+)\s*\{")
    prisma_primitives = {
        "String",
        "Int",
        "Boolean",
        "DateTime",
        "Json",
        "Decimal",
        "Float",
        "Bytes",
        "Unsupported",
    }

    pending_lines: list[tuple[int, str]] = []
    in_block = False
    block_type = ""
    block_name = ""
    block_lines: list[tuple[int, str]] = []

    for idx, line in enumerate(lines):
        line_num = idx + 1
        stripped = line.strip()

        if not in_block:
            match = block_start_re.match(stripped)
            if match:
                in_block = True
                block_type = match.group(1)
                block_name = match.group(2)
                block_lines = [*pending_lines, (line_num, line)]
                pending_lines = []
            else:
                pending_lines.append((line_num, line))
        else:
            block_lines.append((line_num, line))
            if stripped == "}" or stripped.endswith("}"):
                text = "".join(lc for _, lc in block_lines)
                start_line = block_lines[0][0]
                end_line = block_lines[-1][0]

                dependencies_set = set()
                for _, bline in block_lines:
                    words = re.findall(r"\b[A-Z]\w*\b", bline)
                    for w in words:
                        if w != block_name and w not in prisma_primitives:
                            dependencies_set.add(w)

                metadata = ChunkMetadata(
                    models=[block_name] if block_type in ("model", "enum") else [],
                    class_name=block_name,
                    type=block_type,
                    dependencies=sorted(dependencies_set),
                )
                chunks.append(
                    Chunk(
                        text=text,
                        start_line=start_line,
                        end_line=end_line,
                        metadata=metadata,
                    )
                )
                in_block = False
                block_type = ""
                block_name = ""
                block_lines = []

    if in_block and block_lines:
        text = "".join(lc for _, lc in block_lines)
        start_line = block_lines[0][0]
        end_line = block_lines[-1][0]
        dependencies_set = set()
        for _, bline in block_lines:
            words = re.findall(r"\b[A-Z]\w*\b", bline)
            for w in words:
                if w != block_name and w not in prisma_primitives:
                    dependencies_set.add(w)
        chunks.append(
            Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                metadata=ChunkMetadata(
                    models=[block_name] if block_type in ("model", "enum") else [],
                    class_name=block_name,
                    type=block_type,
                    dependencies=sorted(dependencies_set),
                ),
            )
        )

    if pending_lines:
        text = "".join(lc for _, lc in pending_lines)
        start_line = pending_lines[0][0]
        end_line = pending_lines[-1][0]
        chunks.append(
            Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                metadata=ChunkMetadata(
                    models=[],
                    class_name="",
                    type="",
                    dependencies=[],
                ),
            )
        )

    return chunks
