import os
import time

from google import genai
from google.genai import types
from google.genai.errors import APIError

from rag_local.core.config import (
    EMBEDDING_FALLBACK_MODEL,
    EMBEDDING_MODEL,
    ENV_PATH,
    GEMINI_API_KEY,
    GENERATION_FALLBACK_MODELS,
    GENERATION_MODEL,
    INITIAL_BACKOFF,
    MAX_RETRIES,
)
from rag_local.core.logging import logger

# Inicializar cliente GenAI de forma segura
genai_client: genai.Client | None = None
if GEMINI_API_KEY:
    try:
        genai_client = genai.Client()
    except Exception as e:
        logger.error(f"Error al inicializar el cliente Google GenAI: {e}")
else:
    logger.warning(
        "Advertencia: GEMINI_API_KEY no encontrada en variables de entorno "
        f"o archivo .env en: {ENV_PATH}"
    )


def _call_embedding_api(texts: list[str], model: str) -> list[list[float]] | None:
    """Realiza la llamada real a la API de embeddings con reintentos y backoff."""
    if not genai_client:
        raise ValueError(
            "El cliente Google GenAI no está inicializado. Verifica tu GEMINI_API_KEY."
        )

    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            response = genai_client.models.embed_content(
                model=model,
                contents=[types.Content(parts=[types.Part(text=s)]) for s in texts],
                config=types.EmbedContentConfig(output_dimensionality=768),
            )
            if response.embeddings is None:
                return None
            result_embeddings = []
            for emb in response.embeddings:
                if emb.values is not None:
                    result_embeddings.append(list(emb.values))
            return result_embeddings
        except APIError as e:
            is_rate_limit = e.code == 429
            is_server_error = e.code is not None and e.code >= 500
            is_high_demand = (
                e.message is not None and "high demand" in e.message.lower()
            )

            if (
                is_rate_limit or is_server_error or is_high_demand
            ) and attempt < MAX_RETRIES - 1:
                logger.warning(
                    f"API saturada ({e.code or 'High Demand'}) usando {model}: "
                    f"{e.message}. Reintentando en {backoff:.1f}s "
                    f"(Intento {attempt + 1}/{MAX_RETRIES})..."
                )
                time.sleep(backoff)
                backoff *= 2.0
            else:
                logger.error(f"Error de API Gemini en {model}: {e}")
                raise e
        except Exception as e:
            logger.error(f"Error inesperado en {model}: {e}")
            raise e
    return None


def get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Obtiene embeddings de Gemini con backoff exponencial y fallback.

    Usa text-embedding-004 como fallback.
    """
    if os.getenv("RAG_MOCK_API") == "1":
        import hashlib
        import random

        embeddings = []
        for text in texts:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            rng = random.Random(h)
            vector = [rng.uniform(-1.0, 1.0) for _ in range(768)]
            embeddings.append(vector)
        return embeddings

    try:
        # Intentar primero con el modelo configurado
        return _call_embedding_api(texts, model=EMBEDDING_MODEL)
    except Exception as e:
        logger.warning(
            f"Fallo al obtener embeddings con {EMBEDDING_MODEL}: {e}. "
            f"Intentando fallback a {EMBEDDING_FALLBACK_MODEL}..."
        )
        try:
            # Fallback
            return _call_embedding_api(texts, model=EMBEDDING_FALLBACK_MODEL)
        except Exception as fallback_err:
            logger.error(
                "Fallo definitivo al generar embeddings tras intentar fallback: "
                f"{fallback_err}"
            )
            raise fallback_err


def _call_generate_content_api(
    prompt: str,
    system_instruction: str,
    model: str,
) -> str:
    """Realiza la llamada real de generación con reintentos y backoff exponencial."""
    if not genai_client:
        raise ValueError(
            "El cliente Google GenAI no está inicializado. Verifica tu GEMINI_API_KEY."
        )

    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            response = genai_client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction
                ),
            )
            if response.text is not None:
                return response.text
            raise ValueError("No se recibió respuesta de texto del modelo.")
        except APIError as e:
            is_rate_limit = e.code == 429
            is_server_error = e.code is not None and e.code >= 500
            is_high_demand = (
                e.message is not None and "high demand" in e.message.lower()
            )

            if (
                is_rate_limit or is_server_error or is_high_demand
            ) and attempt < MAX_RETRIES - 1:
                logger.warning(
                    f"Generación saturada ({e.code or 'High Demand'}) usando {model}: "
                    f"{e.message}. Reintentando en {backoff:.1f}s "
                    f"(Intento {attempt + 1}/{MAX_RETRIES})..."
                )
                time.sleep(backoff)
                backoff *= 2.0
            else:
                logger.error(f"Error de API Gemini en {model}: {e.message}")
                raise e
        except Exception as e:
            logger.error(f"Error inesperado durante la generación en {model}: {e}")
            raise e

    raise TimeoutError(
        "Se excedió el número máximo de reintentos con la API de Gemini."
    )


def generate_content(
    prompt: str,
    system_instruction: str,
    model_name: str = GENERATION_MODEL,
) -> str:
    """Genera texto usando el modelo configurado con fallback en caso de fallas."""
    if os.getenv("RAG_MOCK_API") == "1":
        lines = [line.strip() for line in prompt.split("\n") if line.strip()]
        keywords = ["PREGUNTA", "CONTEXTO", "SOURCE FILE", "xml", "XML"]
        key_lines = [
            line
            for line in lines
            if any(x in line for x in keywords) or len(line) < 120
        ]
        prompt_fragments = "\n".join(key_lines[:20])

        mock_response = (
            f"<response>\n"
            f"[MOCK_GEMINI_RESPONSE]\n"
            f"System Instruction: {system_instruction}\n"
            f"Key Prompt Fragments:\n{prompt_fragments}\n"
            f"</response>"
        )
        return mock_response

    try:
        return _call_generate_content_api(prompt, system_instruction, model_name)
    except Exception as e:
        logger.warning(
            f"Fallo al generar contenido con el modelo {model_name}: {e}. "
            "Iniciando flujo de fallback..."
        )
        
        for fallback_model in GENERATION_FALLBACK_MODELS:
            if fallback_model == model_name:
                continue
            try:
                logger.info(f"Intentando fallback con el modelo: {fallback_model}...")
                return _call_generate_content_api(
                    prompt, system_instruction, fallback_model
                )
            except Exception as fb_err:
                logger.warning(
                    f"Fallo al generar contenido con fallback "
                    f"{fallback_model}: {fb_err}"
                )
        
        msg = f"Error definitivo: Fallaron todos los modelos de generación: {e}"
        logger.error(msg)
        return msg
