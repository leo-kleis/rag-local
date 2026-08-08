from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolver la raíz del RAG y cargar el archivo .env central de forma explícita
RAG_ROOT_DIR = Path(__file__).resolve().parents[3]
ENV_FILE_PATH = RAG_ROOT_DIR / ".env"
if ENV_FILE_PATH.is_file():
    load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)


class Settings(BaseSettings):
    # La raíz del paquete RAG se resuelve estáticamente
    RAG_ROOT: Path = Path(__file__).resolve().parents[3]

    # Propiedades configurables mediante env/dotenv
    RAG_REPO_ROOT: Path | None = None
    RAG_LANCEDB_PATH: Path | None = None
    GEMINI_API_KEY: str | None = None

    # Configuración de carga de pydantic-settings
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Instanciar el objeto de configuración
settings = Settings()

# Mantener compatibilidad absoluta de constantes a nivel de módulo
RAG_ROOT: Path = settings.RAG_ROOT
ENV_PATH: Path = RAG_ROOT / ".env"
REPO_ROOT: Path = settings.RAG_REPO_ROOT if settings.RAG_REPO_ROOT else RAG_ROOT
GEMINI_API_KEY: str | None = settings.GEMINI_API_KEY
LANCEDB_PATH: Path = (
    settings.RAG_LANCEDB_PATH if settings.RAG_LANCEDB_PATH else REPO_ROOT / ".lancedb"
)

# Configuración de reintentos y rate limits
MAX_RETRIES: int = 5
INITIAL_BACKOFF: float = 2.0

# Configuración de ingestión y versión de esquema
SCHEMA_VERSION: str = "1.0.0"
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
    ".vercel",
    ".turbo",
    "out",
    "coverage",
}
ALLOWED_EXTENSIONS: set[str] = {
    ".ts",
    ".js",
    ".tsx",
    ".jsx",
    ".html",
    ".prisma",
    ".py",
    ".css",
}

# Configuración de system instruction RAG
SYSTEM_INSTRUCTION_TECH_STACK: str = (
    "Python 3.12+, Angular 21, NestJS 11, Fastify, Prisma 7, and Next.js 16 (React 19)"
)

# Modelos y concurrencia
CONCURRENT_WORKERS: int = 4
LOCAL_EMBEDDING_MODEL: str = "Alibaba-NLP/gte-multilingual-base"
RERANKER_MODEL: str = "BAAI/bge-reranker-base"
GENERATION_MODEL: str = "gemini-2.5-flash"
GENERATION_FALLBACK_MODELS: list[str] = [
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
]

# Configuración de límites y seguridad
MAX_QUERY_LENGTH: int = 2000
MAX_FILE_SIZE_BYTES: int = 5242880  # 5MB

# Configuración de RAG
MAX_CONTEXT_CHARS: int = 120000
INITIAL_K_FOR_RERANK: int = 30
MAX_ENRICHED_CONTEXT_FILES: int = 15
MAX_ENRICHED_CHUNK_CHARS: int = 3000
COMPRESS_CODE_CONTEXT: bool = True

# Threshold de relevancia post-rerank
# El cross-encoder BAAI/bge-reranker-base produce logits en rango ~[-11, +11].
# Chunks con score inferior a este valor se descartan como claramente irrelevantes.
# -2.0 separa bien código ajeno al tema (~[-10, -3]) de código relacionado (~[-1, +10]).
MIN_RERANK_SCORE: float = -2.0

# Configuración del Worker Daemon (Precarga en VRAM / RAM)
DAEMON_IDLE_TIMEOUT: int = 1800  # 30 minutos de inactividad
DAEMON_GRACE_PERIOD: int = 15  # Segundos de gracia tras pérdida de PID padre
DAEMON_HEALTH_TIMEOUT: float = 2.0  # Timeout para healthcheck HTTP
DAEMON_REQUEST_TIMEOUT: float = 30.0  # Timeout por solicitud HTTP individual
DAEMON_PORT_FILE: str = "daemon.json"  # Nombre del archivo de estado en LANCEDB_PATH
DAEMON_WARMUP_PASSES: int = 3  # Pasadas dummy de warm-up
