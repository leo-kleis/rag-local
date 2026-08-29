from typing import Any

from rag_local.core.exceptions import IngestError
from rag_local.core.logging import logger
from rag_local.core.models import Chunk
from rag_local.services.db_connection import get_db_connection, get_table_names
from rag_local.services.db_wrapper import sanitize_sql_value


def delete_file_chunks(collection: Any, file_path_rel: str) -> None:
    """Borra los chunks en LanceDB cuya metadata 'source' sea igual a file_path_rel."""
    try:
        collection.delete(where={"source": file_path_rel})

        # Eliminar también las relaciones de código asociadas al archivo
        try:
            db = get_db_connection()
            if "code_relationships" in get_table_names(db):
                table_rel = db.open_table("code_relationships")
                sanitized = sanitize_sql_value(file_path_rel)
                table_rel.delete(f"source_file = '{sanitized}'")
        except Exception as e:
            logger.warning(
                f"No se pudieron eliminar relaciones para {file_path_rel}: {e}"
            )
    except Exception as e:
        logger.exception(f"Error al eliminar chunks obsoletos para {file_path_rel}")
        raise IngestError(
            f"Error al eliminar chunks obsoletos para {file_path_rel}: {e}"
        ) from e


def save_file_relationships(file_path_rel: str, chunks: list[Chunk]) -> None:
    """Extrae y guarda las relaciones de código de un archivo basado en sus chunks."""
    try:
        db = get_db_connection()
        if "code_relationships" not in get_table_names(db):
            from rag_local.services.db_schemas import CodeRelationship

            table_rel = db.create_table(
                "code_relationships", schema=CodeRelationship, exist_ok=True
            )
        else:
            table_rel = db.open_table("code_relationships")

        records = []
        seen = set()

        for chunk in chunks:
            # Procesar imports
            imports = chunk.metadata.imports or []
            if isinstance(imports, str):
                imports = [i.strip() for i in imports.split(",") if i.strip()]
            for imp in imports:
                rel_key = (file_path_rel, imp, "import")
                if rel_key not in seen:
                    seen.add(rel_key)
                    records.append(
                        {
                            "id": f"{file_path_rel}#{imp}#import",
                            "source_file": file_path_rel,
                            "target_symbol": imp,
                            "relationship_type": "import",
                        }
                    )

            # Procesar dependencies
            deps = chunk.metadata.dependencies or []
            if isinstance(deps, str):
                deps = [d.strip() for d in deps.split(",") if d.strip()]
            for dep in deps:
                rel_key = (file_path_rel, dep, "depends_on")
                if rel_key not in seen:
                    seen.add(rel_key)
                    records.append(
                        {
                            "id": f"{file_path_rel}#{dep}#depends_on",
                            "source_file": file_path_rel,
                            "target_symbol": dep,
                            "relationship_type": "depends_on",
                        }
                    )

        if records:
            sanitized = sanitize_sql_value(file_path_rel)
            table_rel.delete(f"source_file = '{sanitized}'")
            table_rel.add(records)

    except Exception as e:
        logger.warning(
            f"No se pudieron guardar las relaciones de código para {file_path_rel}: {e}"
        )
