---
trigger: always_on
glob:
description: "Reglas para utilizar el RAG local (rag-local)"
---

# Reglas de Uso de RAG Local (rag-local)

El proyecto cuenta con un RAG local embebido basado en LanceDB. Sigue estas directrices estrictas para su consumo.

---

## Cómo funciona `query_codebase`

El RAG busca por **similitud semántica de código** y **texto exacto (FTS)**. El índice contiene fragmentos del código fuente tal como está escrito — nombres de clases, métodos, variables, decoradores, campos de Prisma, etc.

**El RAG NO entiende conceptos de negocio que no estén nombrados en el código.**

Esto significa que la query debe usar **términos que existan literalmente en el código fuente**, no conceptos abstractos o nombres propios del dominio que tú conoces pero el código no usa.

---

## Flujo recomendado al inicio de una sesión

Al trabajar con un proyecto por primera vez, o al retomar una sesión:

1. **`get_config`** — verifica si el índice existe (`LANCEDB_INDEXADA: Sí/No`) y el estado del Worker Daemon.
2. **`manage_daemon(action="start")`** — (opcional) precarga los modelos en VRAM para máxima velocidad de respuesta (~0.05s por query).
3. **`ingest_codebase`** — si el índice no existe o se requiere una reindexación completa (`force=True`). Proponer al usuario si requiere reindexar.
4. **`get_project_map`** — obtén el mapa de clases, servicios y modelos del proyecto.
4. **`get_styles_map`** — obtén la trazabilidad Componente ↔ CSS, líneas exactas, variables `--*` y mapa de propiedades (recomendado usar `component_filter`).
5. **`audit_layout_risks`** — realiza una auditoría estática de layout responsivo, desbordamientos flexbox y mitigación por jerarquía DOM UI.
6. **`get_code_metrics`** — obtén las métricas LOC del proyecto e identifica archivos críticos (>400 líneas) o de advertencia (>200 líneas) que requieran refactorización.
7. **`query_codebase`** — con los nombres encontrados en el mapa, obtén el código real y relaciones.

```
Ejemplo:
  get_project_map() →
    [nestjs] Controllers: AuthController, BillingController
             Services: BillingService, UserService
    [nestjs/prisma] Models: User, Order, Payment

  get_styles_map(component_filter="ChatTab") →
    [Component ↔ CSS Traceability]
      Component: src/components/chat/ChatTab.js
        - .sys-text -> src/css/chat.css:L462-469
          | selector: '.chat-msg.is-system .sys-text'
          | props(font-size: 0.93em, color: var(--text), flex: 1, min-width: 0, overflow-wrap: anywhere, word-break: break-word)

  audit_layout_risks(severity="CRITICAL") →
    [CSS Layout Audit — 0 issues found (Severity Filter: CRITICAL)]

  get_code_metrics(threshold=200) →
    [Code Metrics Summary]
      [CRITICAL] src/services/billing.py: 450 LOC (needs refactoring)
```

---

### `get_styles_map`
- **Cuándo usar**: Al trabajar en diseño UI, estilizado CSS, agregar componentes, auditar código muerto (Dead CSS) o inspeccionar propiedades de diseño.
- **Qué devuelve**:
  - Trazabilidad bidireccional Componente UI ↔ Reglas CSS con línea exacta y mapa completo de propiedades.
  - Catálogo de variables CSS (`vars(--*)`) por archivo.
  - Reporte de clases obsoletas (Dead CSS) no referenciadas (excluyendo prefijos de librerías de iconos como `fa-`).
  - **RECOMENDACIÓN**: Pasar siempre `component_filter="NombreComponente"` (ej. `component_filter="ChatTab"`) para evitar respuestas extensas.

### `audit_layout_risks`
- **Cuándo usar**: Al diagnosticar desbordamientos responsivos, elementos que rompen el layout en pantallas pequeñas, textos no rotos o reglas CSS conflictivas.
- **Qué devuelve**:
  - Auditoría estática clasificada por severidad (`CRITICAL`, `WARNING`, `INFO`).
  - Detección de Flexbox/Grid sin `min-width: 0` u `overflow: hidden` (evaluando `overflow-x` y `overflow-y`).
  - Cruzamiento de jerarquía DOM de componentes JSX/HTML y reglas CSS (mitigación por contenedor padre etiquetada como `[MITIGATED: Protegido por ancestro .clase]`).
  - Exclusión automática de falsos positivos (elementos con `flex-shrink: 0`, dimensiones fijas en `px`, pseudo-clases `:hover`/`:disabled` y resets universales `*`).

### `get_code_metrics`
- **Cuándo usar**: Al analizar la complejidad del codebase, planificar refactorizaciones o evaluar modularidad.
- **Qué devuelve**:
  - Conteo de líneas de código físicas y efectivas (excluyendo vacías y comentarios).
  - Archivos clasificados como `CRITICAL` (>400 líneas) o `WARNING` (>200 líneas).

---

## Flujo obligatorio antes de hacer una query

Si ya tienes el mapa en contexto, úsalo directamente. Si no:

```
SIN MAPA: query_codebase("how does the vehicle system work")
           → el código usa "automatic_move", no "vehicle" → NO_CONTEXT

CON MAPA: get_project_map() → descubre "AutomaticMoveService"
          query_codebase("AutomaticMoveService logic")  ← funciona

ALTERNATIVA (si no hay índice): grep_search("vehicle" OR "auto" OR "move")
          → descubre "AutomaticMoveService" → query_codebase(...)
```

---

## Qué queries funcionan bien

El RAG es efectivo cuando la query contiene términos que existen en el código:

| Tipo | Ejemplos efectivos |
|------|-------------------|
| Nombres de clases/servicios | `"UserService"`, `"AuthController"`, `"PaymentModule"` |
| Nombres de métodos | `"login method AuthController"`, `"findAll users repository"` |
| Nombres de campos Prisma | `"User model fields"`, `"Order relations schema"` |
| Decoradores Angular/NestJS | `"@Injectable providers"`, `"@Component selector"` |
| Nombres de constantes/config | `"MIN_RERANK_SCORE config"`, `"LANCEDB_PATH settings"` |
| Patrones técnicos con su nombre | `"JWT strategy implementation"`, `"Prisma transaction"` |
| Flujos con términos del código | `"process_query pipeline steps"`, `"ingest_codebase flow"` |

## Qué queries NO funcionan

| Tipo | Por qué falla |
|------|--------------|
| Conceptos de negocio sin nombre en código | `"how does the payment system work"` si el módulo se llama `BillingController` |
| Sinónimos del dominio | `"auto"` o `"car"` cuando el código usa `VehicleController` |
| Preguntas en lenguaje natural abstracto | `"¿cuál es el valor configurado de X?"` — usa el nombre exacto de la constante |
| Preguntas sobre comportamiento general | `"what does the app do"` — no hay suficiente señal específica |

---

## Manejo de `NO_CONTEXT`

Si el RAG responde con un mensaje que empieza con `NO_CONTEXT:`, significa que **ningún fragmento del corpus superó el umbral de relevancia mínima**. Esto ocurre cuando:

- La query usa términos que no existen en el código indexado.
- El concepto consultado no está implementado en el proyecto.
- El módulo relevante no ha sido ingestado (ver sección siguiente).

**Ante un `NO_CONTEXT`, nunca inventes ni supongas.** Explora el codebase con `grep_search` o `list_dir` para encontrar el nombre correcto y reintenta la query con ese término.

---

## Clasificación de Herramientas por Dependencia de Ingesta (LanceDB)

### Requieren Ingesta Previa (`ingest_codebase` / LanceDB)
Estas herramientas consultan la base de datos vectorial y los metadatos extendidos (`lines_code`, `css_rules`) indexados en `.lancedb/`. **Leen 100% desde LanceDB sin acceder al disco durante la consulta (0 lecturas a disco)**:
- **`query_codebase`**: Búsqueda semántica (embeddings) y texto completo FTS.
- **`get_project_map`**: Extracción del mapa estructural de clases, servicios, controllers y modelos Prisma almacenados en el índice.
- **`get_styles_map`**: Trazabilidad Componente ↔ CSS y mapa de clases/variables consultando metadatos `css_rules` en el índice.
- **`audit_layout_risks`**: Auditoría estática de layout responsivo, desbordamientos flexbox y mitigación DOM consultando metadatos `css_rules` en el índice.
- **`get_code_metrics`**: Conteo e inspección de volumen de líneas de código (LOC) consultando metadatos `lines_code` en el índice.

### Herramienta Sintética de Estado del Entorno
- **`get_config`**: Muestra el resumen sintético del repositorio, versión de esquema SemVer (`SCHEMA_VERSION`), modelo de embeddings y estado del índice en 5 líneas.

---

## Nuevas Herramientas Especializadas

```
query_codebase(
    query: str,          # Términos de búsqueda en inglés, usando nombres del código
    scope: str | None,   # "angular" | "nestjs" | "python" — filtra por framework
    project_path: str    # Ruta absoluta del workspace actual (OBLIGATORIO)
)

ingest_codebase(
    project_path: str | None, # Ruta absoluta opcional al repositorio del proyecto
    force: bool = False       # Forzar reindexación completa ignorando caché de hashes
)

get_styles_map(
    project_path: str | None,     # Ruta absoluta opcional al repositorio del proyecto
    component_filter: str | None, # Filtra por nombre o archivo de componente UI (ej. 'ChatTab')
    class_filter: str | None,     # Filtra por nombre de clase CSS específica (ej. 'sys-text')
    property_filter: str | None   # Filtra por propiedad CSS (ej. 'word-break' o 'flex')
)

audit_layout_risks(
    project_path: str | None,     # Ruta absoluta opcional al repositorio del proyecto
    severity: str = "ALL",        # Filtra por gravedad ('CRITICAL', 'WARNING', 'INFO', 'ALL')
    file_filter: str | None       # Filtra uno o varios archivos CSS (ej. 'chat.css, responsive.css')
)

get_code_metrics(
    project_path: str | None, # Ruta absoluta opcional al repositorio del proyecto
    threshold: int = 200      # Umbral de líneas para reportar (por defecto: 200)
)
```

- **`project_path`**: Siempre pasa la ruta absoluta del workspace. Sin esto el RAG no sabe qué base de datos abrir.
- **`scope`**: Úsalo cuando sabes que la respuesta está en un framework específico. Reduce ruido y mejora precisión.
- **`query`**: En inglés. Usa los nombres exactos del código cuando los conoces.

---

## Mantener el índice actualizado (Refresco Automático Express)

- **Sincronización Automática Incremental (`Fast Pre-Query Check`)**: Todas las herramientas del RAG (`query_codebase`, `audit_layout_risks`, `get_styles_map`, `get_code_metrics`, `get_project_map`) ejecutan una verificación ultra-rápida de compatibilidad de esquema SemVer (`SCHEMA_VERSION`) y modificación de archivos (`mtime`) en **~10ms**.
- Si detecta un esquema desactualizado, el RAG ejecuta de forma transparente una re-ingesta limpia forzada (`force=True`).
- Si durante la sesión editas o creas archivos en el proyecto, **el RAG los detecta y sincroniza automáticamente los deltas en LanceDB en ~150ms antes de responder o auditar**.
- No es necesario ejecutar `ingest_codebase` manualmente tras editar archivos ni al actualizar versiones de esquema.