import os
import time

from google import genai
from google.genai import types
from google.genai.errors import APIError

from rag_local.core.config import (
    EMBEDDING_FALLBACK_MODEL,
    EMBEDDING_MODEL,
    ENV_PATH,
    GENERATION_FALLBACK_MODELS,
    GENERATION_MODEL,
    INITIAL_BACKOFF,
    MAX_RETRIES,
)
from rag_local.core.exceptions import EmbeddingError, QueryError, RagLocalError
from rag_local.core.logging import logger

# Inicializar cliente GenAI de forma segura
_genai_client: genai.Client | None = None
_last_api_key: str | None = None


def get_genai_client() -> genai.Client:
    """Retorna el cliente de GenAI inicializado dinámicamente según GEMINI_API_KEY."""
    global _genai_client, _last_api_key
    from rag_local.core import config

    current_key = config.GEMINI_API_KEY

    if _genai_client is None or current_key != _last_api_key:
        if current_key:
            try:
                _genai_client = genai.Client(api_key=current_key)
                _last_api_key = current_key
            except Exception as e:
                logger.exception("Error al inicializar el cliente Google GenAI")
                msg = f"Error al inicializar el cliente Google GenAI: {e}"
                raise RagLocalError(msg) from e
        else:
            logger.warning(
                "Advertencia: GEMINI_API_KEY no encontrada en variables de entorno "
                f"o archivo .env en: {ENV_PATH}"
            )
            try:
                _genai_client = genai.Client()
                _last_api_key = None
            except Exception as e:
                raise RagLocalError(
                    "No se pudo inicializar el cliente GenAI sin API key. "
                    f"Asegúrese de configurar GEMINI_API_KEY. Detalle: {e}"
                ) from e

    return _genai_client


def _call_embedding_api(texts: list[str], model: str) -> list[list[float]] | None:
    """Realiza la llamada real a la API de embeddings con reintentos y backoff."""
    client = get_genai_client()

    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.embed_content(
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
                import random

                sleep_time = backoff + random.uniform(0.0, 1.0)
                logger.warning(
                    f"API saturada ({e.code or 'High Demand'}) usando {model}: "
                    f"{e.message}. Reintentando en {sleep_time:.1f}s "
                    f"(Intento {attempt + 1}/{MAX_RETRIES})..."
                )
                time.sleep(sleep_time)
                backoff *= 2.0
            else:
                logger.error(f"Error de API Gemini en {model}: {e}")
                raise EmbeddingError(f"Error de API Gemini en {model}: {e}") from e
        except Exception as e:
            logger.exception(f"Error inesperado al generar embeddings con {model}")
            raise EmbeddingError(
                f"Error inesperado al generar embeddings con {model}: {e}"
            ) from e
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

    from rag_local.core.config import MAX_BATCH_TOKENS

    subbatches: list[list[str]] = []
    current_subbatch: list[str] = []
    current_tokens: int = 0

    for text in texts:
        tokens = len(text) // 4
        if current_subbatch and (current_tokens + tokens > MAX_BATCH_TOKENS):
            subbatches.append(current_subbatch)
            current_subbatch = [text]
            current_tokens = tokens
        else:
            current_subbatch.append(text)
            current_tokens += tokens

    if current_subbatch:
        subbatches.append(current_subbatch)

    all_embeddings: list[list[float]] = []
    for subbatch in subbatches:
        try:
            # Intentar primero con el modelo configurado
            sub_embs = _call_embedding_api(subbatch, model=EMBEDDING_MODEL)
        except Exception as e:
            err_msg = str(e).lower()
            # El fallback se realiza unicamente si la cuota fue excedida permanentemente
            is_quota = (
                "quota" in err_msg
                or "resource_exhausted" in err_msg
                or "resource exhausted" in err_msg
            )
            if is_quota:
                logger.warning(
                    f"Cuota excedida usando {EMBEDDING_MODEL}: {e}. "
                    f"Intentando fallback a {EMBEDDING_FALLBACK_MODEL}..."
                )
                try:
                    sub_embs = _call_embedding_api(
                        subbatch, model=EMBEDDING_FALLBACK_MODEL
                    )
                except Exception as fallback_err:
                    logger.exception(
                        "Fallo definitivo al generar embeddings tras intentar fallback"
                    )
                    msg = (
                        "Fallo definitivo al generar embeddings tras intentar "
                        f"fallback: {fallback_err}"
                    )
                    raise EmbeddingError(msg) from fallback_err
            else:
                # Si el error es por exceso de uso temporal (429/rate limit)
                # o no hay respuesta, no hacemos fallback
                logger.error(
                    f"Fallo al obtener embeddings con {EMBEDDING_MODEL} "
                    f"(sin fallback): {e}"
                )
                if isinstance(e, EmbeddingError):
                    raise
                raise EmbeddingError(f"Error al obtener embeddings: {e}") from e

        if sub_embs is None:
            return None
        all_embeddings.extend(sub_embs)

    return all_embeddings


def _call_generate_content_api(
    prompt: str,
    system_instruction: str,
    model: str,
) -> str:
    """Realiza la llamada real de generación con reintentos y backoff exponencial."""
    client = get_genai_client()

    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
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
                import random

                sleep_time = backoff + random.uniform(0.0, 1.0)
                logger.warning(
                    f"Generación saturada ({e.code or 'High Demand'}) usando {model}: "
                    f"{e.message}. Reintentando en {sleep_time:.1f}s "
                    f"(Intento {attempt + 1}/{MAX_RETRIES})..."
                )
                time.sleep(sleep_time)
                backoff *= 2.0
            else:
                logger.error(f"Error de API Gemini en {model}: {e.message}")
                raise QueryError(f"Error de API Gemini en {model}: {e.message}") from e
        except Exception as e:
            logger.exception(f"Error inesperado durante la generación en {model}")
            raise QueryError(
                f"Error inesperado durante la generación en {model}: {e}"
            ) from e

    raise QueryError("Se excedió el número máximo de reintentos con la API de Gemini.")


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
        logger.exception(msg)
        raise QueryError(msg) from e
