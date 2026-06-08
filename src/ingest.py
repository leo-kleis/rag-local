import os
import sys
import time
from pathlib import Path
from typing import Any, cast

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai.errors import APIError

# Obtener directorios del script y de configuración
SCRIPT_DIR = Path(__file__).resolve().parent
RAG_ROOT = SCRIPT_DIR.parent
REPO_ROOT = RAG_ROOT.parent

# Cargar variables de entorno desde el archivo .env ubicado en la raíz de rag-local
ENV_PATH = RAG_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Verificar que la clave API de Gemini esté presente
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY not found in environment variables or .env file.")
    print(f"Please create a .env file at '{ENV_PATH}' containing: GEMINI_API_KEY=your_key_here")
    sys.exit(1)

# Inicializar el cliente de Google GenAI
try:
    genai_client = genai.Client()
except Exception as e:
    print(f"Error initializing Google GenAI client: {e}")
    sys.exit(1)

# Parámetros de configuración
MAX_LINES_PER_CHUNK = 50
OVERLAP_LINES = 10
BATCH_SIZE = 30
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0  # seconds

# Rutas a escanear y directorios a ignorar
SCAN_DIRS = ["frontend", "backend"]
IGNORE_DIRS = {
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
}
ALLOWED_EXTENSIONS = {".ts", ".html"}


def get_relative_path(path: Path) -> str:
    """Devuelve la ruta relativa a la raíz del repositorio, usando barras diagonales."""
    try:
        rel_path = path.relative_to(REPO_ROOT)
        return str(rel_path).replace("\\", "/")
    except ValueError:
        return str(path)


def scan_files() -> list[Path]:
    """Escanea recursivamente el monorrefertorio (monorepo) en busca de

    archivos .ts y .html en las carpetas objetivo.
    """
    files_to_process = []
    for dir_name in SCAN_DIRS:
        target_dir = REPO_ROOT / dir_name
        if not target_dir.exists() or not target_dir.is_dir():
            print(f"Warning: Scan directory '{target_dir}' does not exist.")
            continue

        for path in target_dir.rglob("*"):
            if path.is_file() and path.suffix in ALLOWED_EXTENSIONS:
                # Verificar si el archivo está dentro de algún directorio ignorado
                parts = path.relative_to(REPO_ROOT).parts
                if not any(ignored in parts for ignored in IGNORE_DIRS):
                    files_to_process.append(path)
    return files_to_process


def chunk_file(file_path: Path) -> list[dict]:
    """Divide un archivo en fragmentos de líneas con solapamiento.

    Garantiza que las líneas se mantengan intactas.
    """
    chunks = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return []

    total_lines = len(lines)
    if total_lines == 0:
        return []

    # Si el archivo es más pequeño que MAX_LINES_PER_CHUNK, ponerlo todo en un solo fragmento
    if total_lines <= MAX_LINES_PER_CHUNK:
        text = "".join(lines)
        chunks.append({"text": text, "start_line": 1, "end_line": total_lines})
        return chunks

    start = 0
    while start < total_lines:
        end = min(start + MAX_LINES_PER_CHUNK, total_lines)
        chunk_lines = lines[start:end]
        text = "".join(chunk_lines)

        chunks.append({"text": text, "start_line": start + 1, "end_line": end})

        # Avanzar, teniendo en cuenta el solapamiento
        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES
        if start >= total_lines - OVERLAP_LINES:
            break

    return chunks


def get_embeddings_with_retry(texts: list[str]) -> list[list[float]] | None:
    """Obtiene incrustaciones (embeddings) de la API de Gemini con

    retroceso exponencial para límites de tarifa.
    """
    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            response = genai_client.models.embed_content(model="gemini-embedding-2", contents=texts)
            if response.embeddings is None:
                return None
            result_embeddings = []
            for emb in response.embeddings:
                if emb.values is not None:
                    result_embeddings.append(list(emb.values))
            return result_embeddings
        except APIError as e:
            if e.code == 429 or (e.code and e.code >= 500):
                print(
                    f"API Error ({e.code}) on attempt {attempt + 1}/{MAX_RETRIES}. "
                    f"Retrying in {backoff:.1f}s..."
                )
                time.sleep(backoff)
                backoff *= 2.0
            else:
                print(f"Non-retryable API Error: {e}")
                raise e
        except Exception as e:
            print(f"Unexpected error calling Gemini API: {e}")
            raise e
    print(f"Failed to generate embeddings after {MAX_RETRIES} attempts.")
    return None


def main():
    print("Starting monorepo ingestion process (Structured /src layout)...")
    print(f"Monorepo Root: {REPO_ROOT.resolve()}")
    print(f"RAG Root: {RAG_ROOT.resolve()}")

    # 1. Escanear archivos
    files = scan_files()
    print(f"Found {len(files)} source files to analyze.")

    # 2. Extraer fragmentos de todos los archivos
    all_chunks = []
    for file_path in files:
        rel_path = get_relative_path(file_path)
        scope = "frontend" if "frontend" in rel_path.split("/") else "backend"

        file_chunks = chunk_file(file_path)
        for chunk in file_chunks:
            chunk["source"] = rel_path
            chunk["scope"] = scope
            all_chunks.append(chunk)

    total_chunks = len(all_chunks)
    print(f"Split files into {total_chunks} chunks.")

    if total_chunks == 0:
        print("No content to index. Exiting.")
        return

    # 3. Inicializar el cliente local de ChromaDB (guardado en la raíz de rag-local/)
    chroma_path = RAG_ROOT / ".chromadb"
    print(f"Initializing local database at: {chroma_path}")
    chroma_client = chromadb.PersistentClient(path=str(chroma_path))

    collection = chroma_client.get_or_create_collection(name="monorepo_code")

    # 4. Generar incrustaciones (embeddings) e indexar en lotes
    print(f"Indexing {total_chunks} chunks in batches of {BATCH_SIZE}...")
    success_count = 0

    for i in range(0, total_chunks, BATCH_SIZE):
        batch = all_chunks[i : i + BATCH_SIZE]
        batch_texts = [c["text"] for c in batch]

        print(
            f"Processing batch {i // BATCH_SIZE + 1}/{(total_chunks - 1) // BATCH_SIZE + 1} "
            f"(chunks {i} to {min(i + BATCH_SIZE, total_chunks)})..."
        )

        try:
            embeddings = get_embeddings_with_retry(batch_texts)
            if not embeddings:
                print("Skipping batch due to API issues.")
                continue

            ids = []
            documents = []
            metadatas = []

            for idx, chunk in enumerate(batch):
                chunk_id = f"{chunk['source']}#L{chunk['start_line']}-{chunk['end_line']}"
                ids.append(chunk_id)
                documents.append(chunk["text"])
                metadatas.append({
                    "source": chunk["source"],
                    "scope": chunk["scope"],
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                })

            collection.upsert(
                ids=ids,
                embeddings=cast(Any, embeddings),
                documents=documents,
                metadatas=cast(Any, metadatas),
            )
            success_count += len(batch)

        except Exception as e:
            print(f"Error indexing batch starting at index {i}: {e}")
            print("Continuing with next batch...")

    print("\nIngestion completed successfully!")
    print(f"Total chunks indexed: {success_count}/{total_chunks}")
    print(f"ChromaDB collection count: {collection.count()}")


if __name__ == "__main__":
    main()
