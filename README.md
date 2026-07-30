# RAG Local Optimization

Sistema RAG local de alto rendimiento y bajo consumo de tokens optimizado para analizar monorepos de código estructurados en Angular 21, NestJS 11 + Fastify, Next.js 16 (React 19) y Prisma.

El objetivo principal de esta herramienta es proveer búsquedas de contexto sumamente precisas a agentes de IA, reduciendo significativamente el ruido (eliminando líneas de código cortadas a la mitad) y optimizando el consumo de tokens mediante la fusión inteligente de fragmentos adyacentes, reordenamiento de relevancia y formateo XML estructurado.

---

## Caracteristicas Claves

### 1. Chunking Sintactico con Tree-Sitter
- **TypeScript & TSX**: Utiliza el parser sintáctico de `tree-sitter-typescript` para agrupar clases, componentes React/Next.js (`.tsx`, `.jsx`) y decoradores (ej. `@Component` o `@Injectable`). Mantiene los constructores, componentes y métodos completos sin cortarlos a la mitad.
- **HTML**: Utiliza el parser de `tree-sitter-html` para agrupar etiquetas jerárquicas y previene cortes a la mitad de tags.
- **Prisma**: Divide esquemas por bloques funcionales (`model`, `enum`, `datasource`, etc.) y extrae dependencias entre tablas.
- **Bloque Unico**: Si un archivo es menor a 50 líneas, se procesa como un único bloque para conservar el contexto completo.

### 2. Persistencia y Búsqueda Híbrida (LanceDB)
- **Búsqueda Híbrida**: Combina la similitud semántica (vectores) con búsqueda de texto completo (FTS/BM25) indexando la columna `text`. Esto garantiza encontrar términos de código exactos (variables o firmas de métodos).
- **Ingesta Incremental Basada en Cache**: Almacena hashes SHA256 para evitar re-indexar archivos sin cambios, eliminando chunks obsoletos de forma automática.
- **Embeddings Locales Offline**: Utiliza PyTorch y `sentence-transformers` para generar representaciones vectoriales de forma local y offline, eliminando cuotas de red (RPM/RPD) y retardos.
- **Robustez GPU/CPU**: Inicializa automáticamente en GPU (`cuda`) con fallback automático y transparente a `cpu` ante fallos.
- **Índices Escalares BTREE**: Habilita de forma automática índices de tipo `BTREE` en las columnas `scope` y `source` para acelerar de forma drástica búsquedas filtradas y eliminaciones.
- **Compactación y Limpieza**: Al finalizar la ingesta de fragmentos nuevos o modificados, ejecuta `table.optimize()` para reducir la fragmentación en disco, purgar versiones obsoletas y actualizar los índices.

### 3. Re-ranking y Filtro de Relevancia (GPU / CPU)
- Incorpora una capa de re-ranking con la librería `rerankers` utilizando el modelo `BAAI/bge-reranker-base`.
- Permite recuperar un número alto de chunks candidatos en la búsqueda inicial y reducirlos al subconjunto verdaderamente relevante antes de enviarlo a Gemini.
- **Filtro post-rerank**: Cada chunk recibe un score en logits raw (rango ~[-11, +11]). Los chunks con score inferior a `MIN_RERANK_SCORE` (por defecto `-2.0`) se descartan automáticamente como irrelevantes.
- **Refusal explícito**: Si ningún chunk supera el threshold tras el filtro, el tool MCP retorna `NO_CONTEXT: ...` — un marcador claro para que el agente no fabrique una respuesta basada en conocimiento general.
- Soporte nativo para aceleración por GPU (CUDA) con fallback automático y transparente a CPU si no hay acelerador disponible.

### 4. Enriquecimiento de Contexto Relacional (Graph-RAG)
- **TypeScript & NestJS**: Analiza imports de dependencias locales e inyecta fragmentos importados dentro de etiquetas `<imported_file path="..." relation_type="...">`.
- **Prisma**: Identifica relaciones directas (`@relation` y tablas asociadas) e inyecta los modelos vinculados en etiquetas `<related_model name="...">`.
- **Soporte de Gitignores Múltiples**: Escaneo recursivo que hereda y combina de forma automática las exclusiones de todos los archivos `.gitignore` anidados en subcarpetas.

### 5. Mapa Estructural del Proyecto (`get_project_map`)
- Lee los metadatos ya indexados en LanceDB (clases, servicios, modelos Prisma, componentes) sin generar embeddings ni llamar a ningún LLM.
- Devuelve un mapa del proyecto agrupado por scope (`angular`, `nestjs`, `nextjs-app`, `python`) con los nombres exactos y rutas de cada símbolo.
- **Permite al agente conocer el vocabulario real del código** antes de hacer queries semánticos, eliminando el problema de buscar con sinónimos que el RAG no reconoce.
- Zero costo adicional: reutiliza los metadatos extraídos durante la ingesta (`class_name`, `type`, `models`, `scope`, `source`).

### 6. Optimizacion de Tokens y Contexto para Agentes
- **Fusion de Chunks**: Chunks adyacentes o solapados del mismo archivo se fusionan en un único fragmento continuo.
- **Estructura XML Limpia**: Contexto formateado mediante bloques XML estructurados (`<context>`, `<file path="...">`), facilitando la lectura a agentes LLM.
- **Seguridad**: Escape estricto de caracteres especiales (`&`, `<`, `>`, `"`, `'`) en el código y en las consultas para mitigar inyecciones de prompts.
- **Truncado Seguro**: Si el contexto excede 15,000 caracteres, se trunca limpiamente con un indicador `[TRUNCATED]`.
- **Múltiples Fallbacks de Generación**: En caso de fallas o saturación de límites en el modelo principal de generación (`gemini-2.5-flash`), realiza de forma transparente un fallback secuencial a modelos secundarios (`gemini-3.5-flash`, `gemini-3-flash`, `gemini-3.1-flash-lite` y `gemini-2.5-flash-lite`).

---

## Estructura del Codigo

- `src/rag_local/`:
  - `cli/ingest.py`: Comando `rag-ingest` para indexar y actualizar el repositorio en LanceDB.
  - `cli/mcp.py`: Servidor MCP (`rag-mcp`) para exponer herramientas de consulta e ingesta a agentes con soporte dinámico multiproyecto.
  - `core/config.py`: Gestión estructurada de configuraciones y variables de entorno mediante `pydantic-settings`, resolviendo la base de datos por defecto respecto al repositorio analizado.
  - `core/logging.py`: Configuración estética de logs del sistema mediante `rich`.
  - `parsers/`: Módulos de análisis sintáctico.
    - `typescript/`: Subpaquete modular que implementa **Hierarchical AST Chunking** mediante `tree-sitter`, dividiendo clases y métodos preservando imports y cabeceras de clase de forma sintácticamente válida.
    - `html.py` y `prisma.py`: Parsers sintácticos especializados.
  - `services/db.py`: Wrapper e implementación de colección LanceDB, hashes de archivos, caché de ingesta e integración del chunking sintáctico.
  - `services/embeddings.py`: Servicio exclusivo de generación de embeddings locales en GPU (CUDA) o CPU.
  - `services/gemini.py`: Wrapper para interactuar con la API oficial de Google GenAI para generación de respuestas de texto (LLM).
  - `services/scanner.py`: Detección automática de raíces de proyectos, soporte de `.gitignore` nativo y escaneo recursivo.
  - `services/rag.py`: Flujo de orquestación, integración del Reranker, fusión de fragmentos contiguos, formato XML y escape contra inyecciones.
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
- Clave de API de Gemini (`GEMINI_API_KEY`), necesaria **únicamente** para la síntesis de respuestas de texto (consultas/generación). La ingesta de código y el cálculo de embeddings son 100% locales y offline.

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
El tool MCP devuelve directamente:
```
NO_CONTEXT: No se encontró información relevante en el corpus local sobre este tema...
```

### Integración con Agentes (MCP)

El proyecto incluye soporte nativo para el **Model Context Protocol (MCP)** mediante `fastmcp`. Esto permite a agentes de código (como Antigravity, Cursor, Claude Code, etc.) invocar de manera autónoma las herramientas de ingesta y consulta.

#### Herramientas disponibles

| Tool | Descripción |
|------|------------|
| `get_config` | Verifica rutas, estado del índice y API key |
| `ingest_codebase` | Indexa o actualiza el código incrementalmente (soporta `force=True` para reindexación limpia) |
| `get_project_map` | Devuelve el mapa de clases/servicios/modelos del proyecto |
| `get_styles_map` | Mapea archivos CSS, variables de diseño y clases obsoletas (Dead CSS) |
| `get_code_metrics` | Analiza volumen de líneas (LOC) e identifica archivos que requieren refactorización |
| `query_codebase` | Búsqueda semántica en el código indexado |

#### Flujo de inicio de sesión para agentes

```
1. get_config()        → ¿existe el índice?
2. ingest_codebase()   → (si necesario, con opción force=True para reindexar)
3. get_project_map()   → mapa de clases, servicios y modelos
4. get_styles_map()    → mapa de estilos CSS, variables y clases obsoletas
5. get_code_metrics()  → métricas LOC y clasificación de riesgo de refactorización
6. query_codebase(...) → con los nombres exactos del mapa
```

#### Soporte Multiproyecto Dinámico
Todas las herramientas del servidor MCP (`query_codebase`, `ingest_codebase`, `get_config`) exponen el parámetro opcional `project_path`. 
- Al trabajar con agentes de IA (cuyo directorio de ejecución suele ser una ruta del sistema o temporal), el agente pasará de forma automática la ruta absoluta del workspace actual en `project_path`.
- Esto aísla e indexa la base de datos `.lancedb/` de cada repositorio de forma independiente en su propio directorio raíz.

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
