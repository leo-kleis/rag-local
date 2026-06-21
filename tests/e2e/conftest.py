import importlib
import shutil
import pytest


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    rag_root = tmp_path / "rag_root"
    repo_root = tmp_path / "repo_root"
    lancedb_path = rag_root / ".lancedb"

    rag_root.mkdir(parents=True, exist_ok=True)
    repo_root.mkdir(parents=True, exist_ok=True)
    lancedb_path.mkdir(parents=True, exist_ok=True)

    # Crear subcarpetas de escaneo por defecto para que scan_files no se queje
    scan_dirs = ["frontend", "backend"]
    for sdir in scan_dirs:
        (repo_root / sdir).mkdir(parents=True, exist_ok=True)

    # Crear archivos firma para la detección de Angular y NestJS en los tests
    (repo_root / "frontend" / "angular.json").write_text("{}", encoding="utf-8")
    (repo_root / "backend" / "nest-cli.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("RAG_ROOT", str(rag_root))
    monkeypatch.setenv("RAG_REPO_ROOT", str(repo_root))
    monkeypatch.setenv("RAG_LANCEDB_PATH", str(lancedb_path))
    monkeypatch.setenv("RAG_MOCK_API", "1")

    # Forzar recarga de los módulos para que usen las nuevas variables de entorno
    import rag_local.core.config
    import rag_local.services.scanner
    import rag_local.services.db
    import rag_local.services.embeddings
    import rag_local.services.gemini
    import rag_local.services.rag
    import rag_local.cli.ingest

    importlib.reload(rag_local.core.config)
    importlib.reload(rag_local.services.scanner)
    importlib.reload(rag_local.services.db)
    importlib.reload(rag_local.services.embeddings)
    importlib.reload(rag_local.services.gemini)
    importlib.reload(rag_local.services.rag)
    importlib.reload(rag_local.cli.ingest)

    yield {
        "rag_root": rag_root,
        "repo_root": repo_root,
        "chroma_path": lancedb_path,
    }
