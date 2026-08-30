import pytest

from rag_local.services.db import get_chroma_collection, query_db


def test_f5_query_scope_filtering_angular(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["chunk_fe", "chunk_be"],
        embeddings=[[0.1] * 896, [-0.1] * 896],
        documents=["frontend code here", "backend code here"],
        metadatas=[
            {"source": "fe.ts", "scope": "angular"},
            {"source": "be.ts", "scope": "nestjs"},
        ],
    )

    results = query_db("code", scope="angular", k=2)
    assert len(results["ids"][0]) == 1
    assert results["metadatas"][0][0]["scope"] == "angular"


def test_f5_query_scope_filtering_nestjs(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["chunk_fe", "chunk_be"],
        embeddings=[[0.1] * 896, [-0.1] * 896],
        documents=["frontend code here", "backend code here"],
        metadatas=[
            {"source": "fe.ts", "scope": "angular"},
            {"source": "be.ts", "scope": "nestjs"},
        ],
    )

    results = query_db("code", scope="nestjs", k=2)
    assert len(results["ids"][0]) == 1
    assert results["metadatas"][0][0]["scope"] == "nestjs"


def test_f5_query_no_scope_returns_all(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["chunk_fe", "chunk_be"],
        embeddings=[[0.1] * 896, [-0.1] * 896],
        documents=["frontend code here", "backend code here"],
        metadatas=[
            {"source": "fe.ts", "scope": "angular"},
            {"source": "be.ts", "scope": "nestjs"},
        ],
    )

    results = query_db("code", scope=None, k=2)
    assert len(results["ids"][0]) == 2


def test_f5_query_k_limit_respected(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["c1", "c2", "c3"],
        embeddings=[[0.1] * 896, [0.1] * 896, [0.1] * 896],
        documents=["c1", "c2", "c3"],
        metadatas=[{"source": "a.ts", "scope": "nestjs"}] * 3,
    )
    results = query_db("query", k=2)
    assert len(results["ids"][0]) == 2


def test_f5_query_with_no_chunks_in_db(setup_test_env):
    results = query_db("query", k=2)
    assert results["ids"] == [[]] or len(results["ids"][0]) == 0


def test_f5_boundary_query_extremely_long_text(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["c1"],
        embeddings=[[0.1] * 896],
        documents=["sample"],
        metadatas=[{"source": "a.ts", "scope": "nestjs"}],
    )
    long_query = "query " * 2000
    res = query_db(long_query, k=1)
    assert len(res["ids"][0]) == 1


def test_f5_boundary_query_empty_string_error(setup_test_env):
    with pytest.raises(ValueError):
        query_db("", k=1)


def test_f5_boundary_query_special_characters_search(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["c1"],
        embeddings=[[0.1] * 896],
        documents=["sample"],
        metadatas=[{"source": "a.ts", "scope": "nestjs"}],
    )
    res = query_db("!@#$%^&*()_+{}|:<>?`-=[]\\;',./", k=1)
    assert len(res["ids"][0]) == 1


def test_f5_boundary_query_non_existent_scope_filter(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["c1"],
        embeddings=[[0.1] * 896],
        documents=["sample"],
        metadatas=[{"source": "a.ts", "scope": "nestjs"}],
    )
    res = query_db("sample", scope="non-existent", k=1)
    assert len(res["ids"][0]) == 0


def test_f5_boundary_query_k_value_out_of_bounds(setup_test_env):
    collection = get_chroma_collection()
    collection.add(
        ids=["c1"],
        embeddings=[[0.1] * 896],
        documents=["sample"],
        metadatas=[{"source": "a.ts", "scope": "nestjs"}],
    )
    with pytest.raises(ValueError):
        query_db("sample", k=-5)
