import argparse
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

# Ruta de la base de datos en el directorio raíz de RAG
CHROMA_PATH = RAG_ROOT / ".chromadb"


def parse_arguments():
    """Analiza los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Query the local Monorepo RAG system using ChromaDB and Gemini."
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="The question or search query you want to ask about the codebase.",
    )
    parser.add_argument(
        "--scope",
        type=str,
        choices=["frontend", "backend"],
        default=None,
        help="Filter results by scope: 'frontend' (Angular) or 'backend' (NestJS/Fastify/Prisma).",
    )
    return parser.parse_args()


def get_query_embedding(query_text: str) -> list[float] | None:
    """Genera una incrustación vectorial (vector embedding) para la

    consulta del usuario usando gemini-embedding-2.
    """
    try:
        response = genai_client.models.embed_content(
            model="gemini-embedding-2", contents=query_text
        )
        if response.embeddings and len(response.embeddings) > 0:
            if response.embeddings[0].values is not None:
                return list(response.embeddings[0].values)
        return None
    except APIError as e:
        print(f"Gemini API Error while generating embedding: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error generating embedding: {e}")
        return None


def retrieve_relevant_chunks(query_vector: list[float], scope: str | None, k: int = 4):
    """Consulta ChromaDB para recuperar los k fragmentos de código más relevantes."""
    if not CHROMA_PATH.exists():
        print(f"Error: Database not found at '{CHROMA_PATH}'. Please run 'src/ingest.py' first.")
        sys.exit(1)

    try:
        chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        collection = chroma_client.get_collection(name="monorepo_code")
    except Exception as e:
        print(f"Error connecting to ChromaDB: {e}")
        sys.exit(1)

    # Construir filtro de metadatos si se especifica el alcance (scope)
    where_filter = None
    if scope:
        where_filter = {"scope": scope}

    try:
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=k,
            where=cast(Any, where_filter),
        )
        return results
    except Exception as e:
        print(f"Error querying ChromaDB collection: {e}")
        sys.exit(1)


def generate_llm_response(query: str, retrieved_data) -> str:
    """Construye el prompt y llama a gemini-2.5-flash para generar la respuesta final."""
    documents = retrieved_data.get("documents", [[]])[0]
    metadatas = retrieved_data.get("metadatas", [[]])[0]

    if not documents:
        return "No se encontraron fragmentos de código relevantes en la base de datos local."

    # Construir bloque de contexto
    context_blocks = []
    for doc, meta in zip(documents, metadatas):
        source_file = meta.get("source", "Unknown file")
        scope_name = meta.get("scope", "unknown").upper()
        start_line = meta.get("start_line", 1)
        end_line = meta.get("end_line", 1)

        block = (
            f"--- START SOURCE FILE: {source_file} "
            f"(Scope: {scope_name}, Lines: {start_line}-{end_line}) ---\n"
            f"{doc}\n"
            f"--- END SOURCE FILE: {source_file} ---\n"
        )
        context_blocks.append(block)

    context_str = "\n".join(context_blocks)

    # Construct the instruction prompt (keeping line lengths under 100 characters)
    # Using English for system instructions optimizes LLM reasoning and reduces token usage.
    system_instruction = (
        "You are a Senior AI Engineer expert in software development, "
        "Angular 21, NestJS 11, Fastify, and Prisma.\n"
        "Your task is to answer the user's question based strictly on the provided "
        "source code context.\n"
        "Follow these strict rules:\n"
        "1. ALWAYS RESPOND IN SPANISH to the user.\n"
        "2. Be clear, educational, and direct.\n"
        "3. Use code blocks when necessary to illustrate or explain the solution.\n"
        "4. If the answer cannot be deduced from the provided code, state it clearly, "
        "but try to respond helpfully with what is available.\n"
    )

    prompt = (
        f"CONTEXTO DE CÓDIGO FUENTE RECUPERADO:\n"
        f"{context_str}\n\n"
        f"PREGUNTA DEL USUARIO:\n"
        f"{query}\n\n"
        f"RESPUESTA:"
    )

    backoff = 2.0
    max_retries = 4
    for attempt in range(max_retries):
        try:
            # Llamar al modelo gemini-2.5-flash
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"system_instruction": system_instruction},
            )
            if response.text is not None:
                return response.text
            return "Error: No se recibió texto de respuesta del modelo."
        except APIError as e:
            # Reintentar en caso de límite de cuota (429),
            # error de servidor (5xx) o picos de demanda
            is_rate_limit = e.code == 429
            is_server_error = e.code and e.code >= 500
            is_high_demand = e.message and "high demand" in e.message.lower()

            if (is_rate_limit or is_server_error or is_high_demand) and attempt < max_retries - 1:
                print(
                    f"API de Gemini saturada ({e.code or 'Demanda Alta'}): {e.message}. "
                    f"Reintentando en {backoff:.1f}s (Intento {attempt + 1}/{max_retries})...."
                )
                time.sleep(backoff)
                backoff *= 2.0
            else:
                return f"Error en la API de Gemini al generar la respuesta: {e.message}"
        except Exception as e:
            return f"Error inesperado al generar la respuesta: {e}"

    return "Error: Se superó el límite de reintentos con la API de Gemini."


def main():
    args = parse_arguments()

    print(f"Analyzing query: '{args.query}'")
    if args.scope:
        print(f"Applying scope filter: '{args.scope}'")

    # 1. Incrustar consulta (embed query)
    print("Generating query embedding...")
    query_vector = get_query_embedding(args.query)
    if not query_vector:
        print("Failed to embed query. Exiting.")
        sys.exit(1)

    # 2. Recuperar de ChromaDB
    print("Retrieving relevant code blocks from ChromaDB...")
    results = retrieve_relevant_chunks(query_vector, args.scope)

    # 3. Generar respuesta usando Gemini
    print("Generating answer using gemini-2.5-flash...")
    answer = generate_llm_response(args.query, results)

    # 4. Imprimir resultados
    print("\n" + "=" * 40 + " CONTEXTO RECUPERADO " + "=" * 40)
    retrieved_metadatas = results.get("metadatas")
    metadatas = retrieved_metadatas[0] if retrieved_metadatas else []
    for idx, meta in enumerate(metadatas):
        source = meta.get("source")
        lines = f"L{meta.get('start_line')}-{meta.get('end_line')}"
        print(f"[{idx + 1}] {source} ({lines})")

    print("\n" + "=" * 40 + " RESPUESTA RAG " + "=" * 40)
    print(answer)
    print("=" * 101)


if __name__ == "__main__":
    main()
