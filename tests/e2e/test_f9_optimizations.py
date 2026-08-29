import pytest
from rag_local.services.db import get_chroma_collection, query_db, delete_file_chunks


def test_scalar_indices_created(setup_test_env):
    """Verifica que se creen correctamente los índices escalares en 'scope' y 'source'."""
    collection = get_chroma_collection()

    # Obtener los índices de la tabla de LanceDB
    indices = collection.table.list_indices()
    indexed_columns = {idx.columns[0] for idx in indices if idx.columns}

    # Comprobar que 'scope' y 'source' están indexados
    assert "scope" in indexed_columns
    assert "source" in indexed_columns


def test_sql_injection_and_quotes_sanitization(setup_test_env):
    """Prueba que el filtrado SQL escape correctamente las comillas simples en 'scope' y 'source'."""
    collection = get_chroma_collection()

    # Insertar registros con comillas en metadatos para simular casos adversos
    collection.add(
        ids=["chunk'1", "chunk'2"],
        embeddings=[[0.1] * 1024, [-0.1] * 1024],
        documents=["doc 1", "doc 2"],
        metadatas=[
            {"source": "test'file.ts", "scope": "scope'1"},
            {"source": "normal.ts", "scope": "normal_scope"},
        ],
    )

    # 1. Probar query_db con scope con comillas simples (no debe lanzar error de sintaxis SQL)
    results = query_db("doc", scope="scope'1", k=2)
    assert len(results["ids"][0]) == 1
    assert results["metadatas"][0][0]["source"] == "test'file.ts"

    # 2. Probar delete_file_chunks con nombre de archivo con comillas simples (no debe fallar)
    delete_file_chunks(collection, "test'file.ts")

    # Verificar que el chunk con comilla simple fue eliminado con éxito
    remaining = collection.get(where={"source": "test'file.ts"})
    assert len(remaining["ids"]) == 0

    # Verificar que el otro chunk normal no se vio afectado
    remaining_normal = collection.get(where={"source": "normal.ts"})
    assert len(remaining_normal["ids"]) == 1
