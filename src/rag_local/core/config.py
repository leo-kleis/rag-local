import os
from pathlib import Path

from dotenv import load_dotenv

# Rutas del sistema
CORE_DIR: Path = Path(__file__).resolve().parent

# Buscar RAG_ROOT desde el entorno antes de cargar dotenv para saber de dónde cargarlo
_rag_root_env = os.getenv("RAG_ROOT")
RAG_ROOT: Path = Path(_rag_root_env) if _rag_root_env else CORE_DIR.parent.parent.parent

# Cargar variables de entorno desde .env en la raíz de rag-local
ENV_PATH: Path = RAG_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Re-evaluar variables de entorno después de cargar dotenv por si se redefinieron allí
_rag_root_env = os.getenv("RAG_ROOT")
RAG_ROOT = Path(_rag_root_env) if _rag_root_env else CORE_DIR.parent.parent.parent

_rag_repo_root_env = os.getenv("RAG_REPO_ROOT")
REPO_ROOT: Path = Path(_rag_repo_root_env) if _rag_repo_root_env else RAG_ROOT.parent

# Clave API de Gemini
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")

# Ruta de base de datos de LanceDB
_rag_lancedb_path_env = os.getenv("RAG_LANCEDB_PATH")
LANCEDB_PATH: Path = (
    Path(_rag_lancedb_path_env) if _rag_lancedb_path_env else RAG_ROOT / ".lancedb"
)

# Configuración de reintentos y rate limits
MAX_RETRIES: int = 5
INITIAL_BACKOFF: float = 2.0

# Configuración de ingestión
MAX_LINES_PER_CHUNK: int = 50
OVERLAP_LINES: int = 10
BATCH_SIZE: int = 30

IGNORE_DIRS: set[str] = {
    "node_modules",
    "dist",
    ".git",
    ".angular",
    ".next",
    "build",
    ".lancedb",
    "rag-local",
    ".agents",
    "scripts-test",
    "specs",
    "generated",
}
ALLOWED_EXTENSIONS: set[str] = {".ts", ".html", ".prisma"}

# Modelos y concurrencia
CONCURRENT_WORKERS: int = 4
EMBEDDING_MODEL: str = "gemini-embedding-2"
EMBEDDING_FALLBACK_MODEL: str = "text-embedding-004"
GENERATION_MODEL: str = "gemini-2.5-flash"

