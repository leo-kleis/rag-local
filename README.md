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

### 2. Persistencia y Búsqueda Híbrida (LanceDB)
- **Búsqueda Híbrida**: Combina la similitud semántica (vectores) con búsqueda de texto completo (FTS/BM25) indexando la columna `text`. Esto garantiza encontrar términos de código exactos (variables o firmas de métodos).
- **Ingesta Incremental Basada en Cache**: Almacena hashes SHA256 para evitar re-indexar archivos sin cambios, eliminando chunks obsoletos de forma automática.
- **Resiliencia**: Fallback automático a búsqueda vectorial pura si la consulta FTS falla.

### 3. Re-ranking de Resultados (GPU / CPU)
- Incorpora una capa de re-ranking con la librería `rerankers` utilizando el modelo `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- Permite recuperar un número alto de chunks candidatos en la búsqueda inicial y reducirlos al subconjunto verdaderamente relevante antes de enviarlo a Gemini.
- Soporte nativo para aceleración por GPU (CUDA) con fallback automático y transparente a CPU si no hay acelerador disponible.

### 4. Enriquecimiento de Contexto Relacional (Graph-RAG)
- **TypeScript & NestJS**: Analiza imports de dependencias locales e inyecta fragmentos importados dentro de etiquetas `<imported_file path="..." relation_type="...">`.
- **Prisma**: Identifica relaciones directas (`@relation` y tablas asociadas) e inyecta los modelos vinculados en etiquetas `<related_model name="...">`.
- **Soporte de Gitignores Múltiples**: Escaneo recursivo que hereda y combina de forma automática las exclusiones de todos los archivos `.gitignore` anidados en subcarpetas.

### 5. Optimizacion de Tokens y Contexto para Agentes
- **Fusion de Chunks**: Chunks adyacentes o solapados del mismo archivo se fusionan en un único fragmento continuo.
- **Estructura XML Limpia**: Contexto formateado mediante bloques XML estructurados (`<context>`, `<file path="...">`), facilitando la lectura a agentes LLM.
- **Seguridad**: Escape estricto de caracteres especiales (`&`, `<`, `>`, `"`, `'`) en el código y en las consultas para mitigar inyecciones de prompts.
- **Truncado Seguro**: Si el contexto excede 15,000 caracteres, se trunca limpiamente con un indicador `[TRUNCATED]`.

---

## Estructura del Codigo

- `src/rag_local/`:
  - `cli/ingest.py`: Comando `rag-ingest` para indexar y actualizar el repositorio en LanceDB.
  - `cli/query.py`: Comando `rag-query` para consultar al RAG con opción de formato JSON o estético.
  - `core/config.py`: Parámetros globales de reintentos, límites y extensiones de archivos.
  - `core/logging.py`: Configuración estética de logs del sistema mediante `rich`.
  - `parsers/`: Módulos de análisis sintáctico.
    - `typescript/`: Subpaquete modular que implementa **Hierarchical AST Chunking** mediante `tree-sitter`, dividiendo clases y métodos preservando imports y cabeceras de clase de forma sintácticamente válida.
    - `html.py` y `prisma.py`: Parsers sintácticos especializados.
  - `services/db.py`: Wrapper e implementación de colección LanceDB, hashes de archivos, caché de ingesta e integración del chunking sintáctico.
  - `services/gemini.py`: Wrapper para interactuar con la API oficial de Google GenAI (embeddings y generación).
  - `services/scanner.py`: Detección automática de raíces de proyectos, soporte de `.gitignore` nativo y escaneo recursivo.
  - `services/rag.py`: Flujo de orquestación, integración del Reranker, fusión de fragmentos contiguos, formato XML y escape contra inyecciones.
- `tests/e2e/`:
  - `conftest.py`: Fixture compartido `setup_test_env` para aislamiento del entorno de pruebas.
  - `test_f1_scan.py` a `test_f6_fusion.py`: Suites de pruebas dedicadas para cada característica funcional (Tiers 1 y 2).
  - `test_f7_cross.py`: Pruebas de integración y combinaciones cruzadas (Tier 3).
  - `test_f8_scenarios.py`: Escenarios de integración en monorepos reales y reranking (Tier 4).


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

Escanea el repositorio raíz en búsqueda de proyectos Angular (mediante `angular.json`) y NestJS (mediante `nest-cli.json`), clasifica automáticamente sus archivos en scopes (`frontend` / `backend`) y los indexa incrementalmente en LanceDB. Además, aplica de manera automática las exclusiones descritas en el archivo `.gitignore` del proyecto:
```bash
uv run rag-ingest
```

> [!IMPORTANT]
> Si el script no detecta al menos un proyecto Angular o NestJS, la ejecución se detendrá de inmediato con un mensaje de error descriptivo.

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
uv run pytest
```

> [!NOTE]
> Las pruebas operan desconectadas (offline) mediante un mock determinista de embeddings y respuestas del LLM para asegurar repetibilidad y velocidad.

> [!IMPORTANT]
> LanceDB almacena localmente los datos en el directorio `.lancedb/`. Este directorio, al igual que los archivos `.env` y el caché de pytest, se encuentra configurado en el archivo `.gitignore` para no ser subido al repositorio de control de versiones.
