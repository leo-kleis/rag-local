# Project: RAG Local Optimization

## Architecture
El proyecto `rag-local` es una herramienta de línea de comandos (CLI) en Python para realizar RAG local sobre repositorios de código.

- **LanceDB**: Base de datos vectorial embebida local utilizada para persistir las representaciones vectoriales del código.
- **Gemini API**: Usada para generar embeddings (`gemini-embedding-2` con truncamiento a 768 dimensiones) y para la generación de respuestas contextualizadas (`gemini-2.5-flash`).
- **Entrada**: Archivos de código (TypeScript, Prisma, HTML, etc.).
- **Procesamiento**:
  1. `scan_files()`: Escaneo recursivo de directorios aplicando filtros de `.gitignore` del proyecto de manera automática.
  2. `detect_project_roots()`: Detección inteligente de raíces de frameworks (`angular.json` y `nest-cli.json`) para determinar scopes dinámicos (`frontend` y `backend`).
  3. `chunk_file()`: Chunking sintáctico de archivos mediante `tree-sitter`.
  4. `index_chunks()`: Generación concurrente de embeddings e inserción incremental en LanceDB con recuperación individual inteligente (uno a uno) ante fallos.
- **Consulta**:
  1. `query_db()`: Recuperación inicial de fragmentos semánticamente similares en LanceDB con filtrado dinámico de scope.
  2. **Re-ranking**: Reordenamiento local mediante `rerankers` (usando el modelo `cross-encoder/ms-marco-MiniLM-L-6-v2` con soporte para GPU CUDA).
  3. Fusión de fragmentos adyacentes del mismo archivo y formateo final en bloques XML estructurados.
  4. LLM: Generación de la respuesta estructurada final envuelta en etiquetas `<response>`.

## Code Layout
- `src/rag_local/`:
  - `cli/ingest.py`: Comando CLI `rag-ingest` para indexar archivos de forma incremental, con detención estricta si no se detectan frameworks.
  - `cli/query.py`: Comando CLI `rag-query` para consultar al RAG de forma humana o vía JSON, sin limitaciones estáticas de scope.
  - `core/config.py`: Parámetros de configuración (rutas de LanceDB, extensiones, límites, etc.).
  - `core/logging.py`: Configuración del sistema de logs con formato enriquecido.
  - `parsers/`: Módulos de análisis sintáctico con `tree-sitter` para estructurar chunks de TypeScript y HTML.
  - `services/db.py`: Wrapper de LanceDB, cálculo de hashes y orquestación de caché de ingesta.
  - `services/scanner.py`: Lógica de detección de frameworks, carga y análisis de `.gitignore` y escaneo recursivo.
  - `services/gemini.py`: Cliente de APIs de Google GenAI con reintentos exponenciales y fallback de modelos.
  - `services/rag.py`: Flujo de consulta RAG, aplicación del Reranker, fusión de bloques y formateo XML.

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|---|---|-------------|--------|
| M1 | E2E Testing Track | Crear suite de pruebas de caja negra (74 casos de prueba, Tiers 1-4) | None | COMPLETED |
| M2 | Chunking y Metadatos | Implementar parser basado en bloques/sintáctico para TS/Prisma/HTML, y extraer metadatos ricos | None | COMPLETED |
| M3 | Ingesta Incremental | Caché basada en hashes/fechas para evitar re-indexación de archivos no modificados | None | COMPLETED |
| M4 | Fusión y Prompt XML | Fusión de chunks adyacentes y prompts formateados con bloques XML limpios | M2, M3 | COMPLETED |
| M5 | Calidad y README | Refactor PEP8, tipado estricto con Pydantic/pyrefly, README.md sin emojis | M2, M3, M4 | COMPLETED |
| M6 | Final Milestone | Validación final de pruebas E2E e inyección de pruebas adversarias (Tier 5) | M1, M5 | COMPLETED |
| M7 | Mejoras Avanzadas | Búsqueda híbrida (FTS), Graph-RAG, relaciones Prisma y escaneo recursivo con gitignores múltiples | M6 | COMPLETED |
| M8 | Hierarchical AST | Implementar segmentación jerárquica basada en AST (TS) y modularizar parser en submódulos | M7 | COMPLETED |
| M9 | Optimización y Seguridad | Implementar índices escalares, compactación de base de datos con optimize() y sanitización de comillas simples | M6, M7 | COMPLETED |
| M10 | Ingesta Concurrente y Robustez | Indexado paralelo de lotes, recuperación de errores uno a uno en embeddings y parametrización de modelos | M9 | COMPLETED |

## Interface Contracts
### `services.db.chunk_file` ↔ `cli.ingest`
- **Signature**: `chunk_file(file_path: Path) -> list[dict[str, Any]]`
- **Output format**: Cada dict en la lista contiene:
  - `text`: str (contenido completo del fragmento sintáctico sin cortes de bloque)
  - `start_line`: int
  - `end_line`: int
  - `metadata`: dict (con claves `class_name`, `method_name`, `imports`, `dependencies`, `models` adicionales según aplique al parser)
