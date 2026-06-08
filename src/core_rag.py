import os
import sys
import time
from pathlib import Path
from typing import Any, cast

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError

# Get script and configuration directories
SCRIPT_DIR = Path(__file__).resolve().parent
RAG_ROOT = SCRIPT_DIR.parent
REPO_ROOT = RAG_ROOT.parent

# Load environment variables from .env file located in the rag-local root
ENV_PATH = RAG_ROOT / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Verify that the Gemini API key is present
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    # We do not crash here immediately if imported as a library, but log it
    print(
        f"Warning: GEMINI_API_KEY not found in environment variables "
        f"or .env file at '{ENV_PATH}'",
        file=sys.stderr
    )

# Initialize Google GenAI client
genai_client: genai.Client | None = None
if GEMINI_API_KEY:
    try:
        genai_client = genai.Client()
    except Exception as e:
        print(f"Error initializing Google GenAI client: {e}", file=sys.stderr)

# Database path in RAG root directory
CHROMA_PATH = RAG_ROOT / ".chromadb"

# Configuration constants
MAX_RETRIES = 5
INITIAL_BACKOFF = 2.0  # seconds


def get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Gets embeddings from Gemini API with exponential backoff for rate limits."""
    if not genai_client:
        raise ValueError("Google GenAI client is not initialized. Check your GEMINI_API_KEY.")

    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            response = genai_client.models.embed_content(
                model="gemini-embedding-2",
                contents=[types.Content(parts=[types.Part(text=s)]) for s in texts]
            )
            if response.embeddings is None:
                return None
            result_embeddings = []
            for emb in response.embeddings:
                if emb.values is not None:
                    result_embeddings.append(list(emb.values))
            return result_embeddings
        except APIError as e:
            # Retry on rate limit (429), server errors (5xx) or high demand
            is_rate_limit = e.code == 429
            is_server_error = e.code and e.code >= 500
            is_high_demand = e.message and "high demand" in e.message.lower()

            if (is_rate_limit or is_server_error or is_high_demand) and attempt < MAX_RETRIES - 1:
                print(
                    f"Gemini API rate limited/busy ({e.code or 'High Demand'}): {e.message}. "
                    f"Retrying in {backoff:.1f}s (Attempt {attempt + 1}/{MAX_RETRIES})...",
                    file=sys.stderr
                )
                time.sleep(backoff)
                backoff *= 2.0
            else:
                print(f"Gemini API Error in get_embeddings: {e}", file=sys.stderr)
                raise e
        except Exception as e:
            print(f"Unexpected error in get_embeddings: {e}", file=sys.stderr)
            raise e
    return None


def get_chroma_collection():
    """Initializes and returns the persistent ChromaDB collection."""
    try:
        chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        return chroma_client.get_or_create_collection(name="monorepo_code")
    except Exception as e:
        print(f"Error connecting to ChromaDB: {e}", file=sys.stderr)
        raise e


def query_db(query_text: str, scope: str | None = None, k: int = 4) -> Any:
    """Embeds the query text and retrieves the top k matches from ChromaDB."""
    # 1. Embed query
    query_vector_list = get_embeddings([query_text])
    if not query_vector_list or len(query_vector_list) == 0:
        raise ValueError("Failed to generate embedding for the search query.")
    query_vector = query_vector_list[0]

    # 2. Retrieve collection
    collection = get_chroma_collection()

    # 3. Build filter if scope is specified
    where_filter = None
    if scope:
        where_filter = {"scope": scope}

    # 4. Query DB
    try:
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=k,
            where=cast(Any, where_filter),
        )
        return results
    except Exception as e:
        print(f"Error querying ChromaDB collection: {e}", file=sys.stderr)
        raise e


def generate_response(
    query_text: str, retrieved_data: Any, respond_in_english: bool = False
) -> str:
    """Builds prompt and generates response using gemini-2.5-flash with retries."""
    if not genai_client:
        raise ValueError("Google GenAI client is not initialized. Check your GEMINI_API_KEY.")

    documents = retrieved_data.get("documents")
    metadatas = retrieved_data.get("metadatas")

    docs_list = documents[0] if documents else []
    meta_list = metadatas[0] if metadatas else []

    if not docs_list:
        return "No relevant code fragments were found in the local vector database."

    # Build context string
    context_blocks = []
    for doc, meta in zip(docs_list, meta_list):
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

    # Determine prompt target language
    target_language = "ENGLISH" if respond_in_english else "SPANISH"

    system_instruction = (
        "You are a Senior AI Engineer expert in software development, "
        "Angular 21, NestJS 11, Fastify, and Prisma.\n"
        "Your task is to answer the user's question based strictly on the provided "
        "source code context.\n"
        "Follow these strict rules:\n"
        f"1. ALWAYS RESPOND IN {target_language} to the user.\n"
        "2. Be clear, educational, and direct.\n"
        "3. Use code blocks when necessary to illustrate or explain the solution.\n"
        "4. If the answer cannot be deduced from the provided code, state it clearly, "
        "but try to respond helpfully with what is available.\n"
    )

    prompt = (
        f"CONTEXTO DE CÓDIGO FUENTE RECUPERADO:\n"
        f"{context_str}\n\n"
        f"PREGUNTA DEL USUARIO:\n"
        f"{query_text}\n\n"
        f"RESPUESTA:"
    )

    backoff = 2.0
    for attempt in range(MAX_RETRIES):
        try:
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"system_instruction": system_instruction},
            )
            if response.text is not None:
                return response.text
            return "Error: Did not receive text response from the model."
        except APIError as e:
            is_rate_limit = e.code == 429
            is_server_error = e.code and e.code >= 500
            is_high_demand = e.message and "high demand" in e.message.lower()

            if (is_rate_limit or is_server_error or is_high_demand) and attempt < MAX_RETRIES - 1:
                print(
                    f"Gemini API generation busy ({e.code or 'High Demand'}): {e.message}. "
                    f"Retrying in {backoff:.1f}s (Attempt {attempt + 1}/{MAX_RETRIES})...",
                    file=sys.stderr
                )
                time.sleep(backoff)
                backoff *= 2.0
            else:
                return f"Error calling Gemini API: {e.message}"
        except Exception as e:
            return f"Unexpected error during generation: {e}"

    return "Error: Maximum retries exceeded calling Gemini API."
