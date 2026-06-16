# RAG Local Optimization

Sistema RAG local de alto rendimiento y bajo consumo de tokens optimizado para analizar monorepos de código estructurados en Angular 21, NestJS 11 + Fastify y Prisma.

El objetivo principal de esta herramienta es proveer búsquedas de contexto sumamente precisas a agentes de IA, reduciendo significativamente el ruido (eliminando líneas de código cortadas a la mitad) y optimizando el consumo de tokens mediante la fusión inteligente de fragmentos adyacentes, reordenamiento de relevancia y formateo XML estructurado.

---

## Caracteristicas Claves

### 1. Chunking Sintactico con Tree-Sitter
- **TypeScript**: Utiliza el parser sintáctico de `tree-sitter-typescript` para agrupar clases y decoradores (ej. `@Component` o `@Injectable`). Mantiene los constructores y métodos completos sin cortarlos a la mitad.
- **HTML**: Utiliza el parser de `tree-sitter-html` para agrupar etiquetas jerárquicas y previene cortes a la mitad de tags.
- **Prisma**: Divide esquemas por bloques funcionales (`model`, `enum`, `datasource`, etc.) y extrae dependencias entre tablas.
- **Bloque Unico**: Si un archivo es menor a 50 líneas, se procesa como un único bloque para conservar el contexto completo.

### 2. Persistencia con LanceDB
- Se utiliza LanceDB como base de datos vectorial local e integrada.
- **Ingesta Incremental Basada en Cache**: Almacena hashes SHA256 de los archivos en la base de datos para omitir archivos sin cambios y evitar la regeneración innecesaria de embeddings.
- Remueve de manera automática los chunks antiguos de archivos modificados o eliminados.

### 3. Re-ranking de Resultados (GPU / CPU)
- Incorpora una capa de re-ranking con la librería `rerankers` utilizando el modelo `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- Permite recuperar un número alto de chunks candidatos en la búsqueda inicial y reducirlos al subconjunto verdaderamente relevante antes de enviarlo a Gemini.
- Soporte nativo para aceleración por GPU (CUDA) con fallback automático y transparente a CPU si no hay acelerador disponible.

### 4. Optimizacion de Tokens y Contexto para Agentes
- **Fusion de Chunks**: Chunks adyacentes o solapados del mismo archivo se fusionan en un único fragmento continuo.
- **Estructura XML Limpia**: Contexto formateado mediante bloques XML estructurados (`<context>`, `<file path="...">`), facilitando la lectura a agentes LLM.
- **Seguridad**: Escape estricto de caracteres especiales (`&`, `<`, `>`, `"`, `'`) en el código y en las consultas para mitigar inyecciones de prompts.
- **Truncado Seguro**: Si el contexto excede 15,000 caracteres, se trunca limpiamente con un indicador `[TRUNCATED]`.

---

## Estructura del Codigo

- `src/rag_local/`:
  - `cli/ingest.py`: Comando `rag-ingest` para indexar y actualizar el repositorio en LanceDB.
  - `cli/query.py`: Comando `rag-query` para consultar al RAG con opción de formato JSON o estético.
  - `core/config.py`: Parámetros globales de reintentos, límites, carpetas a escanear e ignorar.
  - `core/logging.py`: Configuración estética de logs del sistema mediante `rich`.
  - `services/db.py`: Wrapper e implementación de colección LanceDB, hashes de archivos, caché de ingesta e integración del chunking sintáctico.
  - `services/gemini.py`: Wrapper para interactuar con la API oficial de Google GenAI (embeddings y generación).
  - `services/rag.py`: Flujo de orquestación, integración del Reranker, fusión de fragmentos contiguos, formato XML y escape contra inyecciones.
- `tests/e2e/test_suite.py`: Completa suite de pruebas de caja negra TDD con 72 casos de verificación.

---

## Instalacion y Uso

### Requisitos Previos

- Python 3.12+ gestionado a través de `mise` o `uv`.
- Clave de API de Gemini (`GEMINI_API_KEY`).

### Configuracion de Entorno

Copia el archivo `.env.example` como `.env` e ingresa tu clave:
```bash
GEMINI_API_KEY="tu-clave-api-aquí"
```

### Ejecutar Ingestion de Codigo

Escanea el monorepo y actualiza de manera incremental la base de datos LanceDB:
```bash
uv run rag-ingest
```

### Realizar Consultas RAG

**Formato Humano (Terminal con Rich Panels)**:
```bash
uv run rag-query --query "¿Cómo está definido el modelo User en el esquema Prisma?" --scope backend
```

**Formato JSON (Para Consumo de Agentes de IA)**:
```bash
uv run rag-query --query "Find AuthController login method dependencies" --scope backend --json
```

---

## Pruebas de Software (TDD)

El proyecto cuenta con una suite E2E que valida la consistencia en el comportamiento del RAG. Para ejecutarla offline:
```bash
uv run --with pytest pytest
```

> [!NOTE]
> Las pruebas operan desconectadas (offline) mediante un mock determinista de embeddings y respuestas del LLM para asegurar repetibilidad y velocidad.

> [!IMPORTANT]
> LanceDB almacena localmente los datos en el directorio `.lancedb/`. Este directorio, al igual que los archivos `.env` y el caché de pytest, se encuentra configurado en el archivo `.gitignore` para no ser subido al repositorio de control de versiones.
