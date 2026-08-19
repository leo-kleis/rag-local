---
trigger: always_on
description: "Rules for using the local RAG (rag-local)"
---

# Local RAG Usage Rules (rag-local)

The project has an embedded local RAG based on LanceDB. Follow these strict guidelines for its use.

---

## How `query_codebase` works

The RAG searches by **semantic code similarity** and **exact full-text search (FTS)**. The index contains source code fragments exactly as written — class names, methods, variables, decorators, Prisma fields, etc.

**The RAG does NOT understand business concepts that aren't named in the code.**

This means the query must use **terms that literally exist in the source code**, not abstract concepts or domain-specific names that you know but the code doesn't use.

---

## Mandatory Automatic Startup Protocol

**Do not wait for user instructions to use these tools.** At the start of every work session on a project, detect the task type and autonomously execute the corresponding flow.

### Mandatory Pre-Step: Inspect MCP Tool Schemas

Before running any `rag-local` tool, the agent **MUST review the tool schemas** (`.gemini/antigravity/mcp/rag-local/<toolName>.json`) or their formal declaration. This guarantees exact knowledge of all real properties and filters available (e.g., `file_filter` and `severity` in `audit_layout_risks`; `component_filter`, `class_filter`, and `property_filter` in `get_styles_map`; `scope` in `query_codebase`), avoiding assumptions or executions without the right filters.

---

### How to detect the task type

Analyze the user's initial message and classify the task:

| Signals in the message | Task type |
|---|---|
| Logic bug, error, functionality, API, database, service, model | **Logic only** |
| Events, WebSockets, Socket.IO, reducer, dispatch, emit, real-time, event bus | **Event / Reactive logic** |
| CSS, layout, design, visual, UI component, responsive, style, color | **Design only** |
| Both signals present, or "fix issues" without specifics | **Logic + design** |

---

### Index pre-condition check

Before any flow, if the index is not initialized (`Indexado: No` in `get_config(project_path=<workspace_path>)`), inform the user and propose running `ingest_codebase`. **Do not run `ingest_codebase` automatically** — it is a heavy operation that requires confirmation.

---

## Flow by task type

### Flow A: Logic only (and event architectures)

1. **Review schema**: check parameters for `get_config`, `get_project_map`, `trace_event_flow`, and `query_codebase`.
2. `get_config(project_path=<workspace_path>)` — checks index and Worker Daemon status. **`project_path` is mandatory.**
3. `get_project_map(project_path=<workspace_path>)` — structural map of the project (review the `Events:` and `Actions:` sections if applicable).
4. **If the task involves events / WebSockets / reducers**:
   `trace_event_flow(project_path=<workspace_path>, event_name=<relevant_event>)` — maps the full chain (`Definition -> Emitter -> WebSocket -> Reducer -> UI`) before querying code.
5. `query_codebase(project_path=<workspace_path>, ...)` — using the names and files discovered in the map or event trace. **`project_path` is mandatory.**
6. *(Optional)* `get_code_metrics(project_path=<workspace_path>)` — when the task involves refactoring or assessing code complexity.

---

### Flow B: Design only

1. **Review schema**: check parameters for `get_styles_map` (`component_filter`, `class_filter`, `property_filter`) and `audit_layout_risks` (`file_filter`, `severity`).
2. `get_config(project_path=<workspace_path>)` — checks index and Worker Daemon status. **`project_path` is mandatory.**
3. `get_project_map(project_path=<workspace_path>)` — structural map (needed to understand components).
4. `get_styles_map(project_path=<workspace_path>, component_filter=<relevant_component>)` — CSS traceability for the affected component.
5. `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` — current design state before intervening (baseline).
6. `query_codebase(project_path=<workspace_path>, ...)` — to find the component's JSX/HTML code and its relations.
7. After making changes: `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` again to confirm the new implementation doesn't introduce regressions.

---

### Flow C: Logic + design

1. **Review schemas**: check parameters for every tool to be used before invoking it.
2. `get_config(project_path=<workspace_path>)` — checks index and Worker Daemon status. **`project_path` is mandatory.**
3. `get_project_map(project_path=<workspace_path>)` — full structural map.
4. **If the design reacts to events or WebSocket**:
   `trace_event_flow(project_path=<workspace_path>, event_name=<relevant_event>)` — locates the UI template/component and its reducer.
5. `get_styles_map(project_path=<workspace_path>, component_filter=<relevant_component>)` — CSS traceability.
6. `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` — design state baseline.
7. `query_codebase(project_path=<workspace_path>, ...)` — for logic and code relations, using names from the map.
8. *(Optional)* `get_code_metrics(project_path=<workspace_path>)` — when the task involves refactoring or assessing code complexity.
9. After finishing: `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` to confirm the implementation is visually correct.

```
Example:
  get_project_map(project_path="/path/to/project") →
    [Project Map — 24 files indexed across 4 modules]

    [src/auth] 4 files
      Classes: AuthService (auth.service.ts), AuthGuard (auth.guard.ts)
      Functions: validateToken (jwt.utils.ts), hashPassword (hash.utils.ts)
      Events: UserLoggedInEvent (auth.events.ts)

    [src/billing] 3 files
      Classes: BillingService (billing.service.ts), StripeClient (stripe.client.ts)
      Functions: calculateTax (tax.utils.ts)

    [prisma] 1 file
      Models: User, Order, Payment, Subscription

  get_styles_map(project_path="/path/to/project", component_filter="ChatTab") →
    [Component ↔ CSS Traceability]
      Component: src/components/chat/ChatTab.js
        - .sys-text -> src/css/chat.css:L462-469
          | selector: '.chat-msg.is-system .sys-text'
          | props(font-size: 0.93em, color: var(--text), flex: 1, min-width: 0, overflow-wrap: anywhere, word-break: break-word)

  audit_layout_risks(project_path="/path/to/project", severity="CRITICAL") →
    [CSS Layout Audit — 0 issues found (Severity Filter: CRITICAL)]

  get_code_metrics(project_path="/path/to/project", threshold=200) →
    [Codebase Metrics — 10 files, 3,450 total lines]
    Files >= 200 lines: 1

    [Refactoring Targets — Files >= 200 lines]
      CRITICAL: src/services/billing.py (450 lines | 380 code | 4 chunks)
```

---

## Tool reference

### `get_styles_map`
- **When to use**: When working on UI design, CSS styling, adding components, auditing dead CSS, or inspecting design properties.
- **Returns**:
  - Bidirectional traceability between UI components and CSS rules, with exact line numbers and a full property map.
  - Catalog of CSS variables (`vars(--*)`) per file.
  - Report of unreferenced/obsolete classes (Dead CSS), excluding icon-library prefixes such as `fa-`.
  - **RECOMMENDATION**: Always pass `component_filter="ComponentName"` (e.g. `component_filter="ChatTab"`) to avoid overly long responses.

### `audit_layout_risks`
- **When to use**: When diagnosing responsive overflow, elements breaking layout on small screens, unbroken text, or conflicting CSS rules.
- **Returns**:
  - Static audit classified by severity (`CRITICAL`, `WARNING`, `INFO`).
  - Detection of Flexbox/Grid without `min-width: 0` or `overflow: hidden` (evaluating `overflow-x` and `overflow-y`).
  - Cross-reference between JSX/HTML DOM hierarchy and CSS rules (parent-container mitigation labeled `[MITIGATED: Protected by ancestor .class]`).
  - Automatic exclusion of false positives (elements with `flex-shrink: 0`, fixed `px` dimensions, `:hover`/`:disabled` pseudo-classes, and universal `*` resets).

### `trace_event_flow`
- **When to use**: When working on event-driven architectures (Socket.IO, WebSockets, Redux/Preact reducers, EventBus, reactive actions).
- **Returns**:
  - Full end-to-end traceability map:
    `Backend Definition -> Backend Emitter -> Handler/WebSocket -> Frontend Reducer -> UI Component/Config`.
  - Supports filtering by specific name (`event_name='user_nickname_updated'`) or global mapping with pagination (`limit=15`).

### `get_code_metrics`
- **When to use**: When analyzing codebase complexity, planning refactors, or evaluating modularity.
- **Returns**:
  - Physical and effective lines-of-code count (excluding blank lines and comments).
  - Files classified as `CRITICAL` (>400 lines) or `WARNING` (>200 lines).

### `manage_daemon`
- **What it's for**: Starts or stops the Worker Daemon that preloads models into VRAM for maximum query response speed (~0.05s per query). Global for the whole system (not tied to a `project_path`).
- **Note**: The agent does not need to invoke this tool on its own — it is managed externally by the user through a dedicated process. `get_config` already reports the daemon's current status; the agent only needs to know what this tool is for.

---

## Mandatory flow before running a query

If you already have the map in context, use it directly. If not:

```
NO MAP: query_codebase(project_path=<workspace_path>, query="how does the vehicle system work")
        → the code uses "automatic_move", not "vehicle" → NO_CONTEXT

WITH MAP: get_project_map(project_path=<workspace_path>) → discovers "AutomaticMoveService"
          query_codebase(project_path=<workspace_path>, query="AutomaticMoveService logic")  ← works

ALTERNATIVE (if no index): grep_search("vehicle" OR "auto" OR "move")
          → discovers "AutomaticMoveService" → query_codebase(project_path=<workspace_path>, ...)
```

---

## Queries that work well

The RAG is effective when the query contains terms that exist in the code:

| Type | Effective examples |
|------|-------------------|
| Class/service names | `"UserService"`, `"AuthController"`, `"PaymentModule"` |
| Method names | `"login method AuthController"`, `"findAll users repository"` |
| Prisma field names | `"User model fields"`, `"Order relations schema"` |
| Angular/NestJS decorators | `"@Injectable providers"`, `"@Component selector"` |
| Constant/config names | `"MIN_RERANK_SCORE config"`, `"LANCEDB_PATH settings"` |
| Named technical patterns | `"JWT strategy implementation"`, `"Prisma transaction"` |
| Flows using code terms | `"process_query pipeline steps"`, `"ingest_codebase flow"` |

## Queries that don't work

| Type | Why it fails |
|------|--------------|
| Business concepts not named in code | `"how does the payment system work"` when the module is called `BillingController` |
| Domain synonyms | `"auto"` or `"car"` when the code uses `VehicleController` |
| Abstract natural-language questions | `"what is the configured value of X?"` — use the constant's exact name |
| Questions about general behavior | `"what does the app do"` — not enough specific signal |

---

## Handling `NO_CONTEXT`

If the RAG responds with a message starting with `NO_CONTEXT:`, it means **no fragment in the corpus passed the minimum relevance threshold**. This happens when:

- The query uses terms that don't exist in the indexed code.
- The queried concept isn't implemented in the project.
- The relevant module hasn't been ingested (see next section).

**On a `NO_CONTEXT`, never invent or assume.** Explore the codebase with `grep_search` or `list_dir` to find the correct name and retry the query with that term.

---

## Tool Classification by Ingestion Dependency (LanceDB)

### Require Prior Ingestion (`ingest_codebase` / LanceDB)
These tools query the vector database and the extended metadata (`lines_code`, `css_rules`, `tags`) indexed in `.lancedb/`. **They read 100% from LanceDB with zero disk reads during the query**:
- **`query_codebase`**: Semantic search (embeddings) and full-text search (FTS) with specialized chunking for methods and reducer cases.
- **`get_project_map`**: Extracts the structural map of classes, services, events, actions, controllers, and Prisma models stored in the index.
- **`trace_event_flow`**: Full backend ↔ frontend event lifecycle traceability.
- **`get_styles_map`**: Component ↔ CSS traceability and class/variable map, querying `css_rules` metadata in the index.
- **`audit_layout_risks`**: Static audit of responsive layout, flexbox overflow, and DOM mitigation, querying `css_rules` metadata in the index.
- **`get_code_metrics`**: Lines-of-code (LOC) volume count and inspection, querying `lines_code` metadata in the index.

### Environment Status Utility
- **`get_config(project_path=<workspace_path>)`**: Shows a synthetic summary of the repository, the SemVer schema version (`SCHEMA_VERSION`), the embeddings model, and the index status in 5 lines. **`project_path` is mandatory**, same as the rest of the project tools.

---

## Specialized Tools

```
query_codebase(
    query: str,           # Search terms in English, using names from the code
    project_path: str,    # Absolute path to the current workspace (MANDATORY)
    scope: str | None,    # "angular" | "nestjs" | "nextjs-app" | "python" — filters by framework
)

get_project_map(
    project_path: str,        # Absolute path to the project repository (MANDATORY)
    scope: str | None,        # Optional filter: 'angular' | 'nestjs' | 'nextjs-app' | 'python'
    full_tree: bool = False   # If True, outputs full ASCII directory trie instead of compact module view
)

ingest_codebase(
    project_path: str,    # Absolute path to the project repository (MANDATORY)
    force: bool = False   # If True, forces full re-indexing ignoring cache (default: False)
)

trace_event_flow(
    project_path: str,        # Absolute path to the project repository (MANDATORY)
    event_name: str = "",     # Optional name of the event/action to trace (e.g. 'user_nickname_updated')
    limit: int = 15           # Limit of events shown in global runs (default: 15)
)

get_styles_map(
    project_path: str,            # Absolute path to the project repository (MANDATORY)
    component_filter: str | None, # Filters by UI component name or file (e.g. 'ChatTab')
    class_filter: str | None,     # Filters by specific CSS class name (e.g. 'sys-text')
    property_filter: str | None   # Filters by CSS property (e.g. 'word-break' or 'flex')
)

audit_layout_risks(
    project_path: str,            # Absolute path to the project repository (MANDATORY)
    severity: str = "ALL",        # Filters by severity ('CRITICAL', 'WARNING', 'INFO', 'ALL')
    file_filter: str | None       # Filters one or more CSS files (e.g. 'chat.css, responsive.css')
)

get_code_metrics(
    project_path: str,    # Absolute path to the project repository (MANDATORY)
    threshold: int = 200  # Line threshold to report (default: 200)
)

manage_daemon(
    action: str = "status"  # 'status' | 'start' | 'stop' (system-wide, global)
)

get_config(
    project_path: str    # Absolute path to the current workspace (MANDATORY)
)
```

- **`project_path`**: Always pass the workspace's absolute path in project tools, **including `get_config`**. Mandatory in every tool except `manage_daemon`.
- **`scope`**: Use it when you know the answer lies in a specific framework or domain. Reduces noise and improves precision.
- **`query`**: In English. Use exact code names when you know them.

---

## Keeping the Index Up to Date & Subprocess Timeouts

Every RAG tool **except `manage_daemon` and `get_config`** (`query_codebase`, `audit_layout_risks`, `get_styles_map`, `get_code_metrics`, `get_project_map`, `trace_event_flow`) runs an automatic pre-query check (`Fast Pre-Query Check`, ~10ms) backed by strongly-typed IPC events:

1. **File-change detection (`mtime`)**: If files were edited or created since the last ingest, the RAG transparently syncs only changed deltas into LanceDB (~150ms) before responding. The tool prepends: `[Auto-Sync: Actualizados X archivos modificados en LanceDB]`.
2. **Schema version check (`SCHEMA_VERSION`)**: If a **MINOR** or **MAJOR** version bump in `rag-local` is detected (e.g. `1.3.0`), the RAG automatically triggers a clean re-ingest.
3. **Built-in Automatic Ignore Rules**: Scans automatically exclude `.gitignore` entries plus standard noise directories (`vendor/`, `third_party/`, `.venv/`, `__pycache__/`, `.ruff_cache/`, `node_modules/`, `dist/`) and minified files (`*.min.js`, `*.min.css`, `*.bundle.js`).
4. **Dynamic Subprocess Watchdog Lifecycle**:
   - Queries start with a standard **3-minute** timeout.
   - If auto-sync / re-ingestion is triggered, the static 3-minute limit is disarmed and an **inactivity watchdog (10 minutes between batch progress)** takes over, allowing large codebases to ingest without timing out.
   - Once synchronization completes, the timer **resets to a fresh 3-minute window** for the main query/mapping task.

It is not necessary to manually run `ingest_codebase` after editing files or updating schema versions.
