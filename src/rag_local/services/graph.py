# ruff: noqa: E501
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lancedb

from rag_local.core.logging import logger


def generate_html_graph(lancedb_path: Path, base_output_path: Path) -> None:
    """Genera tres archivos HTML independientes para visualización 3D, 2D y Mermaid

    cargando las plantillas desde el disco y realizando reemplazos de placeholders.
    """
    db = lancedb.connect(str(lancedb_path))
    table_names = list(db.table_names())

    if "code_relationships" not in table_names:
        raise ValueError(
            "La tabla de relaciones no existe. Ejecuta ingest_codebase primero."
        )

    table_rel = db.open_table("code_relationships")
    records: list[dict[str, Any]] = table_rel.search().limit(10000).to_list()

    if not records:
        raise ValueError(
            "No hay relaciones registradas en la base de datos."
        )

    # Identificar todos los nodos y enlaces únicos
    nodes_map: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    # Determinar grados para escalar el tamaño de los nodos
    degrees: dict[str, int] = {}
    for r in records:
        src = r["source_file"]
        tgt = r["target_symbol"]
        degrees[src] = degrees.get(src, 0) + 1
        degrees[tgt] = degrees.get(tgt, 0) + 1

    # Definir de forma precisa qué es un archivo y qué es un símbolo
    def get_node_group(name: str) -> str:
        valid_exts = (".py", ".ts", ".js", ".prisma", ".html", ".css", ".json")
        # Debe terminar en extensión de código y no contener espacios
        if any(name.endswith(ext) for ext in valid_exts) and " " not in name:
            return "File"
        return "Symbol"

    # Determinar colores por extensión o rol
    def get_node_color(name: str) -> str:
        if name.endswith(".ts") or name.endswith(".js"):
            return "#38bdf8"  # angular/nestjs file (sky blue)
        if name.endswith(".prisma"):
            return "#34d399"  # prisma (emerald)
        if name.endswith(".py"):
            return "#a78bfa"  # python (purple)
        return "#fbbf24"  # symbols/classes (amber)

    for r in records:
        src = r["source_file"]
        tgt = r["target_symbol"]
        rel_type = r["relationship_type"]

        # Registrar source node
        if src not in nodes_map:
            nodes_map[src] = {
                "id": src,
                "label": src,
                "color": get_node_color(src),
                "group": get_node_group(src),
                "size": 15 + min(degrees.get(src, 1) * 3, 30),
            }

        # Registrar target node
        if tgt not in nodes_map:
            nodes_map[tgt] = {
                "id": tgt,
                "label": tgt,
                "color": get_node_color(tgt),
                "group": get_node_group(tgt),
                "size": 15 + min(degrees.get(tgt, 1) * 3, 30),
            }

        # Enlace original para 3D
        links.append(
            {
                "source": src,
                "target": tgt,
                "label": rel_type,
                "color": "#475569",
            }
        )

    # ────────────────────────────────────────────────────────────────
    # RESOLUCIÓN DE IMPORTACIONES DE ARCHIVOS EN PYTHON
    # ────────────────────────────────────────────────────────────────
    project_files = {name for name in nodes_map if get_node_group(name) == "File"}
    
    # Construir mapa de nombres de módulos importables a rutas reales de archivos
    module_to_file: dict[str, str] = {}
    for filepath in project_files:
        normalized = filepath.replace("\\", "/").lower()
        base_name = normalized.rsplit(".", 1)[0]
        
        # Mapear sin prefijo "src/"
        if base_name.startswith("src/"):
            base_name_no_src = base_name[4:]
            module_to_file[base_name_no_src] = filepath
            module_to_file[base_name_no_src.replace("/", ".")] = filepath
            
        module_to_file[base_name] = filepath
        module_to_file[base_name.replace("/", ".")] = filepath
        
        # Mapear solo el nombre del archivo (por si importan por alias o relativo simple)
        file_only = Path(normalized).name.rsplit(".", 1)[0]
        if len(file_only) > 3:
            module_to_file[file_only] = filepath

    # Ordenar módulos por longitud descendente para emparejar los más específicos primero
    sorted_modules = sorted(module_to_file.keys(), key=len, reverse=True)

    # Construir enlaces directos de archivo a archivo
    file_to_file_links: list[dict[str, Any]] = []
    seen_file_links: set[tuple[str, str, str]] = set()

    for r in records:
        src = r["source_file"]
        tgt = r["target_symbol"]
        rel_type = r["relationship_type"]

        if get_node_group(src) == "File":
            # Si el destino es un Symbol (como un import string), buscar si menciona un archivo del proyecto
            if get_node_group(tgt) == "Symbol":
                tgt_norm = tgt.replace("\\", "/").lower()
                resolved = None
                for mod in sorted_modules:
                    if len(mod) > 4 and mod in tgt_norm:
                        resolved = module_to_file[mod]
                        break
                
                if resolved and resolved != src:
                    link_key = (src, resolved, "imports")
                    if link_key not in seen_file_links:
                        seen_file_links.add(link_key)
                        file_to_file_links.append({
                            "source": src,
                            "target": resolved,
                            "label": "imports",
                            "color": "#6366f1"
                        })
            # Si el destino ya es directamente un archivo del proyecto
            elif get_node_group(tgt) == "File" and src != tgt:
                link_key = (src, tgt, rel_type)
                if link_key not in seen_file_links:
                    seen_file_links.add(link_key)
                    file_to_file_links.append({
                        "source": src,
                        "target": tgt,
                        "label": rel_type,
                        "color": "#6366f1"
                    })

    # ────────────────────────────────────────────────────────────────
    # PREPARAR DATOS EN ID LIMPIAS (n0, n1...) PARA CYTOSCAPE Y MERMAID
    # ────────────────────────────────────────────────────────────────
    unique_nodes = sorted(nodes_map.keys())
    node_id_map = {name: f"n{i}" for i, name in enumerate(unique_nodes)}

    # 1. Mermaid
    mermaid_lines: list[str] = ["graph TD"]
    for r in records:
        src = r["source_file"]
        tgt = r["target_symbol"]
        rel_type = r["relationship_type"]
        m_src = node_id_map[src]
        m_tgt = node_id_map[tgt]
        esc_src = src.replace('"', '\\"')
        esc_tgt = tgt.replace('"', '\\"')
        mermaid_lines.append(f'    {m_src}["{esc_src}"] -->|{rel_type}| {m_tgt}["{esc_tgt}"]')
    mermaid_code = "\n".join(mermaid_lines)

    # 2. Cytoscape Modo Completo (Símbolos + Archivos)
    cy_elements: list[dict[str, Any]] = []
    for node_id, node_info in nodes_map.items():
        short_label = node_id.replace("\\", "/").split("/")[-1]
        cy_elements.append({
            "group": "nodes",
            "data": {
                "id": node_id_map[node_id],
                "shortLabel": short_label,
                "fullName": node_id,
                "color": node_info["color"],
                "size": node_info["size"],
                "group": node_info["group"]
            }
        })
    for i, link in enumerate(links):
        cy_elements.append({
            "group": "edges",
            "data": {
                "id": f"e{i}",
                "source": node_id_map[link["source"]],
                "target": node_id_map[link["target"]],
                "label": link["label"]
            }
        })

    # 3. Cytoscape Modo Simplificado (Solo Archivos)
    cy_only_files: list[dict[str, Any]] = []
    for filepath in project_files:
        node_info = nodes_map[filepath]
        short_label = filepath.replace("\\", "/").split("/")[-1]
        cy_only_files.append({
            "group": "nodes",
            "data": {
                "id": node_id_map[filepath],
                "shortLabel": short_label,
                "fullName": filepath,
                "color": node_info["color"],
                "size": node_info["size"],
                "group": "File"
            }
        })
    for i, link in enumerate(file_to_file_links):
        cy_only_files.append({
            "group": "edges",
            "data": {
                "id": f"fe{i}",
                "source": node_id_map[link["source"]],
                "target": node_id_map[link["target"]],
                "label": link["label"]
            }
        })

    # Inyecciones JSON
    graph_data_json = json.dumps({"nodes": list(nodes_map.values()), "links": links})
    cy_elements_json = json.dumps(cy_elements)
    cy_only_files_json = json.dumps(cy_only_files)

    # Cargar las plantillas desde el disco
    templates_dir = Path(__file__).parent.parent / "templates"
    
    template_3d_path = templates_dir / "graph_3d.html"
    template_2d_path = templates_dir / "graph_2d.html"
    template_mermaid_path = templates_dir / "graph_mermaid.html"

    if not template_3d_path.exists() or not template_2d_path.exists() or not template_mermaid_path.exists():
        raise FileNotFoundError(
            f"No se encontraron los archivos de plantilla HTML en: {templates_dir}"
        )

    html_3d = template_3d_path.read_text(encoding="utf-8").replace("__GRAPH_DATA__", graph_data_json)
    
    # Inyectar tanto la versión simplificada como la versión completa
    html_2d = template_2d_path.read_text(encoding="utf-8")\
        .replace("__GRAPH_ELEMENTS__", cy_elements_json)\
        .replace("__ONLY_FILES_ELEMENTS__", cy_only_files_json)
    
    # Escapar barras diagonales inversas del Mermaid en JavaScript
    escaped_mermaid = mermaid_code.replace("\\", "\\\\").replace("\n", "\\n")
    html_mermaid = template_mermaid_path.read_text(encoding="utf-8").replace("__MERMAID_CODE__", escaped_mermaid)

    # Escribir los 3 archivos de salida en .lancedb/
    dir_path = base_output_path.parent
    dir_path.mkdir(parents=True, exist_ok=True)

    file_3d = dir_path / "project_graph_3d.html"
    file_2d = dir_path / "project_graph_2d.html"
    file_mermaid = dir_path / "project_graph_mermaid.html"

    file_3d.write_text(html_3d, encoding="utf-8")
    file_2d.write_text(html_2d, encoding="utf-8")
    file_mermaid.write_text(html_mermaid, encoding="utf-8")

    logger.info(f"Visualizaciones exportadas por separado en: {dir_path.resolve()}")
