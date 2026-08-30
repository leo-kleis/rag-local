# Project: RAG Local Optimization

## Architecture
El proyecto `rag-local` es una herramienta de línea de comandos (CLI) en Python para realizar RAG local sobre repositorios de código.

- **Requisito de GPU**: El proyecto requiere una GPU NVIDIA con CUDA (>=6 GB VRAM). No existe fallback a CPU. GPU de referencia: GTX 1080 Ti (11 GB). Los valores de protección de VRAM del daemon son configurables en `core/config.py`.
- **LanceDB**: Base de datos vectorial embebida local utilizada para persistir las representaciones vectoriales y metadatos extendidos del código (`lines_code`, `css_rules`, `class_parents`).
- **Ejecución 100% desde LanceDB (0 Lecturas a Disco)**: Las herramientas `get_code_metrics()`, `get_styles_map()`, `audit_layout_risks()` y `get_project_map()` leen directamente los metadatos estructurados desde LanceDB sin acceder al sistema de archivos en disco durante la consulta.
- **Versionado de Esquema (.lancedb/meta.json)**: Control de versionado SemVer (`SCHEMA_VERSION = "5.0.0"`) gestionado por `services/meta.py`. Detección automática de re-ingesta forzada ante incompatibilidades o actualizaciones del modelo Pydantic `CodeChunk` (vectores de 896 dimensiones).
- **Mitigación de Bloqueos Concurrentes**: Reintentos con esperas de backoff exponencial en `get_db_connection()` para evitar fallos de *File Lock* durante escrituras concurrentes.
- **Local Embeddings & Reranker (896D)**: Inferencia 100% local y offline usando ONNX Runtime GPU (CUDA FP32) con `jinaai/jina-code-embeddings-0.5b` (896D) con prefijos de tarea (`nl2code_document` / `nl2code_query`) y `onnx-community/bge-reranker-v2-m3-ONNX` en GPU Pascal (GTX 1080 Ti). El daemon aplica controles de memoria activos (`memory.enable_memory_arena_shrinkage`, `gpu_mem_limit=4GB`, `DAEMON_EMBED_BATCH_SIZE=8`), manteniendo la VRAM estabilizada en ~3.11-4.8 GB.
- **Worker Daemon Global y FastMCP Streamable HTTP**: 
  - `rag-daemon`: Proceso HTTP en background (puerto 21239) que mantiene los modelos cargados en VRAM.
  - `rag-mcp`: Servidor FastMCP Streamable HTTP (puerto 8000 en `/mcp`) que responde con latencia <5ms.
  - **Docker**: Dos volúmenes limpios: `rag_models` (`/app/models`) para binarios de IA y `rag_deps_db` (`/app/.cache/rag-local/dependencies`) para contratos de librerías.
- **Gemini API**: Usada exclusivamente para la generación de respuestas contextualizadas mediante LLM (`gemini-2.5-flash`).
- **Entrada**: Archivos de código (TypeScript, TSX, JSX, Prisma, HTML, CSS, Python).
- **Procesamiento**:
  1. `scan_files()`: Escaneo recursivo de directorios aplicando filtros de `.gitignore` del proyecto de manera automática.
  2. `detect_project_roots()`: Detección inteligente de raíces de frameworks (`angular.json`, `nest-cli.json`, `pyproject.toml` y `next.config.ts/js/mjs`) para determinar scopes dinámicos (`angular`, `nestjs`, `python`, `nextjs-app`).
  3. `chunk_file()`: Chunking sintáctico de archivos mediante `tree-sitter` y parsers dedicados (`css.py`, `html.py`, `prisma.py`). Extrae y serializa reglas CSS (`css_rules`), jerarquía de clases DOM (`class_parents`) y volumen de líneas (`lines_code`).
  4. `index_chunks()`: Generación local y offline de embeddings e inserción incremental en LanceDB con soporte de reindexación forzada (`--force`) o autodetección por versión de esquema.
- **Consulta**:
  1. `query_db()`: Recuperación inicial de fragmentos semánticamente similares en LanceDB con filtrado dinámico de scope (delegando embeddings al daemon en ~15ms).
  2. **Re-ranking con filtro de relevancia**: Reordenamiento local mediante `rerankers` (delegando al daemon en ~35ms). Los chunks con score inferior a `MIN_RERANK_SCORE` (-2.0 en logits raw) se descartan automáticamente como irrelevantes.
  3. **Refusal explícito**: Si ningún chunk supera el threshold, el sistema retorna `NO_CONTEXT: ...` en vez de contexto vacío o ruido.
  4. Fusión de fragmentos adyacentes del mismo archivo y formateo final en bloques XML estructurados.
- **Clasificación de Herramientas y Bases de Datos**:
  - **Base de Datos del Monorepo / Proyecto (`<workspace>/.lancedb/`)**:
    - `query_codebase()`: Búsqueda semántica (embeddings) y texto completo FTS sobre el código fuente del proyecto.
    - `get_project_map()`: Mapa estructural de clases, servicios, funciones y modelos indexados en el proyecto.
    - `trace_event_flow()`: Trazabilidad de ciclo de vida completo de eventos backend ↔ frontend y sus schemas.
    - `get_styles_map()`: Trazabilidad Componente ↔ CSS, clases y variables consultadas desde metadatos `css_rules` (0 lecturas a disco).
    - `get_code_metrics()`: Cálculo de métricas de código (LOC) consultadas desde `lines_code` (0 lecturas a disco).
    - `audit_layout_risks()`: Auditoría estática de layout responsivo consultando metadatos `css_rules` y `class_parents` (0 lecturas a disco).
    - `ingest_codebase()`: Indexación y chunking sintáctico incremental del código fuente del proyecto.
  - **Base de Datos Global de Dependencias (`~/.cache/rag-local/dependencies/`)**:
    - `query_dependency()`: Consulta de contratos, firmas, interfaces y docstrings de librerías de terceros (0 lecturas a disco).
    - `ingest_dependencies()` / CLI `rag-ingest-deps`: Extracción de tipos (`.pyi` / `.d.ts`) e indexación a la caché global.
    - `manage_dependencies()` / CLI `rag-deps`: Administración, estado, consulta (`status`, `query`, `remove`, `clean`) de la caché global.
  - **Herramientas de Configuración e Infraestructura Global**:
    - `get_config()`: Inspección de rutas del entorno, versión de esquema SemVer y resumen sintético del estado del índice en 5 líneas.
    - `manage_daemon()` / CLI `rag-daemon`: Control de ciclo de vida (start / stop / status / health) del Worker Daemon de inferencia local en GPU VRAM.
- **Soporte Multiproyecto**: Soporte dinámico para trabajar con múltiples repositorios de forma aislada. En tiempo de ejecución, el servidor MCP muta `config.REPO_ROOT` y `config.LANCEDB_PATH` en función del parámetro `project_path` provisto por las herramientas, encapsulando y aislando el índice vectorial en el subdirectorio `.lancedb/` de cada repositorio destino. Las dependencias externas se desacoplan a nivel de usuario en `~/.cache/rag-local/dependencies/` para reutilización multi-proyecto (0.0s).

## Code Layout
- `src/rag_local/`:
  - `daemon/`: Paquete del Worker Daemon (`server.py`, `lifecycle.py`, `port_file.py`, `client.py`, `__main__.py`).
  - `cli/daemon.py`: Comando CLI `rag-daemon` para control del daemon (`start`, `stop`, `status`).
  - `cli/ingest.py`: Comando CLI `rag-ingest` para indexar archivos con soporte para `--force` / `-f` y autodetección `[AUTO-FORCE]`.
  - `cli/ingest_deps.py`: Comando CLI `rag-ingest-deps` para indexar contratos de dependencias en la caché global de LanceDB.
  - `cli/dependencies.py`: Comando CLI `rag-deps` para consultar, administrar y limpiar la caché global de dependencias.
  - `cli/query.py`: Comando CLI `rag-query` para consultar al RAG de forma humana o vía JSON.
  - `cli/styles.py`: Comando CLI `rag-styles` para auditar el mapa de estilos y clases obsoletas.
  - `cli/style_audit.py`: Comando CLI `rag-style-audit` para auditoría estática de antipatrones de layout CSS.
  - `cli/metrics.py`: Comando CLI `rag-loc` para calcular métricas de código (LOC) desde LanceDB.
  - `cli/config.py`: Comando CLI `rag-config` para obtener el estado del repositorio, índice, versión de esquema y daemon.
  - `cli/event_flow.py`: Comando CLI `rag-events` para rastreo de flujos de eventos entre backend y frontend.
  - `mcp/`: Servidor MCP (`rag-mcp`) estructurado en herramientas modulares (`tools/query.py`, `tools/dependencies.py`, `tools/ingest.py`, `tools/config.py`, `tools/project_map.py`, `tools/event_flow.py`, `tools/styles.py`, `tools/style_audit.py`, `tools/metrics.py`, `tools/daemon.py`) que ejecutan subprocesos CLI aislados vía `sys.executable -m rag_local.cli.<modulo>`.
  - `core/config.py`: Gestión estructurada de configuraciones, variables de entorno, constantes del daemon y `SCHEMA_VERSION`.
  - `core/logging.py`: Configuración del sistema de logs con formato enriched.
  - `parsers/`: Módulos de análisis sintáctico. `typescript/`, `html.py`, `prisma.py` y `css.py`.
  - `services/db.py`: Wrapper de LanceDB, reintentos con backoff exponencial, hashes y caché de ingesta.
  - `services/db_schemas.py`: Modelos de datos LanceDB (`CodeChunk`, `CodeRelationship`, `DependencySymbol`).
  - `services/dependencies/`: Paquete modular de gestión de dependencias externas (`db.py`, `detector.py`, `extractor_py.py`, `extractor_ts.py`, `sync.py`, `query.py`, `cleaner.py`).
  - `services/meta.py`: Servicio de metadatos del índice (`.lancedb/meta.json`) y validación de `SCHEMA_VERSION`.
  - `services/styles.py`: Servicio de trazabilidad de estilos CSS desde LanceDB.
  - `services/style_audit/`: Paquete modular de auditoría estática de layout responsivo desde LanceDB (`context.py`, `models.py`, `formatter.py` y `evaluators/` con `flexbox.py`, `responsive.py`, `stacking.py`, `syntax.py`, `text.py`, `common.py`).
  - `services/metrics.py`: Servicio de cálculo de métricas de código (LOC) desde LanceDB.
  - `services/project_map.py`: Lector de metadatos LanceDB que genera un mapa estructural del proyecto.
  - `services/scanner.py`: Lógica de detección de frameworks, carga y análisis de `.gitignore`, escaneo recursivo.
  - `services/embeddings.py`: Servicio de embeddings locales con delegación transparente al Worker Daemon.
  - `services/gemini.py`: Cliente de generación de contenido LLM basado en Google GenAI con fallbacks secuenciales.
  - `services/rag.py`: Flujo de consulta RAG, aplicación del Reranker (con delegación al daemon), fusión de bloques y formateo XML.
  - `services/freshness.py`: Módulo centralizado de auto-ingesta y resolución segura de repositorios para todos los comandos CLI y herramientas MCP.
  - `services/project.py`: Configuración dinámica de directorios del monorepo y aislamiento de rutas seguras.
  - `services/subprocess.py`: Ejecutor reutilizable de subprocesos de consola asíncronos con callbacks de progreso y captura de `[AUTO-SYNC]`.

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
| M15 | Versionado de Esquema y Consulta 100% LanceDB | `SCHEMA_VERSION` SemVer en `.lancedb/meta.json`, ejecución 100% LanceDB sin lecturas a disco durante queries, CLI `rag-config`, optimización `sys.executable` y reintentos con backoff | M14 | COMPLETED |
| M16 | Fast Pre-Query Check (Auto-Sync) | Refresco express pre-query (`fast_sync.py`) comprobando `SCHEMA_VERSION` y `mtime` en ~10ms, sincronizando deltas o forzando re-ingesta limpia si cambia el esquema antes de ejecutar consultas o auditorías | M15 | COMPLETED |
| M17 | Model Worker Daemon (VRAM/RAM) | Servidor HTTP en background (`rag-daemon`) con precarga de modelos en VRAM (~1.1 GB), token de seguridad, parent PID tracking, grace period (15s), idle timeout (30m) y herramienta MCP `manage_daemon` | M16 | COMPLETED |
| M18 | Global Daemon & Auto-Sync Header | Desacoplamiento global del daemon con `platformdirs` (`~/.rag-local/`), centralización de auto-ingesta en `services/freshness.py`, incremento de timeout de arranque (60s), tiempo activo `mm:ss` y encabezado `[Auto-Sync]` en respuestas MCP | M17 | COMPLETED |
| M19 | Universal Map, Streaming IPC & Watchdog | Mapa de proyecto por símbolos universales y módulos compactos (`--scope`, `--full-tree`), protocolo IPC estructurado con Pydantic V2 (`core/events.py`), temporizador dinámico con watchdog de inactividad de 10m y exclusión por defecto de `vendor/`, `third_party/`, cachés y minificados (`SCHEMA_VERSION = 1.3.0`) | M18 | COMPLETED |
| M20 | Optimización Definitiva CSS Audit & LanceDB V2 | Precomputación de jerarquías DOM (`class_parents`) en ingesta (0 lecturas a disco en auditoría), timeout de seguridad y depth guard en parser CSS, y corrección de falsos positivos en layout (`SCHEMA_VERSION = 2.0.0`) | M19 | COMPLETED |
| M21 | Modularización de Style Audit & Calibración de Tokens | Descomposición de `evaluators.py` en submódulos especializados (<365 líneas), salida en inglés optimizada para tokens de agentes, calibración contra falsos positivos en Stacking Context Trap (popovers/micro-UI) y Flex Wrap (truncamiento elástico) | M20 | COMPLETED |
| M22 | Caché Global de Dependencias & query_dependency | Almacenamiento desacoplado en caché global (`~/.cache/rag-local/dependencies/`), ingesta selectiva (`rag-ingest-deps`), administración CLI (`rag-deps`), aislamiento por lenguaje y herramienta MCP `query_dependency` | M21 | COMPLETED |
| M23 | Dockerización GPU, ONNX Runtime FP16 & Stat Cache | Contenedorización OCI con CUDA 12.6 + cuDNN 9, migración a ONNX Runtime FP16 con `kSameAsRequested`, gestor de bloqueos cross-platform (`locks.py`), optimización de escaneo con `os.scandir()`, Stat Cache (`mtime + size + hash`) y procesamiento paralelo con `ThreadPoolExecutor` | M22 | COMPLETED |
| M24 | Modernización v4.0.0, BGE-M3 (1024D) & Xet Storage | Migración a BGE-M3 (1024d) y BGE-Reranker-v2-M3 ONNX, aceleración con hf-xet, compatibilidad gitwildmatch con pathspec, chunking jerárquico AST en funciones Python largas y deduplicación de metadatos CSS (`SCHEMA_VERSION = 4.0.0`) | M23 | COMPLETED |

## Interface Contracts
### `services.db.chunk_file` ↔ `cli.ingest`
- **Signature**: `chunk_file(file_path: Path) -> list[dict[str, Any]]`
- **Output format**: Cada dict en la lista contiene:
  - `text`: str (contenido completo del fragmento sintáctico sin cortes de bloque)
  - `start_line`: int
  - `end_line`: int
  - `metadata`: dict (con claves `class_name`, `method_name`, `imports`, `dependencies`, `models` adicionales según aplique al parser)
