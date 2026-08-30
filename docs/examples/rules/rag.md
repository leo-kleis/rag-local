---
trigger: always_on
description: "Rules for using the local RAG (rag-local)"
---

# Local RAG Usage Rules (rag-local)

The project has an embedded local RAG based on LanceDB. Follow these strict guidelines for its use via the FastMCP protocol.

---

## How the RAG Searches

The RAG searches by **semantic code similarity** (embeddings in GPU VRAM) and **exact full-text search (FTS / BM25)**.

### Two Independent Data Layers (Zero Interference)

The RAG strictly decouples user project code from third-party libraries into two separate physical storage layers:

1. **Project Codebase Layer (`<workspace>/.lancedb/`)**:
   - Stores AST chunks, models, classes, functions, event flows, and styles written in the project repository.
   - Kept updated automatically via **Fast Pre-Query Check (~10ms)**.
   - Queried via `query_codebase`, `get_project_map`, `trace_event_flow`, `get_styles_map`, `audit_layout_risks`, and `get_code_metrics`.

2. **External Dependencies Global Layer (`~/.cache/rag-local/dependencies/`)**:
   - Stores third-party type contracts (`.pyi` / `.d.ts`), constructors, interfaces, and docstrings (`twitchio`, `asyncpg`, `fastapi`, `starlette`, `pg`, `@prisma/client`, etc.).
   - Indexed once at user level; shared across projects with **0.0s reuse**.
   - Queried and managed via `query_dependency`, `ingest_dependencies`, and `manage_dependencies`.

---

### Mandatory flow before running a query

**The RAG does NOT understand business concepts that aren't named in the code.** The query must use **terms that literally exist in the source code**.

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

---

### Supported Projects & Framework Signatures

The repository must contain at least one of the following root signature files to be valid for indexing:
- **Python**: `pyproject.toml`
- **Angular**: `angular.json`
- **NestJS**: `nest-cli.json`
- **Next.js**: `next.config.js`, `next.config.mjs`, or `next.config.ts`

> [!NOTE]
> **Vanilla JS & Frontend Assets**: Vanilla JS is not an independent base project type. In fullstack or server-rendered Python projects (e.g. FastAPI, Flask, Twitch bots, or apps serving static HTML/JS/CSS without a Node.js build system), frontend scripts and stylesheets are automatically ingested and indexed under the **`python`** scope.

---

## MCP Tool Reference

### Shared parameter conventions
- **`project_path`**: Absolute path to the current workspace repository root. **Mandatory in every tool except `manage_daemon`.**
- **`scope`**: Optional. Limits results to a specific framework (`'python'`, `'angular'`, `'nestjs'`, `'nextjs-app'`).
- **`query`**: In English. Use exact code names when you know them.

---

## Part I: Project Codebase Tools (`<workspace>/.lancedb/`)

### 1. `get_config` ([`config.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/config.py))
- **When to use**: At the start of every session, to check whether the repository is indexed, verify `SCHEMA_VERSION` compatibility, and check the Worker Daemon status.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the workspace root.
- **Requires prior ingestion**: No (status utility only).
- **Example call**: `get_config(project_path="C:\Users\Leo\Repo\bot-tv")`
- **Example output**:
```
[RAG Configuration & Index Status]
Proyecto: C:\Users\Leo\Repo\bot-tv
Indexado: Sí
Esquema RAG: 5.0.0 (Actualizada)
Modelo Embeddings: jinaai/jina-code-embeddings-0.5b [En caché: Sí]
Modelo Reranker: onnx-community/bge-reranker-v2-m3-ONNX [En caché: Sí]
Worker Daemon: Activo (Port 21239 | Dispositivo: CUDA | Tiempo Activo: 08:50 | Path: /app/.cache/daemon/daemon.json)
Total Chunks: 1465
```

---

### 2. `get_project_map` ([`project_map.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/project_map.py))
- **When to use**: At the start of any task, to discover the project's architecture and the real names of classes, functions, and models before querying code.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory.
  - `scope` (`str | None`, Optional, default `None`): Filters symbols by framework (`'python'`, `'angular'`, `'nestjs'`, `'nextjs-app'`).
  - `path_filter` (`str | None`, Optional, default `None`): Filters symbols by directory or subsytem substring (e.g. `'src/bot_tv/actions'`).
  - `full_tree` (`bool`, Optional, default `False`): If `True`, returns the full ASCII directory tree.
- **Requires prior ingestion**: Yes.
- **Example call**: `get_project_map(project_path="C:\Users\Leo\Repo\bot-tv", path_filter="src/bot_tv/actions")`
- **Example output**:
```
[Project Map — 3 files, 24 symbols indexed (filtered by path=src/bot_tv/actions)]

  actions/models.py: Classes: AgentTalkResult, BotToggleResult, ModelInfo, NicknameResult, SyncFollowersResult, UserResolveResult, UserRolesResult
  actions/moderation.py: Classes: ModerationActionResult | Functions: _get_broadcaster_channel, action_ban_user, action_delete_messages, action_purge_user, action_unban_user
  actions/users.py: Functions: _sync_irc_user, action_set_nickname, action_sync_user_roles, action_toggle_bot, action_update_user_roles | Events: updated_u, user_nickname_updated, user_role_updated
```

---

### 3. `query_codebase` ([`query.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/query.py))
- **When to use**: To retrieve logic implementations, method definitions, database schemas, or internal API contracts, using exact code terms in English.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory.
  - `query` (`str`, **Mandatory**): Search terms or technical question in English, using literal names from the code.
  - `scope` (`str | None`, Optional, default `None`): Limits search to a specific scope.
  - `full_block` (`bool`, Optional, default `False`): Expands chunks from LanceDB to include the complete enclosing class, method, or free function block (0 disk reads).
- **Requires prior ingestion**: Yes.
- **Example call**: `query_codebase(project_path="C:\Users\Leo\Repo\bot-tv", query="ChatComponent message handling and persistence", full_block=True)`
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
  - `event_name` (`str | None`, Optional, default `None`): Event name or wildcard pattern (e.g. `'user_nickname_updated'`, `'follower_*'`).
  - `entity` (`str | None`, Optional, default `None`): Entity/domain prefix to filter by (e.g. `'user'`, `'chat'`).
  - `limit` (`int`, Optional, default `15`): Maximum number of event chains to return.
- **Requires prior ingestion**: Yes.
- **Example call**: `trace_event_flow(project_path="C:\Users\Leo\Repo\bot-tv", event_name="user_nickname_updated")`
- **Example output**:
```
[Event-Flow Map — 1 Event(s) Detected]

Event: UserNicknameUpdatedEvent (event:user_nickname_updated)
  ├── Schema:      { user_id: str, old_nick: str, new_nick: str }
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
  - `component_filter` (`str | None`, Optional, default `None`): Name or path of the UI component (e.g. `'ChatTab'`).
  - `class_filter` (`str | None`, Optional, default `None`): Specific CSS class name to look up (e.g. `'sys-text'`).
  - `property_filter` (`str | None`, Optional, default `None`): Specific CSS property to audit (e.g. `'flex'`, `'min-width'`).
- **Requires prior ingestion**: Yes.
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
  - `severity` (`str`, Optional, default `"ALL"`): Severity level (`'CRITICAL'`, `'WARNING'`, `'INFO'`, or `'ALL'`).
  - `file_filter` (`str | None`, Optional, default `None`): One or more CSS files, comma-separated (e.g. `'chat.css'`).
- **Requires prior ingestion**: Yes.
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
- **Restriction (mandatory)**: Every other tool already auto-syncs the index transparently (see "Keeping the Index Up to Date" below). **The agent must NEVER call `ingest_codebase` on its own initiative.** Only run it when the user explicitly asks for an ingest/re-index, or when `get_config` reports `Indexado: No` **and** the user has explicitly confirmed after being informed.
- **Example call**: `ingest_codebase(project_path="C:\Users\Leo\Repo\bot-tv", force=False)`

---

## Part II: External Dependencies Tools (`~/.cache/rag-local/dependencies/`)

External dependencies are stored in a dedicated, decoupled global LanceDB table (`external_dependencies`), allowing agents to query third-party contracts without guessing names or reading disk files.

### 9. `query_dependency` ([`dependencies.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/dependencies.py))
- **When to use**: To inspect third-party library signatures, constructors, interfaces, and docstrings without guessing API names or reading disk files.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project directory for environment context.
  - `package_name` (`str`, **Mandatory**): Name of the third-party package (e.g. `'twitchio'`, `'starlette'`, `'pg'`).
  - `symbol_name` (`str | None`, Optional, default `None`): Exact class, interface, function, or enum name (e.g. `'ChannelFollow'`, `'Client'`, `'load_dotenv'`). Resolves in $< 1\text{ ms}$ via B-Tree index.
  - `query` (`str | None`, Optional, default `None`): Semantic concept or keyword search across package docstrings and signatures.
  - `language` (`str | None`, Optional, default `None`): Language filter (`'python'`, `'typescript'`).
  - `limit` (`int`, Optional, default `5`): Maximum number of symbol definitions to return.
- **Requires prior ingestion**: Yes (via `ingest_dependencies` or auto-cached in the global user store).
- **Example call**: `query_dependency(project_path="C:\Users\Leo\Repo\bot-tv", package_name="twitchio", symbol_name="ChannelFollow")`
- **Example output**:
```
[Dependency Contracts: twitchio — 1 symbol(s) found]

Symbol: ChannelFollow (class) | ID: python:twitchio@3.2.2:ChannelFollow
  ├── Module:    twitchio.models
  ├── Signature: class ChannelFollow(id: str, user_id: str, user_name: str, followed_at: str)
  ├── Docstring: Represents a channel follower event subscription or follower record.
  └── Declaration:
        class ChannelFollow:
            id: str
            user_id: str
            user_name: str
            followed_at: datetime
```

---

### 10. `ingest_dependencies` ([`dependencies.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/dependencies.py))
- **When to use**: To extract and index third-party type contracts (`.pyi` / `.d.ts`), constructors, and docstrings from project dependencies into the global LanceDB cache.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project repository.
  - `package_name` (`str | None`, Optional, default `None`): Ingest or re-index a single specific package (e.g. `'twitchio'`).
  - `language` (`str | None`, Optional, default `None`): Ingest only a specific ecosystem (`'python'` or `'typescript'`).
  - `force` (`bool`, Optional, default `False`): Forces re-extraction even if the package is already cached.
- **Requires prior ingestion**: No.
- **Example call**: `ingest_dependencies(project_path="C:\Users\Leo\Repo\bot-tv", package_name="twitchio")`
- **Example output**:
```
Ingesta de dependencias finalizada:
  • Paquetes nuevos/actualizados: 1 (python:twitchio@3.2.2)
  • Paquetes ya presentes en caché global: 12
  • Total de nuevos símbolos indexados: 250
```

---

### 11. `manage_dependencies` ([`dependencies.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/dependencies.py))
- **When to use**: To check the synchronization status of project dependencies vs the global cache, remove specific packages, or purge the global cache.
- **Parameters**:
  - `project_path` (`str`, **Mandatory**): Absolute path to the project repository.
  - `action` (`str`, Optional, default `"status"`): Management action (`'status'`, `'remove'`, or `'clean'`).
  - `package_name` (`str | None`, Optional, default `None`): Name of the package to remove (required for action `'remove'`).
  - `version` (`str | None`, Optional, default `None`): Specific package version to remove.
  - `language` (`str | None`, Optional, default `None`): Language filter (`'python'` or `'typescript'`).
- **Requires prior ingestion**: No.
- **Example call**: `manage_dependencies(project_path="C:\Users\Leo\Repo\bot-tv", action="status")`
- **Example output**:
```
Estado de dependencias para: C:\Users\Leo\Repo\bot-tv

Python (11):
  • asyncpg (0.31.0): Indexada en caché global
  • twitchio (3.2.2): Indexada en caché global
  • starlette (1.3.1): Indexada en caché global
  ...
Typescript (4):
  • dotenv (17.4.2): Indexada en caché global
  • pg (8.22.0): Indexada en caché global
  ...
```

---

## Part III: Global Infrastructure & Worker Daemon

### 12. `manage_daemon` ([`daemon.py`](file:///c:/Users/Leo/Repo/rag-local/src/rag_local/mcp/tools/daemon.py))
- **When to use**: To manually query or manage the global Worker Daemon that preloads embedding and re-ranking models into GPU VRAM. *(Global, user-level service — does not require `project_path`.)*
- **Parameters**:
  - `action` (`str`, Optional, default `"status"`):
    - `'status'`: checks whether the daemon is running, allocated VRAM, port, and uptime.
    - `'start'`: launches the background process and preloads the models into VRAM.
    - `'stop'`: stops the daemon and frees 100% of the VRAM in use.
- **Requires prior ingestion**: No — unrelated to LanceDB content.
- **Restriction (mandatory)**: The daemon is managed externally by the user through a dedicated process. `get_config` already reports its current status, which is enough for the agent's purposes. **The agent must NOT call `manage_daemon` on its own initiative** — only if the user explicitly asks to start, stop, or check the daemon directly.
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
| Third-party libraries, external packages, decorators, SDK methods (TwitchIO, Starlette, FastAPI, PG, Prisma) | External Dependency / API Contract | D |

---

### Index pre-condition & supported projects (applies to every flow)

Every flow below starts with `get_config`. 
- **Supported projects**: The repository must contain at least one supported framework (Angular, NestJS, Python, Next.js).
- If `get_config` reports `Indexado: No`, the index hasn't been created yet: **stop, inform the user, and propose running `ingest_codebase`** — never run it automatically (see the restriction on `ingest_codebase` in Tool Reference above).

---

### Flow A: Logic only (and event architectures)

1. `get_config(project_path=<workspace_path>)` — check index and daemon status.
2. `get_project_map(project_path=<workspace_path>)` — structural map of the project (review the `Events:`/`Actions:` sections if applicable).
3. **If the task involves events / WebSockets / reducers**: `trace_event_flow(project_path=<workspace_path>, event_name=<relevant_event>)` — map the full chain before querying code.
4. `query_codebase(project_path=<workspace_path>, ...)` — using the names and files discovered in the map or event trace.
5. *(Optional)* `get_code_metrics(project_path=<workspace_path>)` — when the task involves refactoring or assessing code complexity.

---

### Flow B: Design only

1. `get_config(project_path=<workspace_path>)` — check index and daemon status.
2. `get_project_map(project_path=<workspace_path>)` — structural map (needed to understand components).
3. `get_styles_map(project_path=<workspace_path>, component_filter=<relevant_component>)` — CSS traceability for the affected component.
4. `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` — current design state before intervening (baseline).
5. `query_codebase(project_path=<workspace_path>, ...)` — to find the component's JSX/HTML code and its relations.
6. After making changes: `audit_layout_risks(project_path=<workspace_path>, file_filter=..., severity=...)` again to confirm the new implementation doesn't introduce regressions.

---

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

### Flow D: Third-Party Dependencies & Library Contracts

When working with external libraries, SDKs, or third-party packages:
1. `query_dependency(project_path=<workspace_path>, package_name="<pkg>", symbol_name="<Symbol>")` — look up the exact class, function, interface, or constructor signature.
2. **If looking for concepts/methods by functionality**: `query_dependency(project_path=<workspace_path>, package_name="<pkg>", query="<concept>")` — semantic/FTS search across docstrings.
3. **If the package returns `NO_DEPENDENCY_FOUND`**: Run `ingest_dependencies(project_path=<workspace_path>, package_name="<pkg>")` to extract and index its contracts to the global cache.
4. Write implementation using the verified signatures without hallucinating method names or argument orders.

---

## Keeping the Index & Cache Up to Date

The system manages synchronization and timeouts across the two storage layers with different, optimized strategies:

### 1. Project Codebase Index (`<workspace>/.lancedb/`)
Every Project Codebase tool **except `manage_daemon` and `get_config`** (`query_codebase`, `audit_layout_risks`, `get_styles_map`, `get_code_metrics`, `get_project_map`, `trace_event_flow`) runs an automatic pre-query check (`Fast Pre-Query Check`, ~10ms) backed by strongly-typed IPC events:
- **File-change detection (Stat Cache: `mtime + size + hash`)**: If project files were edited or created since the last ingest, the RAG transparently checks file metadata without opening disk files in $<5\text{ ms}$ and syncs only the changed deltas into LanceDB (~150ms) using parallel AST chunking before responding. The tool prepends: `[Auto-Sync: Actualizados X archivos modificados en LanceDB]`.
- **Schema version check (`SCHEMA_VERSION`)**: If any version or embedding model mismatch in `rag-local` is detected between the index metadata and `SCHEMA_VERSION` (e.g. `4.0.0`), the RAG automatically triggers a clean re-ingest.
- **Automatic Ignore Rules**: Scans automatically evaluate all `.gitignore` rules via `pathspec` (100% gitwildmatch compliance) plus standard noise directories (`vendor/`, `third_party/`, `.venv/`, `__pycache__/`, `.ruff_cache/`, `node_modules/`, `dist/`) and minified files (`*.min.js`, `*.min.css`, `*.bundle.js`).
- **Dynamic Watchdog**: Project queries run with standard timeouts and an inactivity watchdog (10 minutes between batch progress during re-ingestion) that automatically resets to a fresh 3-minute window once synchronization completes.

### 2. External Dependencies Cache (`~/.cache/rag-local/dependencies/`)
The global dependencies database operates under a **Package-Version Delta Strategy**:
- **Delta-Sync by Package & Version**: `ingest_dependencies` inspects lockfiles (`uv.lock`, `package.json`/`node_modules`) and queries the global LanceDB table. Packages already indexed with the same version and `DEPS_SCHEMA_VERSION` (e.g. `1.0.0`) are skipped instantly (**0.0s**). Only new or upgraded packages are extracted and vectorized.
- **Decoupled Lifecycle**: Modifying project application files does NOT trigger dependency re-ingestion, and updating or removing third-party dependencies does NOT invalidate the project codebase AST index.
- **On-Demand Extraction**: `query_dependency` queries the global cache. If a package is not yet indexed, it returns `NO_DEPENDENCY_FOUND`, allowing the agent to call `ingest_dependencies(project_path=..., package_name="<pkg>")` directly.
