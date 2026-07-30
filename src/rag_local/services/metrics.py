from pathlib import Path
from typing import Any

import lancedb

from rag_local.core import config
from rag_local.core.logging import logger
from rag_local.services.db import get_chroma_collection


def count_effective_code_lines(file_path: Path) -> tuple[int, int]:
    """Cuenta el número total de líneas y líneas efectivas de código

    (excluyendo líneas vacías y comentarios, incluyendo triple-quote Python).
    """
    if not file_path.is_file():
        return 0, 0
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        total_lines = len(lines)
        code_lines = 0
        in_multiline_comment = False
        in_triple_quote = False
        triple_char = ""
        is_python = file_path.suffix.lower() == ".py"
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Manejo de triple-quote en Python (docstrings)
            if is_python and not in_multiline_comment:
                for tq in ('"""', "'''"):
                    if tq in stripped:
                        if in_triple_quote and triple_char == tq:
                            in_triple_quote = False
                            triple_char = ""
                            break
                        elif not in_triple_quote:
                            # Chequear si abre y cierra en la misma línea
                            count = stripped.count(tq)
                            if count >= 2:
                                break  # Abre y cierra: es código inline
                            in_triple_quote = True
                            triple_char = tq
                            break
                if in_triple_quote:
                    continue

            if in_multiline_comment:
                if "*/" in stripped:
                    in_multiline_comment = False
                continue
            if stripped.startswith("/*"):
                if "*/" not in stripped:
                    in_multiline_comment = True
                continue
            if stripped.startswith("//") or stripped.startswith("#"):
                continue
            code_lines += 1
        return total_lines, code_lines
    except Exception as e:
        logger.warning(f"Error al contar líneas de {file_path}: {e}")
        return 0, 0


def get_code_metrics(
    repo_path: str | None = None, min_lines: int = 200
) -> dict[str, Any]:
    """Calcula métricas de volumen de líneas de código por archivo e identifica

    archivos que superen el umbral configurado (ej: 200 líneas).
    """
    try:
        wrapper = get_chroma_collection()
        table: lancedb.table.Table = wrapper.table
        rows: list[dict[str, Any]] = (
            table.search().select(["source", "end_line"]).limit(10000).to_list()
        )
    except Exception as e:
        logger.error(f"Error al consultar LanceDB en get_code_metrics: {e}")
        return {
            "status": "error",
            "message": f"No se pudo consultar la base de datos: {e}",
            "summary": {},
            "exceeding_files": [],
            "top_10_largest_files": [],
            "by_extension": {},
        }

    if not rows:
        return {
            "status": "empty",
            "message": "La base de datos está vacía.",
            "summary": {},
            "exceeding_files": [],
            "top_10_largest_files": [],
            "by_extension": {},
        }

    root_path = Path(repo_path).resolve() if repo_path else config.REPO_ROOT.resolve()

    file_stats: dict[str, dict[str, Any]] = {}

    for row in rows:
        source: str = str(row.get("source", ""))
        end_line: int = int(row.get("end_line", 0))

        if not source:
            continue

        if source not in file_stats:
            file_stats[source] = {
                "source": source,
                "max_end_line": end_line,
                "chunks_count": 1,
            }
        else:
            file_stats[source]["max_end_line"] = max(
                file_stats[source]["max_end_line"], end_line
            )
            file_stats[source]["chunks_count"] += 1

    processed_files: list[dict[str, Any]] = []
    by_extension: dict[str, dict[str, int]] = {}
    # Cache para evitar lecturas duplicadas del mismo archivo (M2)
    file_metrics_cache: dict[Path, tuple[int, int]] = {}

    for source, stats in file_stats.items():
        abs_path = root_path / source
        if abs_path not in file_metrics_cache:
            file_metrics_cache[abs_path] = count_effective_code_lines(abs_path)
        total_lines, code_lines = file_metrics_cache[abs_path]
        if total_lines == 0:
            total_lines = stats["max_end_line"]
            code_lines = total_lines

        suffix = Path(source).suffix.lower() or "unknown"
        if suffix not in by_extension:
            by_extension[suffix] = {"file_count": 0, "total_lines": 0}
        by_extension[suffix]["file_count"] += 1
        by_extension[suffix]["total_lines"] += total_lines

        # M4: Clasificar por code_lines (efectivas) en vez de total_lines
        risk_level = "OK"
        if code_lines >= 400:
            risk_level = "CRITICAL"
        elif code_lines >= min_lines:
            risk_level = "WARNING"

        item = {
            "source": source,
            "lines_total": total_lines,
            "lines_code": code_lines,
            "chunks_count": stats["chunks_count"],
            "risk_level": risk_level,
        }
        processed_files.append(item)

    processed_files.sort(key=lambda x: x["lines_total"], reverse=True)

    exceeding_files = [f for f in processed_files if f["lines_total"] >= min_lines]
    top_10 = processed_files[:10]

    return {
        "status": "success",
        "summary": {
            "total_files": len(processed_files),
            "files_exceeding_threshold": len(exceeding_files),
            "min_lines_threshold": min_lines,
            "total_lines_codebase": sum(f["lines_total"] for f in processed_files),
        },
        "exceeding_files": exceeding_files,
        "top_10_largest_files": top_10,
        "by_extension": by_extension,
    }


def format_code_metrics(data: dict[str, Any]) -> str:
    """Formatea las métricas de código en texto plano optimizado para el agente."""
    if data.get("status") != "success":
        return f"NO_DATA: {data.get('message', 'No code metrics available.')}"

    summary = data.get("summary", {})
    exceeding = data.get("exceeding_files", [])
    by_ext = data.get("by_extension", {})

    total_files = summary.get("total_files", 0)
    total_lines = summary.get("total_lines_codebase", 0)
    threshold = summary.get("min_lines_threshold", 200)

    lines = [
        f"[Codebase Metrics — {total_files} files, {total_lines:,} total lines]",
        f"Files >= {threshold} lines: {summary.get('files_exceeding_threshold', 0)}",
    ]

    lines.append(f"\n[Refactoring Targets — Files >= {threshold} lines]")
    if not exceeding:
        lines.append("  (no files exceeding threshold)")
    else:
        for f in exceeding:
            risk = f["risk_level"]
            src = f["source"]
            tot = f["lines_total"]
            cod = f["lines_code"]
            chk = f["chunks_count"]
            lines.append(f"  {risk}: {src} ({tot} lines | {cod} code | {chk} chunks)")

    lines.append("\n[Language Distribution]")
    for ext, stats in sorted(
        by_ext.items(), key=lambda x: x[1]["total_lines"], reverse=True
    ):
        lines.append(
            f"  {ext}: {stats['file_count']} file(s) ({stats['total_lines']:,} lines)"
        )

    return "\n".join(lines)
