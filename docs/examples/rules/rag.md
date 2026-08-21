---
trigger: always_on
description: "Rules for using the local RAG (rag-local)"
---

# Local RAG Usage Rules (rag-local)

The project has an embedded local RAG based on LanceDB. Follow these strict guidelines for its use.

---

## How the RAG Searches

The RAG searches by **semantic code similarity** and **exact full-text search (FTS)**. The index contains source code fragments exactly as written — class names, methods, variables, decorators, Prisma fields, etc.

**The RAG does NOT understand business concepts that aren't named in the code.** The query must use **terms that literally exist in the source code**, not abstract concepts or domain-specific names that you know but the code doesn't use.

### Mandatory flow before running a query

If you already have the project map in context, use it directly. If not:

```
NO MAP: query_codebase(project_path=<workspace_path>, query="how does the vehicle system work")
        → the code uses "automatic_move", not "vehicle" → NO_CONTEXT

WITH MAP: get_project_map(project_path=<workspace_path>) → discovers "AutomaticMoveService"
          query_codebase(project_path=<workspace_path>, query="AutomaticMoveService logic")  ← works

ALTERNATIVE (if no index): grep_search("vehicle" OR "auto" OR "move")
          → discovers "AutomaticMoveService" → query_codebase(project_path=<workspace_path>, ...)
```

### Queries that work well

| Type | Effective examples |
|------|-------------------|
| Class/service names | `"UserService"`, `"AuthController"`, `"PaymentModule"` |
| Method names | `"login method AuthController"`, `"findAll users repository"` |
| Prisma field names | `"User model fields"`, `"Order relations schema"` |
| Angular/NestJS decorators | `"@Injectable providers"`, `"@Component selector"` |
| Constant/config names | `"MIN_RERANK_SCORE config"`, `"LANCEDB_PATH settings"` |
| Named technical patterns | `"JWT strategy implementation"`, `"Prisma transaction"` |
| Flows using code terms | `"process_query pipeline steps"`, `"ingest_codebase flow"` |

### Queries that don't work

| Type | Why it fails |
|------|--------------|
| Business concepts not named in code | `"how does the payment system work"` when the module is called `BillingController` |
| Domain synonyms | `"auto"` or `"car"` when the code uses `VehicleController` |
| Abstract natural-language questions | `"what is the configured value of X?"` — use the constant's exact name |
| Questions about general behavior | `"what does the app do"` — not enough specific signal |

### Handling `NO_CONTEXT`

If the RAG responds with a message starting with `NO_CONTEXT:`, it means **no fragment in the corpus passed the minimum relevance threshold**. This happens when:

- The query uses terms that don't exist in the indexed code.
- The queried concept isn't implemented in the project.
- The relevant module hasn't been ingested.

**On a `NO_CONTEXT`, never invent or assume.** Explore the codebase with `grep_search` or `list_dir` to find the correct name and retry the query with that term.

### Supported Projects & Framework Signatures

The repository must contain at least one of the following root signature files to be valid for indexing:
- **Python**: `pyproject.toml`
- **Angular**: `angular.json`
- **NestJS**: `nest-cli.json`
- **Next.js**: `next.config.js`, `next.config.mjs`, or `next.config.ts`

> [!NOTE]
> **Vanilla JS & Frontend Assets**: Vanilla JS is not an independent base project type. In fullstack or server-rendered Python projects (e.g. FastAPI, Flask, Twitch bots, or apps serving static HTML/JS/CSS without a Node.js build system), frontend scripts and stylesheets are automatically ingested and indexed under the **`python`** scope.

---

## Tool Reference

### Shared parameter conventions
- **`project_path`**: Absolute path to the current workspace repository root. **Mandatory in every tool except `manage_daemon`.** Without it the RAG doesn't know which LanceDB database to open. (Note: system paths, root drives, user home, and `.gemini` paths are rejected for security).
- **`scope`**: Optional. Use it when you know the answer lies in a specific framework/environment (`'python'` | `'angular'` | `'nestjs'` | `'nextjs-app'`). Reduces noise and improves precision.
- **`query`**: In English. Use exact code names when you know them.

---

### 1. `get_config` ([`config.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/config.py))
- **When to use**: At the start of every session, to check whether the repository is indexed, verify `SCHEMA_VERSION` compatibility, and check the Worker Daemon status.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the workspace/project root. Sets the active context and validates the `.lancedb/` database.
- **Requires prior ingestion**: No (status utility only).
- **Example call**: `get_config(project_path="C:\Users\Leo\Repo\bot-tv")`
- **Example output**:
```
[RAG Configuration & Index Status]
Proyecto: C:\Users\Leo\Repo\bot-tv
Indexado: Sí
Esquema RAG: 2.0.0 (Actualizada)
Modelo Embeddings: Alibaba-NLP/gte-multilingual-base
Worker Daemon: Activo (Port 2139 | Dispositivo: CUDA | Tiempo Activo: 01:56 | Path: C:\Users\Leo\.rag-local\daemon.json)
Total Chunks: 800
```

---

### 2. `get_project_map` ([`project_map.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/project_map.py))
- **When to use**: At the start of any task, to discover the project's architecture and the real names of classes, functions, and models before querying code.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory.
  - `scope` (`str | None`, Optional, default `None`): Filters symbols by framework/environment (`'python'`, `'angular'`, `'nestjs'`, `'nextjs-app'`).
  - `full_tree` (`bool`, Optional, default `False`): If `True`, generates the full ASCII directory tree. If `False`, returns the compact modular view grouped by symbols.
- **Requires prior ingestion**: Yes.
- **Example call**: `get_project_map(project_path="C:\Users\Leo\Repo\bot-tv")`
- **Example output**:
```
[Project Map — 133 files, 582 symbols indexed]

  actions/models.py: Classes: AgentTalkResult, BotToggleResult, ModelInfo, NicknameResult, SyncFollowersResult, UserResolveResult, UserRolesResult
  actions/moderation.py: Classes: ModerationActionResult | Functions: _get_broadcaster_channel, action_ban_user, action_delete_messages, action_purge_user, action_unban_user
  actions/users.py: Functions: _sync_irc_user, action_set_nickname, action_sync_user_roles, action_toggle_bot, action_update_user_roles | Events: updated_u, user_nickname_updated, user_role_updated
  agent/client.py: Classes: TalkAgent | Functions: chat, clear_history, get_all_rpm_status, get_rpm_status, initialize
  bot_tv/bot.py: Classes: Bot | Functions: close, event_command_error, event_oauth_authorized, event_ready | Events: bot_fully_connected
  components/chat_component.py: Classes: ChatComponent | Functions: _enrich_and_persist, _get_chatter_role_cached, component_command_error
  prisma/schema.prisma: Classes: client, db | Models: ApiConsumptionLog, AppSettings, ChannelUser, ChatHistory, Token, User
```

---

### 3. `query_codebase` ([`query.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/query.py))
- **When to use**: To retrieve logic implementations, method definitions, database schemas, or API contracts, using exact code terms in English. See "How the RAG Searches" above for query-crafting rules.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory.
  - `query` (`str`, **Mandatory**): Search terms or technical question in English, using literal names from the code (e.g. `'AuthService validateToken'`, `'User model relations'`).
  - `scope` (`str | None`, Optional, default `None`): Limits the semantic + FTS search to a specific scope.
- **Requires prior ingestion**: Yes.
- **Example call**: `query_codebase(project_path="C:\Users\Leo\Repo\bot-tv", query="ChatComponent message handling and persistence")`
- **Example output**:
```
[Archivos relevantes: 2]
  - [src/bot_tv/components/chat_component.py:L55-L151]
  - [src/bot_tv/web/static/app.js:L55-L78]

<context>
<file path="src/bot_tv/components/chat_component.py" start_line="55" end_line="151" compressed="true">
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
import twitchio
from twitchio.ext import commands

class ChatComponent(commands.Component):
    """Componente de chat: mensajes en consola + comandos generales."""

    async def _enrich_and_persist(
        self,
        payload: twitchio.ChatMessage,
        user_id: str,
        username: str,
        display_name: str,
        msg_id: str | None,
        es_bot: bool,
    ) -> None:
        """Persiste datos en PostgreSQL y enriquece avatar/follow en segundo plano."""
        cache = self.bot.user_cache
        chatter = payload.chatter
        ...
</file>
</context>
```

---

### 4. `trace_event_flow` ([`event_flow.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/event_flow.py))
- **When to use**: When working on reactive or real-time architectures (Socket.IO, WebSockets, Redux/Preact reducers, EventBus, dispatch actions).
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory.
  - `event_name` (`str | None`, Optional, default `None`): Name of the event, action, or constant to trace (e.g. `'user_nickname_updated'`, `'ADD_TOAST'`). If omitted, returns the global trace for the monorepo.
  - `limit` (`int`, Optional, default `15`): Maximum number of event chains to list in global, unfiltered runs.
- **Requires prior ingestion**: Yes.
- **Example call**: `trace_event_flow(project_path="C:\Users\Leo\Repo\bot-tv", event_name="user_nickname_updated")`
- **Example output**:
```
[Event-Flow Map — 1 Event(s) Detected]

Event: UserNicknameUpdatedEvent (event:user_nickname_updated)
  ├── Definition:  src/bot_tv/events.py:87 (class UserNicknameUpdatedEvent)
  ├── Emitter:     src/bot_tv/actions/users.py:161 (action_set_nickname)
  ├── WebSocket:   src/bot_tv/web/ws_handler.py:1 (block), src/bot_tv/web/ws_handler.py:87 (block)
  ├── Reducer:     src/bot_tv/web/static/app.js:358 (reducer:user_nickname_updated)
  └── UI/Consumer: src/bot_tv/web/static/components/event-config.js:161 (block)
```

---

### 5. `get_styles_map` ([`styles.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/styles.py))
- **When to use**: When working on UI design, creating components, inspecting which CSS applies to a JSX/HTML file, or auditing unused classes (Dead CSS).
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory.
  - `component_filter` (`str | None`, Optional, default `None`): Name or path of the UI component to inspect (e.g. `'ChatTab'`, `'src/components/modal'`).
  - `class_filter` (`str | None`, Optional, default `None`): Specific CSS class name to look up in the styles (e.g. `'sys-text'`).
  - `property_filter` (`str | None`, Optional, default `None`): Specific CSS property or value to audit (e.g. `'flex'`, `'word-break'`, `'min-width'`).
- **Requires prior ingestion**: Yes.
- **Recommendation**: Always pass `component_filter="ComponentName"` (e.g. `component_filter="ChatTab"`) to avoid overly long responses.
- **Example call**: `get_styles_map(project_path="C:\Users\Leo\Repo\bot-tv", component_filter="ChatTab")`
- **Example output**:
```
[Styles System Map — 18 CSS files, 266 variables, 347 classes]

[Active Filters: component=ChatTab]

[Component ↔ CSS Traceability]
  Component: src/bot_tv/web/static/components/chat/ChatTab.js
    - .alert-desc -> src/bot_tv/web/static/css/chat.css:L809-813 | selector: '.irc-disconnect-alert .alert-desc' | props(color: var(--text-muted), font-size: 10px, line-height: 1.3)
    - .badge-moderator -> src/bot_tv/web/static/css/chat.css:L340-344 | selector: '.badge-moderator' | props(background: rgba(46, 125, 50, 0.15), color: #4caf50, border: 1px solid rgba(46, 125, 50, 0.3))
    - .chat-author -> src/bot_tv/web/static/css/chat.css:L147-151 | selector: '.chat-msg-header .chat-author' | props(font-weight: 700, white-space: nowrap, flex-shrink: 0)
    - .chat-display -> src/bot_tv/web/static/css/chat.css:L155-160 | selector: '.chat-msg-header .chat-display' | props(font-weight: 400, opacity: 0.65, font-size: 0.8em, margin-left: 2px)
```

---

### 6. `audit_layout_risks` ([`style_audit.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/style_audit.py))
- **When to use**: When diagnosing responsive overflow on mobile screens, clipped elements, unwrapped long text, or to validate that a CSS change didn't introduce design regressions.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory.
  - `severity` (`str`, Optional, default `"ALL"`): Severity level to report (`'CRITICAL'`, `'WARNING'`, `'INFO'`, or `'ALL'`).
  - `file_filter` (`str | None`, Optional, default `None`): Name or path of one or more CSS files, comma-separated (e.g. `'chat.css'`, `'responsive.css'`).
- **Requires prior ingestion**: Yes.
- **Note**: each finding lists a `Selector`, an `Issue` (what's wrong), and a `Fix` (concrete suggestion), formatted in concise English for optimal LLM token efficiency. Detects Flexbox/Grid failures without `min-width: 0` or `overflow: hidden`, text overflow, stacking context traps, and mitigation by DOM hierarchy. Automatically excludes false positives (`flex-shrink: 0`, fixed `px` dimensions, `:hover`/`:disabled` pseudo-classes, universal `*` resets, elastic truncation `ellipsis` in children, popovers with self-isolation, and atomic micro-UI).
- **Example call**: `audit_layout_risks(project_path="C:\Users\Leo\Repo\bot-tv", severity="WARNING")`
- **Example output**:
```
[CSS Layout Audit — 2 issues found (0 CRITICAL, 2 WARNING, 0 INFO)]

[WARNING] src/bot_tv/web/static/css/agent.css:L9-17 | Text Break Risk
  Selector: .agent-convo
  Issue: Text container '.agent-convo' does not specify wrap rules ('overflow-wrap: anywhere' or 'overflow-wrap: break-word').
  Fix: Add 'overflow-wrap: anywhere;' or 'overflow-wrap: break-word;'.

[WARNING] src/bot_tv/web/static/css/chat.css:L39-57 | Flex Wrap Overflow Risk
  Selector: .chat-action-group
  Issue: Horizontal flex container with multiple items '.chat-action-group' (display: flex) lacks 'flex-wrap: wrap' or 'overflow-x: auto', which may cause child overflow.
  Fix: Add 'flex-wrap: wrap;' or 'overflow-x: auto;'.
```

---

### 7. `get_code_metrics` ([`metrics.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/metrics.py))
- **When to use**: When analyzing technical complexity, planning refactors, or identifying monolithic files that exceed line-count thresholds.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory.
  - `threshold` (`int`, Optional, default `200`): Minimum physical lines of code to report a file as a refactoring candidate.
- **Requires prior ingestion**: Yes.
- **Note**: also reports a language distribution breakdown by file extension.
- **Example call**: `get_code_metrics(project_path="C:\Users\Leo\Repo\bot-tv", threshold=200)`
- **Example output**:
```
[Codebase Metrics — 133 files, 21,700 total lines]
Files >= 200 lines: 36

[Refactoring Targets — Files >= 200 lines]
  CRITICAL: src/bot_tv/web/static/css/chat.css (912 lines | 1013 code | 23 chunks)
  CRITICAL: src/bot_tv/web/static/css/followers.css (712 lines | 749 code | 18 chunks)
  CRITICAL: src/bot_tv/web/static/components/user/UserProfileDrawer.js (706 lines | 660 code | 2 chunks)
  CRITICAL: src/bot_tv/web/static/components/chat/ChatTab.js (637 lines | 641 code | 11 chunks)
  CRITICAL: src/bot_tv/web/static/app.js (561 lines | 768 code | 46 chunks)
  CRITICAL: src/bot_tv/actions/users.py (551 lines | 518 code | 8 chunks)
  CRITICAL: src/bot_tv/database/user_cache.py (429 lines | 465 code | 20 chunks)

[Language Distribution]
  .py: 72 file(s) (9,571 lines)
  .js: 40 file(s) (7,201 lines)
  .css: 18 file(s) (4,722 lines)
  .html: 1 file(s) (115 lines)
  .prisma: 1 file(s) (79 lines)
  .ts: 1 file(s) (12 lines)
```

---

### 8. `ingest_codebase` ([`ingest.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/ingest.py))
- **When to use**: For the initial indexing of a non-indexed repository, or when a full, clean re-ingest is required (`force=True`).
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the repository to index.
  - `force` (`bool`, Optional, default `False`): If `True`, discards the hash cache and rebuilds the vector tables from scratch.
- **Requires prior ingestion**: N/A — this is the ingestion tool itself.
- **Framework validation**: Validates that `project_path` contains at least one supported project signature (`angular.json`, `nest-cli.json`, `pyproject.toml`, or `next.config.*`). If no supported framework is detected, ingestion is cancelled with an error.
- **Restriction (mandatory)**: Every other tool already auto-syncs the index transparently (see "Keeping the Index Up to Date" below). **The agent must NEVER call `ingest_codebase` on its own initiative.** Only run it when the user explicitly asks for an ingest/re-index, or when `get_config` reports `Indexado: No` **and** the user has explicitly confirmed after being informed.
- **Example call**: `ingest_codebase(project_path="C:\Users\Leo\Repo\bot-tv", force=False)`
- **Example output**:
```
Ingesta completada de forma exitosa.
Resumen:
-> Procesamiento finalizado. Nuevos: 3, Modificados: 0, Sin cambios: 130.
No hay fragmentos nuevos o modificados para indexar.
Optimizando y compactando almacenamiento en LanceDB...
[22:32:19] INFO     Base de datos LanceDB compactada correctamente.
Optimización y compactación completadas con éxito.
¡Ingesta completada exitosamente!
• Archivos procesados en disco: 133
• Archivos nuevos: 3
• Archivos modificados: 0
• Archivos eliminados: 0
• Archivos sin cambios: 130
• Chunks indexados con éxito: 0/0
• Total de chunks en LanceDB: 800
```

---

### 9. `manage_daemon` ([`daemon.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/daemon.py))
- **When to use**: To manually query or manage the global Worker Daemon that preloads embedding and re-ranking models into GPU VRAM. *(Global, user-level service — does not require `project_path`.)*
- **Parameters**:
  - `action` (`str`, Optional, default `"status"`):
    - `'status'`: checks whether the daemon is running, allocated VRAM, port, and uptime.
    - `'start'`: launches the background process and preloads the models into VRAM.
    - `'stop'`: stops the daemon and frees 100% of the VRAM in use.
- **Requires prior ingestion**: No — unrelated to LanceDB content.
- **Restriction (mandatory)**: The daemon is managed externally by the user through a dedicated process. `get_config` already reports its current status, which is enough for the agent's purposes. **The agent must NOT call `manage_daemon` on its own initiative** — only if the user explicitly asks to start, stop, or check the daemon directly.
- **Note**: also reports the compute device in use (e.g. `cuda`).
- **Example call**: `manage_daemon(action="status")`
- **Example output**:
```
WORKER_DAEMON: Activo
  - Archivo de estado: C:\Users\Leo\.rag-local\daemon.json
  - Puerto: 2139
  - PID: 6968
  - Dispositivo: cuda
  - VRAM: 2813.75 MB / 11263.75 MB
  - Tiempo Activo: 03:45
```

---

## Mandatory Automatic Startup Protocol

**Do not wait for user instructions to use these tools.** At the start of every work session on a project, detect the task type and autonomously execute the corresponding flow.

### How to detect the task type

| Signals in the message | Task type | Flow |
|---|---|---|
| Logic bug, error, functionality, API, database, service, model | Logic only | A |
| Events, WebSockets, Socket.IO, reducer, dispatch, emit, real-time, event bus | Event / Reactive logic | A (with event branch) |
| CSS, layout, design, visual, UI component, responsive, style, color | Design only | B |
| Both signals present, or "fix issues" without specifics | Logic + design | C |

### Index pre-condition & supported projects (applies to every flow)

Every flow below starts with `get_config`. 
- **Supported projects**: The repository must contain at least one supported framework (Angular, NestJS, Python, Next.js).
- If `get_config` reports `Indexado: No`, the index hasn't been created yet: **stop, inform the user, and propose running `ingest_codebase`** — never run it automatically (see the restriction on `ingest_codebase` in Tool Reference above).

### Flow A: Logic only (and event architectures)

1. `get_config(project_path=<workspace_path>)` — check index and daemon status.
2. `get_project_map(project_path=<workspace_path>)` — structural map of the project (review the `Events:`/`Actions:` sections if applicable).
3. **If the task involves events / WebSockets / reducers**: `trace_event_flow(project_path=<workspace_path>, event_name=<relevant_event>)` — map the full chain before querying code.
4. `query_codebase(project_path=<workspace_path>, ...)` — using the names and files discovered in the map or event trace.
5. *(Optional)* `get_code_metrics(project_path=<workspace_path>)` — when the task involves refactoring or assessing code complexity.

### Flow B: Design only

1. `get_config(project_path=<workspace_path>)` — check index and daemon status.
2. `get_project_map(project_path=<workspace_path>)` — structural map (needed to understand components).
3. `get_styles_map(project_path=<workspace_path>, component_filter=<relevant_component>)` — CSS traceability for the affected component.
4. `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` — current design state before intervening (baseline).
5. `query_codebase(project_path=<workspace_path>, ...)` — to find the component's JSX/HTML code and its relations.
6. After making changes: `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` again to confirm the new implementation doesn't introduce regressions.

### Flow C: Logic + design

1. `get_config(project_path=<workspace_path>)` — check index and daemon status.
2. `get_project_map(project_path=<workspace_path>)` — full structural map.
3. **If the design reacts to events or WebSocket**: `trace_event_flow(project_path=<workspace_path>, event_name=<relevant_event>)` — locate the UI template/component and its reducer.
4. `get_styles_map(project_path=<workspace_path>, component_filter=<relevant_component>)` — CSS traceability.
5. `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` — design state baseline.
6. `query_codebase(project_path=<workspace_path>, ...)` — for logic and code relations, using names from the map.
7. *(Optional)* `get_code_metrics(project_path=<workspace_path>)` — when the task involves refactoring or assessing code complexity.
8. After finishing: `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` to confirm the implementation is visually correct.

---

## Keeping the Index Up to Date & Subprocess Timeouts

Every RAG tool **except `manage_daemon` and `get_config`** (`query_codebase`, `audit_layout_risks`, `get_styles_map`, `get_code_metrics`, `get_project_map`, `trace_event_flow`) runs an automatic pre-query check (`Fast Pre-Query Check`, ~10ms) backed by strongly-typed IPC events:

1. **File-change detection (`mtime`)**: If files were edited or created since the last ingest, the RAG transparently syncs only the changed deltas into LanceDB (~150ms) before responding. The tool prepends: `[Auto-Sync: Actualizados X archivos modificados en LanceDB]`.
2. **Schema version check (`SCHEMA_VERSION`)**: If any version or embedding model mismatch in `rag-local` is detected between the index metadata and `SCHEMA_VERSION` (e.g. `2.0.0`), the RAG automatically triggers a clean re-ingest.
3. **Built-in Automatic Ignore Rules**: Scans automatically exclude `.gitignore` entries plus standard noise directories (`vendor/`, `third_party/`, `.venv/`, `__pycache__/`, `.ruff_cache/`, `node_modules/`, `dist/`) and minified files (`*.min.js`, `*.min.css`, `*.bundle.js`).
4. **Dynamic Subprocess Watchdog Lifecycle**:
   - Queries start with a standard **3-minute** timeout.
   - If auto-sync / re-ingestion is triggered, the static 3-minute limit is disarmed and an **inactivity watchdog (10 minutes between batch progress)** takes over, allowing large codebases to ingest without timing out.
   - Once synchronization completes, the timer **resets to a fresh 3-minute window** for the main query/mapping task.

Because of this automatic sync, **it is not necessary — and not permitted without explicit user confirmation — to run `ingest_codebase` manually** after editing files or updating schema versions (see the restriction on `ingest_codebase` in Tool Reference above).
