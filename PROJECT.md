# Project: RAG Local Optimization

## Architecture
El proyecto `rag-local` es una herramienta de línea de comandos (CLI) en Python para realizar RAG local sobre repositorios de código.

- **Requisito de GPU**: El proyecto requiere una GPU NVIDIA con CUDA (>=6 GB VRAM). No existe fallback a CPU. GPU de referencia: GTX 1080 Ti (11 GB). Los valores de protección de VRAM del daemon son configurables en `core/config.py`.
- **LanceDB**: Base de datos vectorial embebida local utilizada para persistir las representaciones vectoriales y metadatos extendidos del código (`lines_code`, `css_rules`).
- **Ejecución 100% desde LanceDB (0 Lecturas a Disco)**: Las herramientas `get_code_metrics()`, `get_styles_map()`, `audit_layout_risks()` y `get_project_map()` leen directamente los metadatos estructurados desde LanceDB sin acceder al sistema de archivos en disco durante la consulta.
- **Versionado de Esquema (.lancedb/meta.json)**: Control de versionado SemVer (`SCHEMA_VERSION = "1.0.0"`) gestionado por `services/meta.py`. Detección automática de re-ingesta forzada ante incompatibilidades o actualizaciones del modelo Pydantic `CodeChunk`.
- **Mitigación de Bloqueos Concurrentes**: Reintentos con esperas de backoff exponencial en `get_db_connection()` para evitar fallos de *File Lock* durante escrituras concurrentes.
- **Local Embeddings & Reranker**: Inferencia 100% local y offline usando `sentence-transformers` (`Alibaba-NLP/gte-multilingual-base`, 768D) y `rerankers` (`BAAI/bge-reranker-base`). Soporta modo Standalone (carga efímera de ~550 MB con liberación total a 0 MB) y modo Daemon (precarga permanente en VRAM), compartiendo un límite unificado de reserva del 72% de VRAM (`DAEMON_VRAM_FRACTION=0.72`), auto-reducción de batch size y purga de caché post-lote.
- **Worker Daemon Global (Precarga en VRAM)**: Proceso HTTP en background (`rag-daemon`) global a nivel de usuario (`~/.rag-local/daemon.json` gestionado con `platformdirs`) que mantiene ambos modelos cargados en VRAM (~2.5-3.0 GB en CUDA), eliminando la penalización de carga de disco (~1.6s) y reduciendo la latencia de queries a **~0.05s**. Requiere GPU NVIDIA con CUDA. Cuenta con protección de VRAM configurable (`DAEMON_VRAM_FRACTION=0.72`, `DAEMON_VRAM_PRESSURE_THRESHOLD_MB=500`), auto-recuperación de OOM, parent PID tracking (Windows), idle timeout (30m), grace period (15s), token de seguridad y timeout de arranque configurable (`DAEMON_STARTUP_TIMEOUT = 120.0`).
- **Gemini API**: Usada exclusivamente para la generación de respuestas contextualizadas mediante LLM (`gemini-2.5-flash`).
- **Entrada**: Archivos de código (TypeScript, TSX, JSX, Prisma, HTML, CSS, Python).
- **Procesamiento**:
  1. `scan_files()`: Escaneo recursivo de directorios aplicando filtros de `.gitignore` del proyecto de manera automática.
  2. `detect_project_roots()`: Detección inteligente de raíces de frameworks (`angular.json`, `nest-cli.json`, `pyproject.toml` y `next.config.ts/js/mjs`) para determinar scopes dinámicos (`angular`, `nestjs`, `python`, `nextjs-app`).
  3. `chunk_file()`: Chunking sintáctico de archivos mediante `tree-sitter` y parsers dedicados (`css.py`, `html.py`, `prisma.py`). Extrae y serializa reglas CSS (`css_rules`) y volumen de líneas (`lines_code`).
  4. `index_chunks()`: Generación local y offline de embeddings e inserción incremental en LanceDB con soporte de reindexación forzada (`--force`) o autodetección por versión de esquema.
- **Consulta**:
  1. `query_db()`: Recuperación inicial de fragmentos semánticamente similares en LanceDB con filtrado dinámico de scope (delegando embeddings al daemon en ~15ms).
  2. **Re-ranking con filtro de relevancia**: Reordenamiento local mediante `rerankers` (delegando al daemon en ~35ms). Los chunks con score inferior a `MIN_RERANK_SCORE` (-2.0 en logits raw) se descartan automáticamente como irrelevantes.
  3. **Refusal explícito**: Si ningún chunk supera el threshold, el sistema retorna `NO_CONTEXT: ...` en vez de contexto vacío o ruido.
  4. Fusión de fragmentos adyacentes del mismo archivo y formateo final en bloques XML estructurados.
- **Clasificación de Herramientas según Dependencia de LanceDB**:
  - **Requieren LanceDB (`ingest_codebase`)**:
    - `query_codebase()`: Búsqueda semántica (embeddings) y texto completo FTS sobre los vectores del índice `.lancedb/`.
    - `get_project_map()`: Extracción de metadatos de clases, servicios y modelos Prisma indexados en LanceDB.
    - `trace_event_flow()`: Trazabilidad de ciclo de vida completo de eventos backend ↔ frontend desde metadatos en LanceDB.
    - `get_styles_map()`: Trazabilidad Componente ↔ CSS, clases y variables consultadas desde metadatos `css_rules` en LanceDB (0 lecturas a disco).
    - `get_code_metrics()`: Cálculo de métricas de código (LOC) consultadas desde `lines_code` en LanceDB (0 lecturas a disco).
    - `audit_layout_risks()`: Auditoría estática de layout responsivo consultando metadatos `css_rules` en LanceDB (0 lecturas a disco).
  - **NO Requieren LanceDB (Análisis Directo y Gestión)**:
    - `get_config()`: Inspección de rutas del entorno, versión de esquema SemVer y resumen sintético del estado del índice en 5 líneas.
    - `manage_daemon()`: Control de ciclo de vida (start / stop / status) del Worker Daemon de inferencia local.
- **Soporte Multiproyecto**: Soporte dinámico para trabajar con múltiples repositorios de forma aislada. En tiempo de ejecución, el servidor MCP muta `config.REPO_ROOT` y `config.LANCEDB_PATH` en función del parámetro `project_path` provisto por las herramientas, encapsulando y aislando el índice vectorial en el subdirectorio `.lancedb/` de cada repositorio destino. El CLI asume de forma predeterminada el directorio actual (CWD) si no se configuran variables de entorno.

## Code Layout
- `src/rag_local/`:
  - `daemon/`: Paquete del Worker Daemon (`server.py`, `lifecycle.py`, `port_file.py`, `client.py`, `__main__.py`).
  - `cli/daemon.py`: Comando CLI `rag-daemon` para control del daemon (`start`, `stop`, `status`).
  - `cli/ingest.py`: Comando CLI `rag-ingest` para indexar archivos con soporte para `--force` / `-f` y autodetección `[AUTO-FORCE]`.
  - `cli/query.py`: Comando CLI `rag-query` para consultar al RAG de forma humana o vía JSON.
  - `cli/styles.py`: Comando CLI `rag-styles` para auditar el mapa de estilos y clases obsoletas.
  - `cli/style_audit.py`: Comando CLI `rag-style-audit` para auditoría estática de antipatrones de layout CSS.
  - `cli/metrics.py`: Comando CLI `rag-loc` para calcular métricas de código (LOC) desde LanceDB.
  - `cli/config.py`: Comando CLI `rag-config` para obtener el estado del repositorio, índice, versión de esquema y daemon.
  - `cli/event_flow.py`: Comando CLI `rag-events` para rastreo de flujos de eventos entre backend y frontend.
  - `mcp/`: Servidor MCP (`rag-mcp`) estructurado en herramientas modulares (`tools/query.py`, `tools/ingest.py`, `tools/config.py`, `tools/project_map.py`, `tools/event_flow.py`, `tools/styles.py`, `tools/style_audit.py`, `tools/metrics.py`, `tools/daemon.py`) que ejecutan subprocesos CLI aislados vía `sys.executable -m rag_local.cli.<modulo>`.
  - `core/config.py`: Gestión estructurada de configuraciones, variables de entorno, constantes del daemon y `SCHEMA_VERSION`.
  - `core/logging.py`: Configuración del sistema de logs con formato enriched.
  - `parsers/`: Módulos de análisis sintáctico. `typescript/`, `html.py`, `prisma.py` y `css.py`.
  - `services/db.py`: Wrapper de LanceDB, reintentos con backoff exponencial, hashes y caché de ingesta.
  - `services/meta.py`: Servicio de metadatos del índice (`.lancedb/meta.json`) y validación de `SCHEMA_VERSION`.
  - `services/styles.py`: Servicio de trazabilidad de estilos CSS desde LanceDB.
  - `services/style_audit.py`: Servicio de auditoría estática de layout responsivo desde LanceDB.
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

## Interface Contracts
### `services.db.chunk_file` ↔ `cli.ingest`
- **Signature**: `chunk_file(file_path: Path) -> list[dict[str, Any]]`
- **Output format**: Cada dict en la lista contiene:
  - `text`: str (contenido completo del fragmento sintáctico sin cortes de bloque)
  - `start_line`: int
  - `end_line`: int
  - `metadata`: dict (con claves `class_name`, `method_name`, `imports`, `dependencies`, `models` adicionales según aplique al parser)
