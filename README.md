# RAG Local Optimization

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Type Checker: Pyrefly](https://img.shields.io/badge/type%20checker-pyrefly-blueviolet)](https://pyrefly.org/)
[![Package Manager: uv](https://img.shields.io/badge/package%20manager-uv-de5b43)](https://docs.astral.sh/uv/)

Sistema RAG local de alto rendimiento y bajo consumo de tokens optimizado para analizar monorepos de código estructurados en Angular 21, NestJS 11 + Fastify, Next.js 16 (React 19) y Prisma.

El objetivo principal de esta herramienta es proveer búsquedas de contexto sumamente precisas a agentes de IA, reduciendo significativamente el ruido (eliminando líneas de código cortadas a la mitad) y optimizando el consumo de tokens mediante la fusión inteligente de fragmentos adyacentes, reordenamiento de relevancia y formateo XML estructurado.

---

## Requisitos de Hardware

- **GPU NVIDIA con CUDA** (obligatorio). No existe fallback a CPU.
- **VRAM mínima recomendada**: 6 GB. El daemon mantiene ~2.5-3 GB de modelos cargados permanentemente.
- **GPU de referencia**: NVIDIA GTX 1080 Ti (11 GB). Los valores de configuración de VRAM (`DAEMON_VRAM_FRACTION`, `DAEMON_VRAM_PRESSURE_THRESHOLD_MB`) están calibrados para esta GPU pero son ajustables en `config.py` para otras GPUs NVIDIA.
- **Sistema Operativo**: Windows (soporte nativo de WDDM, `pythonw.exe` para daemon invisible, `DETACHED_PROCESS`).

---

## Caracteristicas Claves

### 1. Chunking Sintactico con Tree-Sitter
- **TypeScript & TSX**: Utiliza el parser sintáctico de `tree-sitter-typescript` para agrupar clases, componentes React/Next.js (`.tsx`, `.jsx`), decoradores y funciones reducer extensas (`switch(action.type)`). Segmenta casos quirúrgicos (`case '...'`) inyectando imports y cabeceras contextuales para mantener precisión máxima.
- **Python**: Agrupa clases (desempaquetando decoradores como `@dataclass` o `@router`), funciones y detecta automáticamente etiquetas de eventos (SocketIO, EventBus, `*Event(...)`, `EVENT_TYPE_MAP`).
- **HTML**: Utiliza el parser de `tree-sitter-html` para agrupar etiquetas jerárquicas y previene cortes a la mitad de tags.
- **Prisma**: Divide esquemas por bloques funcionales (`model`, `enum`, `datasource`, etc.) y extrae dependencias entre tablas.
- **Bloque Unico**: Si un archivo es menor a 50 líneas, se procesa como un único bloque para conservar el contexto completo.

### 2. Persistencia y Búsqueda Híbrida (LanceDB)
- **Búsqueda Híbrida**: Combina la similitud semántica (vectores) con búsqueda de texto completo (FTS/BM25) indexando la columna `text`. Esto garantiza encontrar términos de código exactos (variables o firmas de métodos).
- **Refresco Automático Express (`fast_sync.py`)**: Antes de cada consulta o auditoría, el RAG realiza un chequeo express en **~10ms** validando la versión de esquema SemVer (`SCHEMA_VERSION`) y la fecha de modificación de archivos (`mtime`). Si detecta un esquema desactualizado ejecuta una re-ingesta forzada limpia (`force=True`); si detecta archivos editados por un usuario o agente, sincroniza automáticamente los deltas en LanceDB en ~150ms antes de responder.
- **Ingesta Incremental Basada en Cache**: Almacena hashes SHA256 para evitar re-indexar archivos sin cambios, eliminando chunks obsoletos de forma automática.
- **Embeddings Locales Offline**: Utiliza PyTorch y `sentence-transformers` para generar representaciones vectoriales de forma local y offline en GPU (CUDA). Cuenta con dos modos de operación protegidos:
  - **Modo Standalone (sin daemon)**: Carga en memoria exclusivamente el modelo de embeddings (~550 MB en VRAM). Al concluir el comando CLI, el subproceso se destruye liberando el 100% de la memoria GPU a 0 MB.
  - **Modo Worker Daemon (global)**: Mantiene los modelos de embeddings y reranker precargados (~2.5-3.0 GB en VRAM) para consultas ultra-rápidas en ~0.05s.
  - **Protección VRAM Unificada**: Ambos modos aplican un límite estricto de reserva del 72% (`DAEMON_VRAM_FRACTION=0.72`, ~8.0 GB en GPU de 11 GB dejando ~3.1 GB libres para Windows y apps), auto-recuperación de OOM con reducción de batch size y liberación inmediata de tensores tras cada lote (`gc.collect()` + `torch.cuda.empty_cache()`).
- **Inferencia GPU (CUDA)**: Ejecución exclusiva en GPU NVIDIA con CUDA. Requiere una GPU con >=6 GB de VRAM.
- **Índices Escalares BTREE**: Habilita de forma automática índices de tipo `BTREE` en las columnas `scope` y `source` para acelerar de forma drástica búsquedas filtradas y eliminaciones.
- **Compactación y Limpieza**: Al finalizar la ingesta de fragmentos nuevos o modificados, ejecuta `table.optimize()` para reducir la fragmentación en disco, purgar versiones obsoletas y actualizar los índices.

### 3. Re-ranking y Filtro de Relevancia (GPU)
- Incorpora una capa de re-ranking con la librería `rerankers` utilizando el modelo `BAAI/bge-reranker-base`.
- Permite recuperar un número alto de chunks candidatos en la búsqueda inicial y reducirlos al subconjunto verdaderamente relevante antes de enviarlo a Gemini.
- **Filtro post-rerank**: Cada chunk recibe un score en logits raw (rango ~[-11, +11]). Los chunks con score inferior a `MIN_RERANK_SCORE` (por defecto `-2.0`) se descartan automáticamente como irrelevantes.
- **Refusal explícito**: Si ningún chunk supera el threshold tras el filtro, el tool MCP retorna `NO_CONTEXT: ...` — un marcador claro para que el agente no fabrique una respuesta basada en conocimiento general.
- Ejecución exclusiva en GPU NVIDIA (CUDA) para máximo rendimiento en inferencia local.

### 4. Enriquecimiento de Contexto Relacional (Graph-RAG)
- **TypeScript & NestJS**: Analiza imports de dependencias locales e inyecta fragmentos importados dentro de etiquetas `<imported_file path="..." relation_type="...">`.
- **Prisma**: Identifica relaciones directas (`@relation` y tablas asociadas) e inyecta los modelos vinculados en etiquetas `<related_model name="...">`.
- **Soporte de Gitignores Múltiples**: Escaneo recursivo que hereda y combina de forma automática las exclusiones de todos los archivos `.gitignore` anidados en subcarpetas.

### 5. Mapa Estructural Universal y Trazabilidad de Eventos (`get_project_map` / `trace_event_flow`)
- **Mapa Estructural Universal (`get_project_map`)**: Extrae y organiza símbolos universales de código (`Classes`, `Functions`, `Models`, `Interfaces`, `Events`) agrupados por módulos/carpetas en modo compacto de alta densidad informativa. Soporta filtrado por framework (`--scope`), filtrado granular por directorio o subsistema (`--path-filter`) y visualización de árbol completo (`--full-tree`).
- **Trazabilidad de Flujo de Eventos (`trace_event_flow`)**: Mapea el ciclo de vida completo de extremo a extremo:
  `Definición Backend -> Emisor Backend -> WebSocket Handler -> Reducer Frontend -> Componente/Configuración UI`.
  Soporta filtros por evento o patrones wildcard (`follower_*`), filtro por entidad/dominio (`entity="user"`), exposición estructurada de contratos de datos (`Schema: { user_id: str, ... }`) persistidos en LanceDB, y paginación con límite configurable (`limit`).
- Zero costo adicional: reutiliza los metadatos extraídos durante la ingesta (`class_name`, `tags`, `method_name`, `type`, `models`, `scope`, `source`, `payload_schema`).

### 6. Trazabilidad CSS, Auditoría y Métricas 100% LanceDB (`get_styles_map` / `audit_layout_risks` / `get_code_metrics`)
- **Ejecución 100% desde LanceDB (0 Lecturas a Disco)**: Todas las consultas de estilos, auditoría responsiva y volumen de líneas de código (LOC) leen directamente los metadatos almacenados (`css_rules`, `class_parents`, `lines_code`) en LanceDB sin acceder a archivos del disco durante la consulta.
- **Trazabilidad Componente ↔ CSS (`get_styles_map`)**: Mapea componentes UI (`.js`, `.jsx`, `.tsx`, `.html`, etc.) con sus reglas CSS correspondientes mediante `tree-sitter-css`, entregando rango de líneas exacto (`L462-469`), selector completo, directivas `@media` y mapa de propiedades (`flex`, `min-width`, `word-break`). Excluye automáticamente etiquetas de arquitectura (`action:...`, `event:...`) y permite filtrar por componente (`component_filter`), clase (`class_filter`) o propiedad (`property_filter`).
- **Auditoría Estática de Riesgos Layout (`audit_layout_risks`)**: Detecta antipatrones de diseño responsivo y jerarquía visual clasificados por gravedad (`CRITICAL`, `WARNING`, `INFO`), emitiendo diagnósticos en inglés conciso optimizados para el consumo de tokens en agentes de IA:
  - **Trampas de Contexto de Apilamiento (*Stacking Context Trap*)**: Detecta elementos `fixed`/`sticky` (drawers, modales) atrapados dentro de contenedores aislados (`isolation: isolate`, `transform`, `filter`, `contain`), excluyendo automáticamente sub-elementos internos, micro-UI y popovers con auto-aislamiento.
  - **Inversión de Jerarquía de Z-Index**: Identifica inconsistencias en la escala lógica de variables globales de capas (`Toast > Modal > Popover > Drawer > Header`).
  - **Riesgos de Scroll en Flex Column**: Identifica contenedores flex verticales scrollables que carecen de `min-height: 0`, con mitigación inteligente para vistas con altura explícita (`height: 100%`).
  - **Inconsistencia de Breakpoints Responsivos**: Detecta discrepancias sutiles entre archivos CSS (ej. `576px` vs `600px`).
  - **Modales en Pantallas de Baja Altura (*Landscape*)**: Detecta backdrops centrados verticalmente que carecen de `overflow-y: auto`.
  - **Ruptura de Texto y Flexbox Overflow**: Analiza fallos flexbox sin `min-width: 0`, desbordamientos de texto y omite falsos positivos ante patrones de truncamiento elástico (`ellipsis`, `is_break_protected`) en hijos del contenedor. Aplica validación cruzada con la jerarquía DOM precalculada en `class_parents` para detectar mitigación por ancestros. Filtra automáticamente micro-UI, pseudo-clases y resets.

### 7. Versionado de Esquema, Protocolo IPC Tipado y Watchdog Dinámico
- **Versionado SemVer Automatizado (`SCHEMA_VERSION = 3.0.0`)**: `SCHEMA_VERSION` en `config.py` y `.lancedb/meta.json` valida la compatibilidad del índice y activa auto-sincronización o re-ingesta limpia ante cambios estructurales.
- **Protocolo de Streaming IPC Tipado (`core/events.py`)**: Comunicación no bloqueante entre subprocesos CLI y el servidor MCP basada en `Pydantic V2` y `SyncPhase` Enum (`START`, `PROGRESS`, `COMPLETED`, `ERROR`), eliminando el raspado frágil de texto libre en consola.
- **Temporizador Dinámico y Watchdog de Inactividad**: Comandos de consulta disponen de un límite estándar de **3 minutos**. Al activarse sincronización/ingesta, el timeout se anula pasando a un **watchdog de inactividad (10 minutos entre lotes)**; al concluir la sincronización, el cronómetro **se restablece a 3 minutos limpios** para la consulta principal.
- **Filtros de Exclusión Nativos**: Escaneo ignora automáticamente reglas de `.gitignore`, carpetas de dependencias y cachés (`vendor/`, `third_party/`, `.venv/`, `__pycache__/`, `.ruff_cache/`, `node_modules/`, `dist/`) y archivos minificados (`*.min.js`, `*.min.css`, `*.bundle.js`).
- **Aislamiento por Subproceso (`sys.executable`)**: Las herramientas MCP ejecutan comandos CLI como subprocesos aislados usando `[sys.executable, "-m", "rag_local.cli.<modulo>", ...]`, eliminando cuellos de botella e incompatibilidades en Windows.

### 8. Optimizacion de Tokens y Contexto para Agentes
- **Referencias de Líneas Explícitas (`[archivo.py:Lstart-Lend]`)**: Entrega en la cabecera de resultados las referencias delimitadas y exactas por archivo y rango de líneas, permitiendo edición directa con herramientas como `replace_file_content` sin pasos intermedios.
- **Fusion de Chunks y Expansión Enclosing Scope (`full_block`)**: Chunks adyacentes o solapados del mismo archivo se fusionan en un único fragmento continuo. Con `--full-block`, reconstruye directamente desde LanceDB el bloque contenedor de funciones o métodos para dar visibilidad completa sin lecturas a disco.
- **Estructura XML Limpia**: Contexto formateado mediante bloques XML estructurados (`<context>`, `<file path="...">`), facilitando la lectura a agentes LLM.
- **Seguridad**: Escape estricto de caracteres especiales (`&`, `<`, `>`, `"`, `'`) en el código y en las consultas para mitigar inyecciones de prompts.
- **Truncado Seguro**: Si el contexto excede 15,000 caracteres, se trunca limpiamente con un indicador `[TRUNCATED]`.
- **Múltiples Fallbacks de Generación**: En caso de fallas o saturación de límites en el modelo principal de generación (`gemini-2.5-flash`), realiza de forma transparente un fallback secuencial a modelos secundarios (`gemini-3.5-flash`, `gemini-3-flash`, `gemini-3.1-flash-lite` y `gemini-2.5-flash-lite`).

### 9. Worker Daemon de Inferencia Local (Precarga en VRAM)
- **Precarga en VRAM (~2.5-3.0 GB)**: Mantiene los modelos `Alibaba-NLP/gte-multilingual-base` y `BAAI/bge-reranker-base` cargados permanentemente en la VRAM de la GPU NVIDIA, eliminando el tiempo de carga desde disco (~1.6s) en cada ejecución y reduciendo la latencia de queries a **~0.05s**. Incluye protección de VRAM configurable (`DAEMON_VRAM_FRACTION`, `DAEMON_VRAM_PRESSURE_THRESHOLD_MB`) y auto-recuperación de OOM con reducción dinámica de batch_size.
- **Gestión Inteligente de Ciclo de Vida**: Monitorea el PID del proceso padre (agente/IDE) en Windows, incluye un *Grace Period* de 15 segundos para sobrevivir a reinicios/refrescos del IDE, y un *Idle Timeout* de 30 minutos tras el cual se apaga de forma autónoma.
- **Seguridad y Aislamiento**: Escucha exclusivamente en `127.0.0.1`, protegido con token criptográfico `Bearer` generado aleatoriamente en cada inicio y validación estricta de cabecera `Host` contra DNS Rebinding.

### 10. Caché Global de Dependencias en LanceDB (`query_dependency` / `rag-ingest-deps` / `rag-deps`)
- **Almacenamiento Desacoplado**: Persiste contratos de tipos (`.pyi` / `.d.ts`), constructores, interfaces y docstrings de paquetes de terceros en la caché global del usuario (`~/.cache/rag-local/dependencies/`), aislando físicamente el índice de librerías del código del proyecto.
- **Reutilización Multi-Proyecto con Costo Cero**: Una librería externa se indexa una sola vez a nivel de sistema; proyectos posteriores que compartan la misma versión reutilizan la caché con tiempo de ingesta de **0.0 segundos**.
- **Aislamiento por Lenguaje (Namespaces)**: Estructura de claves primarias prefijada por ecosistema (`python:pkg@ver` vs `npm:pkg@ver`) que previene colisiones entre paquetes homónimos.
- **Búsqueda Híbrida Multi-Capa**: Combina resolución exacta B-Tree en sub-milisegundos ($< 1\text{ ms}$) con búsqueda semántica vectorial y FTS BM25 sobre docstrings.
- **Comandos Dedicados**: `rag-ingest-deps` para ingesta selectiva, `rag-deps` para administración/limpieza y `query_dependency` para consultas MCP de agentes.

---

## Estructura del Codigo

- `src/rag_local/`:
  - `daemon/`: Servidor HTTP del Worker Daemon (`server.py`), ciclo de vida (`lifecycle.py`), archivo de estado atómico (`port_file.py`) y cliente IPC con fallback transparente (`client.py`).
  - `cli/daemon.py`: Comando `rag-daemon` para iniciar, detener y consultar el estado del daemon.
  - `cli/ingest.py`: Comando `rag-ingest` para indexar y actualizar el repositorio en LanceDB con autodetección de esquema.
  - `cli/ingest_deps.py`: Comando `rag-ingest-deps` para indexar contratos de dependencias en la caché global.
  - `cli/dependencies.py`: Comando `rag-deps` para consultar, administrar y limpiar la caché global de dependencias.
  - `cli/query.py`: Comando `rag-query` para consultar al RAG de forma humana o vía JSON.
  - `cli/event_flow.py`: Comando `rag-event-flow` para rastrear la cadena completa de flujo de eventos.
  - `cli/project_map.py`: Comando `rag-project-map` para generar el mapa estructural por scopes y categorías.
  - `mcp/tools/dependencies.py`: Herramienta MCP `query_dependency` para consulta de tipos y contratos de dependencias.
  - `cli/styles.py`: Comando `rag-styles` para inspeccionar la trazabilidad Componente ↔ CSS y mapa de propiedades.
  - `cli/style_audit.py`: Comando `rag-style-audit` para ejecutar auditorías estáticas de layout responsivo.
  - `cli/metrics.py`: Comando `rag-loc` para calcular métricas de líneas de código (LOC) desde LanceDB.
  - `cli/config.py`: Comando `rag-config` para obtener el estado sintético del proyecto, índice, versión de esquema y daemon en tiempo real.
  - `mcp/`: Servidor MCP (`rag-mcp`) estructurado en herramientas modulares (`tools/query.py`, `tools/ingest.py`, `tools/config.py`, `tools/project_map.py`, `tools/event_flow.py`, `tools/styles.py`, `tools/style_audit.py`, `tools/metrics.py`, `tools/daemon.py`).
  - `core/config.py`: Gestión estructurada de configuraciones, variables de entorno, constantes de daemon y `SCHEMA_VERSION`.
  - `core/logging.py`: Configuración estética de logs del sistema mediante `rich`.
  - `parsers/`: Módulos de análisis sintáctico. `typescript/` (con `switch_chunker.py`), `python.py`, `html.py`, `prisma.py` y `css.py`.
  - `services/db.py`: Wrapper e implementación de colección LanceDB, reintentos con backoff exponencial, hashes de archivos y caché de ingesta.
  - `services/fast_sync.py`: Servicio de verificación express de modificación de archivos (`mtime`) y sincronización transparente de deltas.
  - `services/meta.py`: Gestión de metadatos `.lancedb/meta.json` y control de versión de esquema.
  - `services/event_flow.py`: Servicio de trazabilidad de flujo de eventos backend ↔ frontend.
  - `services/styles.py`: Servicio de trazabilidad de estilos CSS desde LanceDB.
  - `services/style_audit/`: Paquete modular de auditoría estática de layout responsivo desde LanceDB (`context.py`, `models.py`, `formatter.py` y `evaluators/` con `flexbox.py`, `responsive.py`, `stacking.py`, `syntax.py`, `text.py`, `common.py`).
  - `services/metrics.py`: Servicio de cálculo de métricas de código (LOC) desde LanceDB.
  - `services/project_map.py`: Lector de metadatos LanceDB que genera el mapa estructural del proyecto por scope.
  - `services/embeddings.py`: Servicio de embeddings locales con delegación automática al Worker Daemon en VRAM.
  - `services/gemini.py`: Wrapper para interactuar con la API oficial de Google GenAI para generación de respuestas de texto (LLM).
  - `services/scanner.py`: Detección automática de raíces de proyectos, soporte de `.gitignore` nativo y escaneo recursivo.
  - `services/rag.py`: Flujo de orquestación, integración del Reranker (con delegación al daemon), fusión de fragmentos contiguos, formato XML y escape contra inyecciones.
- `tests/e2e/`:
  - `conftest.py`: Fixture compartido `setup_test_env` para aislamiento del entorno de pruebas.
  - `test_f1_scan.py` a `test_f6_fusion.py`: Suites de pruebas dedicadas para cada característica funcional (Tiers 1 y 2).
  - `test_f7_cross.py`: Pruebas de integración y combinaciones cruzadas (Tier 3).
  - `test_f8_scenarios.py`: Escenarios de integración en monorepos reales y reranking (Tier 4).
  - `test_f9_optimizations.py`: Pruebas para validar la creación de índices escalares, mantenimiento y sanitización contra inyección SQL.


---

## Instalacion y Uso

### Requisitos Previos

- Python 3.12+ gestionado a través de `mise` o `uv`.
- Clave de API de Gemini (`GEMINI_API_KEY`), necesaria **únicamente** para la síntesis de respuestas de texto (consultas/generación). La ingesta de código, embeddings y reranking son 100% locales y offline.

### Configuracion de Entorno

Copia el archivo `.env.example` como `.env` e ingresa tu clave:
```bash
GEMINI_API_KEY="tu-clave-api-aquí"
```

### Gestión del Worker Daemon (Global para Máximo Rendimiento)

El Worker Daemon es un servicio en segundo plano **global a nivel de usuario** (gestiona su estado en `~/.rag-local/daemon.json` mediante `platformdirs`) compartido entre todos los repositorios indexados. Mantiene los modelos de embeddings y re-ranking precargados en VRAM (~1.1 GB en CUDA):

```bash
# Iniciar el daemon global en segundo plano
uv run rag-daemon start

# Consultar el estado en tiempo real (puerto, PID, VRAM, tiempo activo mm:ss y ruta del archivo)
uv run rag-daemon status

# Detener el daemon y liberar la VRAM
uv run rag-daemon stop
```

Cuando el daemon está activo, la latencia de inferencia de cualquier repositorio se reduce a **~50ms**, apagándose automáticamente tras 30 minutos de inactividad.

### Ejecutar Ingestion de Codigo

Escanea el repositorio raíz en búsqueda de proyectos Angular (mediante `angular.json`) y NestJS (mediante `nest-cli.json`), clasifica automáticamente sus archivos en scopes (`frontend` / `backend`) y los indexa incrementalmente en LanceDB. Además, aplica de manera automática las exclusiones descritas en el archivo `.gitignore` del proyecto:
```bash
uv run rag-ingest
```

#### Uso en Cualquier Proyecto (Multiproyecto vía CLI)
Si deseas ejecutar la ingesta o consulta manual desde la terminal en un repositorio arbitrario (por ejemplo, `project-example`), puedes configurar las variables de entorno:
- `RAG_REPO_ROOT`: Ruta absoluta al proyecto a indexar/consultar.
- `RAG_LANCEDB_PATH`: Ruta donde se guardará la base de datos (por defecto `<RAG_REPO_ROOT>/.lancedb`).

Ejemplo en PowerShell:
```powershell
$env:RAG_REPO_ROOT="C:\ruta\a\project-example"
$env:RAG_LANCEDB_PATH="C:\ruta\a\project-example\.lancedb"
uv run --project "C:\ruta\a\rag-local" rag-ingest
```

Ejemplo en Bash:
```bash
export RAG_REPO_ROOT="/ruta/a/project-example"
export RAG_LANCEDB_PATH="/ruta/a/project-example/.lancedb"
uv run --project "/ruta/a/rag-local" rag-ingest
```

> [!IMPORTANT]
> Si el script no detecta al menos un proyecto Angular o NestJS en la ruta especificada, la ejecución se detendrá de inmediato con un mensaje de error descriptivo.

### Realizar Consultas RAG

**Formato Humano (Terminal con Rich Panels)**:
```bash
uv run rag-query --query "¿Cómo está definido el modelo User en el esquema Prisma?" --scope backend
```

**Formato JSON (Para Consumo de Agentes de IA)**:
```bash
uv run rag-query --query "Find AuthController login method dependencies" --scope backend --json
```

Cuando la consulta no tiene información relevante en el corpus, la respuesta incluye el marcador `NO_CONTEXT:`:
```json
{
  "context": "",
  "retrieved_chunks": []
}
```
### Ejecutar Auditorías de Estilos y Layout (CLI)

**Trazabilidad Componente ↔ CSS y Propiedades**:
```bash
uv run rag-styles --project-path C:\ruta\a\proyecto --component ChatTab
```

**Auditoría Estática de Riesgos de Layout Responsivo**:
```bash
uv run rag-style-audit --project-path C:\ruta\a\proyecto --severity CRITICAL
```

---

### Integración con Agentes (MCP)

El proyecto incluye soporte nativo para el **Model Context Protocol (MCP)** mediante `fastmcp`. Esto permite a agentes de código (como Antigravity, Cursor, Claude Code, etc.) invocar de manera autónoma las herramientas de ingesta, consulta y auditoría.

#### Herramientas disponibles

| Tool | Descripción | Requiere LanceDB |
|------|------------|------------------|
| `get_config` | Verifica rutas, estado del índice, versión de esquema y estado del daemon | No |
| `manage_daemon` | Inicia (`start`), detiene (`stop`) o consulta (`status`) el Worker Daemon | No |
| `ingest_codebase` | Indexa o actualiza el código incrementalmente (soporta `force=True`) | Genera índice |
| `get_project_map` | Devuelve el mapa de clases/servicios/modelos del proyecto | **Sí** (Lee metadatos) |
| `trace_event_flow` | Rastreabilidad completa de eventos y acciones backend ↔ frontend | **Sí** (Lee metadatos) |
| `get_styles_map` | Devuelve la trazabilidad Componente ↔ CSS, líneas exactas, propiedades y `@media` | **Sí** (Lee metadatos) |
| `audit_layout_risks` | Ejecuta auditoría estática de layout responsivo, flexbox y mitigación DOM | **Sí** (Lee metadatos) |
| `get_code_metrics` | Analiza volumen de líneas (LOC) e identifica archivos para refactorizar | **Sí** (Lee metadatos) |
| `query_codebase` | Búsqueda semántica (embeddings) y texto completo (FTS) en el código indexado | **Sí** (Lee vectores) |


#### Flujo de inicio de sesión para agentes

```
1. get_config()         → ¿existe el índice? ¿está activo el daemon?
2. manage_daemon(...)   → (opcional: start para activar precarga en VRAM)
3. ingest_codebase()    → (si necesario, con opción force=True para reindexar)
4. get_project_map()    → mapa de clases, servicios y modelos
5. trace_event_flow()   → trazabilidad de eventos/WebSockets/reducers (si aplica)
6. get_styles_map(...)  → trazabilidad Componente ↔ CSS, líneas y propiedades (usar component_filter)
7. audit_layout_risks() → auditoría estática de layout responsivo y mitigación por jerarquía DOM
8. get_code_metrics()   → métricas LOC y clasificación de riesgo de refactorización
9. query_codebase(...)  → con los nombres exactos del mapa
```

#### Soporte Multiproyecto Dinámico y Auto-Sincronización
Las herramientas orientadas a proyectos (`query_codebase`, `ingest_codebase`, `get_config`, `get_project_map`, `trace_event_flow`, `get_styles_map`, `audit_layout_risks`, `get_code_metrics`) requieren el parámetro obligatorio `project_path`:
- Al trabajar con agentes de IA, el agente suministra la ruta absoluta del workspace actual en `project_path`.
- Cada repositorio mantiene su propia base de datos vectorial aislada en `<repo>/.lancedb/`, mientras comparten un único Worker Daemon global de inferencia (`~/.rag-local/`).
- **Auto-Sync Transparente**: Antes de procesar cualquier consulta, las herramientas verifican en milisegundos si existen archivos modificados en disco y los sincronizan en caliente en LanceDB. Si hubo cambios, anteponen el encabezado:
  `[Auto-Sync: Actualizados X archivos modificados en LanceDB]`
- La herramienta `manage_daemon` no requiere `project_path` al ser 100% global para el sistema.

> [!IMPORTANT]
> El servidor MCP y las herramientas del RAG leen de forma predeterminada la clave `GEMINI_API_KEY` desde el archivo `.env` central ubicado en la raíz de la herramienta RAG. No es necesario ni requerido duplicar este archivo `.env` en los repositorios o proyectos de destino.


#### 1. Iniciar Servidor MCP Localmente
```bash
uv run python -m rag_local.mcp
# O con mise:
mise run mcp:serve
```
*(Nota: El servidor utiliza el transporte `stdio` por defecto y cuenta con un lock para prevenir colisiones de concurrencia).*

> [!IMPORTANT]
> **Compatibilidad y Estabilidad en Windows (Timeout y Apagado):**
> En entornos Windows, la carga de bibliotecas pesadas de machine learning (como PyTorch o SentenceTransformers) puede causar retrasos de inicialización o bloqueos (*loader locks*). Para mitigar errores de timeout (`context deadline exceeded`) y procesos de Python huérfanos que sigan corriendo en segundo plano tras apagar el servicio en el IDE, el servidor MCP implementa **importaciones perezosas (lazy imports)**. Esto garantiza un arranque inmediato (<0.1s) y un ciclo de vida de apagado/encendido limpio y estable.

#### 2. Configuración en Clientes MCP (`mcp_config.json`)
Para registrar el servidor en tu agente o IDE, añade el siguiente bloque a su configuración de servidores MCP (por ejemplo, en `C:\Users\Leo\.gemini\config\mcp_config.json` para Antigravity):

```json
{
  "mcpServers": {
    "rag-local": {
      "command": "C:/Users/Leo/Repo/rag-local/.venv/Scripts/python.exe",
      "args": [
        "-m",
        "rag_local.mcp"
      ],
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

---

## Pruebas de Software (TDD)

El proyecto cuenta con una suite E2E que valida la consistencia en el comportamiento del RAG. Para ejecutarla offline:
```bash
uv run pytest
# O con mise:
mise run test
```

> [!NOTE]
> Las pruebas operan desconectadas (offline) mediante un mock determinista de embeddings y respuestas del LLM para asegurar repetibilidad y velocidad.

> [!IMPORTANT]
> LanceDB almacena localmente los datos en el directorio `.lancedb/`. Este directorio, al igual que los archivos `.env` y el caché de pytest, se encuentra configurado en el archivo `.gitignore` para no ser subido al repositorio de control de versiones.

---

## Configuracion Avanzada

### Threshold de Relevancia (`MIN_RERANK_SCORE`)

El parámetro `MIN_RERANK_SCORE` en `core/config.py` controla cuándo el RAG declara que no tiene información relevante.

| Variable | Valor por defecto | Descripción |
|----------|------------------|-------------|
| `MIN_RERANK_SCORE` | `-2.0` | Logit raw mínimo del cross-encoder. Chunks bajo este valor se descartan. |

El modelo `BAAI/bge-reranker-base` produce scores en rango ~[-11, +11] sin normalización:
- Codigo claramente irrelevante: scores en [-10, -3]
- Codigo relacionado marginalmente: scores en [-3, 0]
- Codigo relevante: scores en [0, +11]

Un valor de `-2.0` es conservador: solo descarta lo claramente irrelevante. Para mayor precision (menos resultados, mayor calidad), incrementa el valor hacia `0.0` o superior.

### Tasks disponibles (mise)

```bash
mise run mcp:serve   # Inicia el servidor MCP
mise run lint        # Ejecuta ruff check
mise run format      # Formatea con ruff
mise run check       # Verifica tipos con pyrefly
mise run test        # Ejecuta pytest
```

---

## Licencia

Este proyecto está bajo la Licencia [MIT](LICENSE).

