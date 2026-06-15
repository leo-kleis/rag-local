import os
from pathlib import Path

from dotenv import load_dotenv

# Rutas del sistema
CORE_DIR: Path = Path(__file__).resolve().parent
RAG_ROOT: Path = CORE_DIR.parent.parent.parent
REPO_ROOT: Path = RAG_ROOT.parent

# Cargar variables de entorno desde .env en la raíz de rag-local
ENV_PATH: Path = RAG_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Clave API de Gemini
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")

# Ruta de base de datos de ChromaDB
CHROMA_PATH: Path = RAG_ROOT / ".chromadb"

# Configuración de reintentos y rate limits
MAX_RETRIES: int = 5
INITIAL_BACKOFF: float = 2.0

# Configuración de ingestión
MAX_LINES_PER_CHUNK: int = 50
OVERLAP_LINES: int = 10
BATCH_SIZE: int = 30

SCAN_DIRS: list[str] = ["frontend", "backend"]
IGNORE_DIRS: set[str] = {
    "node_modules",
    "dist",
    ".git",
    ".angular",
    ".next",
    "build",
    ".chromadb",
    "rag-local",
    ".agents",
    "scripts-test",
    "specs",
    "generated",
}
ALLOWED_EXTENSIONS: set[str] = {".ts", ".html", ".prisma"}
