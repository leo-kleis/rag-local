import os
import time

from google import genai
from google.genai import types
from google.genai.errors import APIError

from rag_local.core.config import (
    ENV_PATH,
    GENERATION_FALLBACK_MODELS,
    GENERATION_MODEL,
    INITIAL_BACKOFF,
    MAX_RETRIES,
)
from rag_local.core.exceptions import QueryError, RagLocalError
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
