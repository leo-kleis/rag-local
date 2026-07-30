# Project: RAG Local Optimization

## Architecture
El proyecto `rag-local` es una herramienta de línea de comandos (CLI) en Python para realizar RAG local sobre repositorios de código.

- **LanceDB**: Base de datos vectorial embebida local utilizada para persistir las representaciones vectoriales del código.
- **Local Embeddings**: Generación 100% local y offline usando `sentence-transformers` con el modelo `Alibaba-NLP/gte-multilingual-base` (768 dimensiones). Se ejecuta en GPU CUDA si está disponible, con fallback automático a CPU.
- **Gemini API**: Usada exclusivamente para la generación de respuestas contextualizadas mediante LLM (`gemini-2.5-flash`).
- **Entrada**: Archivos de código (TypeScript, TSX, JSX, Prisma, HTML, CSS, Python).
- **Procesamiento**:
  1. `scan_files()`: Escaneo recursivo de directorios aplicando filtros de `.gitignore` del proyecto de manera automática.
  2. `detect_project_roots()`: Detección inteligente de raíces de frameworks (`angular.json`, `nest-cli.json`, `pyproject.toml` y `next.config.ts/js/mjs`) para determinar scopes dinámicos (`angular`, `nestjs`, `python`, `nextjs-app`).
  3. `chunk_file()`: Chunking sintáctico de archivos mediante `tree-sitter` y parsers dedicados (`css.py`, `html.py`, `prisma.py`).
  4. `index_chunks()`: Generación local y offline de embeddings e inserción incremental en LanceDB con soporte de reindexación forzada (`--force`).
- **Consulta**:
  1. `query_db()`: Recuperación inicial de fragmentos semánticamente similares en LanceDB con filtrado dinámico de scope.
  2. **Re-ranking con filtro de relevancia**: Reordenamiento local mediante `rerankers` (`BAAI/bge-reranker-base`). Los chunks con score inferior a `MIN_RERANK_SCORE` (-2.0 en logits raw) se descartan automáticamente como irrelevantes.
  3. **Refusal explícito**: Si ningún chunk supera el threshold, el sistema retorna `NO_CONTEXT: ...` en vez de contexto vacío o ruido.
  4. Fusión de fragmentos adyacentes del mismo archivo y formateo final en bloques XML estructurados.
- **Mapa del proyecto y Herramientas Especializadas**:
  - `get_project_map()`: Lee metadatos de LanceDB y devuelve el mapa de símbolos por scope.
  - `get_styles_map()`: Analiza hojas de estilo CSS, variables de diseño y clases obsoletas (Dead CSS) con soporte para patrones BEM, ternarios y coincidencia de prefijos.
  - `get_code_metrics()`: Analiza el volumen de líneas de código (LOC) e identifica archivos críticos o de advertencia que requieren refactorización.
- **Soporte Multiproyecto**: Soporte dinámico para trabajar con múltiples repositorios de forma aislada. En tiempo de ejecución, el servidor MCP muta `config.REPO_ROOT` y `config.LANCEDB_PATH` en función del parámetro `project_path` provisto por las herramientas, encapsulando y aislando el índice vectorial en el subdirectorio `.lancedb/` de cada repositorio destino. El CLI asume de forma predeterminada el directorio actual (CWD) si no se configuran variables de entorno.

## Code Layout
- `src/rag_local/`:
  - `cli/ingest.py`: Comando CLI `rag-ingest` para indexar archivos con soporte para `--force` / `-f`.
  - `cli/query.py`: Comando CLI `rag-query` para consultar al RAG de forma humana o vía JSON.
  - `cli/styles.py`: Comando CLI `rag-styles` para auditar el mapa de estilos y clases obsoletas.
  - `cli/metrics.py`: Comando CLI `rag-loc` para calcular métricas de código (LOC) e identificar refactorizaciones necesarias.
  - `mcp/`: Servidor MCP (`rag-mcp`) estructurado en herramientas modulares (`tools/query.py`, `tools/ingest.py`, `tools/config.py`, `tools/project_map.py`, `tools/styles.py`, `tools/metrics.py`) para exponerlas de forma limpia a agentes LLM con texto plano optimizado en tokens.
  - `core/config.py`: Gestión estructurada de configuraciones y variables de entorno mediante `pydantic-settings`.
  - `core/logging.py`: Configuración del sistema de logs con formato enriquecido.
  - `parsers/`: Módulos de análisis sintáctico. `typescript/`, `html.py`, `prisma.py` y `css.py`.
  - `services/db.py`: Wrapper de LanceDB, cálculo de hashes y orquestación de caché de ingesta.
  - `services/styles.py`: Servicio de mapeo de estilos CSS, variables y detección de clases no utilizadas.
  - `services/metrics.py`: Servicio de cálculo de métricas de código (LOC físicas y efectivas).
  - `services/project_map.py`: Lector de metadatos LanceDB que genera un mapa estructural del proyecto.
  - `services/graph.py`: Generador de visualización interactiva del grafo en 3D (WebGL), 2D (Vis.js) y Mermaid.
  - `services/scanner.py`: Lógica de detección de frameworks, carga y análisis de `.gitignore`, escaneo recursivo.
  - `services/embeddings.py`: Servicio exclusivo de generación de embeddings locales usando sentence-transformers.
  - `services/gemini.py`: Cliente de generación de contenido LLM basado en Google GenAI con fallbacks secuenciales.
  - `services/rag.py`: Flujo de consulta RAG, aplicación del Reranker, fusión de bloques y formateo XML.
  - `services/project.py`: Configuración dinámica de directorios del monorepo y aislamiento de rutas seguras.
  - `services/subprocess.py`: Ejecutor reutilizable de subprocesos de consola asíncronos con callbacks de progreso.

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
| M10 | Embeddings Locales y Robustez | Migración a embeddings locales de última generación (gte-multilingual-base) y optimización de ingesta offline | M9 | COMPLETED |
| M11 | Grounding y Output Estructurado | Filtro post-rerank por score (`MIN_RERANK_SCORE`), refusal explícito `NO_CONTEXT`, header de archivos relevantes en MCP, corrección de scope por especificidad, tasks en mise.toml | M10 | COMPLETED |
| M12 | Mapa Estructural del Proyecto | Tool MCP `get_project_map` que lee metadatos de LanceDB sin embeddings y retorna un mapa de clases/servicios/modelos por scope | M11 | COMPLETED |
| M13 | Grafo Interactivo 3D/2D y Mermaid | Comando CLI y herramienta MCP para exportar visualización premium de relaciones a HTML en .lancedb/ | M12 | COMPLETED |
| M14 | Soporte CSS y Métricas LOC | Mapeo de estilos CSS, detección de Dead CSS con prefijos BEM, métricas LOC y herramientas MCP formateadas para agentes | M13 | COMPLETED |

## Interface Contracts
### `services.db.chunk_file` ↔ `cli.ingest`
- **Signature**: `chunk_file(file_path: Path) -> list[dict[str, Any]]`
- **Output format**: Cada dict en la lista contiene:
  - `text`: str (contenido completo del fragmento sintáctico sin cortes de bloque)
  - `start_line`: int
  - `end_line`: int
  - `metadata`: dict (con claves `class_name`, `method_name`, `imports`, `dependencies`, `models` adicionales según aplique al parser)
