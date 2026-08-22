# Especificación Técnica: `query_dependency` (Inspección de Dependencias en LanceDB)

Este documento describe la arquitectura, diseño, estructura de datos y consideraciones técnicas para la futura implementación de la herramienta de consulta de dependencias externas en `rag-local`.

---

## 1. Motivación y Diagnóstico

En proyectos modernos (Python / TypeScript), los agentes de IA se enfrentan frecuentemente a dudas sobre las APIs, firmas de funciones, modelos y contratos de tipos de paquetes de terceros (ej. `twitchio`, `fastapi`, `lucide-react`, `preact`, `prisma`).

Actualmente, `rag-local` excluye deliberadamente las carpetas `.venv` y `node_modules` de la indexación principal (`monorepo_code`) mediante la configuración `IGNORE_DIRS`. Esto evita:
- Indexar decenas de miles de archivos irrelevantes de implementación interna de terceros.
- Tiempos de ingesta de 30+ minutos.
- Consumo excesivo de gigabytes en almacenamiento vectorial.
- Ruido y pérdida de precisión semántica en `query_codebase`.

Para resolver este punto ciego **sin violar la regla de que todas las consultas deben ejecutarse estrictamente contra LanceDB (cero lecturas a disco en tiempo de consulta)**, se diseña un subsistema de indexación y consulta selectiva de dependencias.

---

## 2. Arquitectura del Sistema

El flujo se divide en dos fases bien diferenciadas:

```
[ Ingesta de Dependencias (rag-ingest) ]
       │
       ▼
 [ Lectura de lockfile: uv.lock / pnpm-lock.yaml ]
       │
       ▼
 [ Filtrar solo DEPENDENCIAS DIRECTAS del proyecto ]
       │
       ▼
 [ Extracción selectiva de contratos AST (.d.ts / .pyi / firmas) ]
       │
       ▼
 [ Almacenamiento en tabla dedicada LanceDB: external_dependencies ]
       │
       ▼
 [ Consulta del Agente (query_dependency) ] ───▶ [ Búsqueda en LanceDB (Sin tocar disco) ]
```

---

## 3. Modelo de Datos en LanceDB

Se creará una tabla dedicada e independiente en LanceDB denominada `external_dependencies`, separada de `monorepo_code`:

```python
class DependencySymbol(LanceModel):
    id: str                         # "package:symbol_name" (ej. "twitchio:ChannelFollow")
    package_name: str               # "twitchio", "fastapi", "preact"
    language: str                   # "python" | "typescript"
    symbol_name: str                # "ChannelFollow", "useState", "BaseModel"
    symbol_type: str                # "class", "function", "interface", "type", "enum"
    signature: str                  # "class ChannelFollow(user_id: str, ...)"
    docstring: str                  # Descripción y parámetros extraídos
    declaration_text: str           # Definición completa del contrato de tipo
    source_module: str              # "twitchio.models"
```

---

## 4. Estrategia de Ingesta Selectiva

### A. Para Proyectos Python (`uv.lock` / `pyproject.toml`)
1. Se parsean las dependencias directas declaradas en `[project.dependencies]`.
2. Se localizan los stubs de tipo (`.pyi`) o los archivos de exportación pública en `.venv/lib/site-packages/<package>/`.
3. Tree-sitter analiza exclusivamente las declaraciones de clases públicas, funciones exportadas y docstrings.
4. Se ignora por completo el código de implementación interna, tests y assets de la librería.

### B. Para Proyectos TypeScript/JavaScript (`pnpm-lock.yaml` / `package.json`)
1. Se leen las `dependencies` directas en `package.json`.
2. Se inspecciona el campo `types` o `typings` del `package.json` de cada dependencia en `node_modules/<package>/`.
3. Tree-sitter procesa los archivos `.d.ts` asociados (ej. `index.d.ts`), extrayendo interfaces, tipos exportados, funciones y clases.

---

## 5. Control de Ingesta y Caché por Lockfile

Para garantizar **cero impacto en el tiempo de ingesta cotidiana**:
- Se almacena un hash criptográfico (SHA-256) de los lockfiles (`uv.lock` y `pnpm-lock.yaml`) en `meta.json`.
- Si el hash no ha cambiado respecto a la última ingesta, el paso de indexación de dependencias se **omite por completo (0 segundos)**.
- Solo cuando el usuario añade o actualiza paquetes (`uv add`, `pnpm add`), se recalculan los símbolos de las dependencias actualizadas (~5 a 15 segundos).

---

## 6. Diseño de la Herramienta MCP y CLI

### A. Comando CLI (`rag-dependency`)
```bash
uv run rag-dependency -p <ruta_proyecto> --package twitchio --symbol ChannelFollow
# O listar todos los símbolos exportados por una dependencia:
uv run rag-dependency -p <ruta_proyecto> --package twitchio
```

### B. Herramienta MCP (`query_dependency`)
```python
@mcp.tool()
async def query_dependency(
    ctx: Context,
    project_path: str,
    package_name: str,
    symbol_name: str | None = None,
) -> str:
    """Queries external dependency contracts and signatures directly from LanceDB.

    Retrieves type definitions, constructor signatures, and docstrings for
    third-party packages installed in the project without reading disk files.

    Args:
        project_path: Absolute path to the project repository.
        package_name: Name of the third-party package (e.g. 'twitchio', 'preact').
        symbol_name: Optional class, interface, or function name to inspect.
    """
```

### C. Salida Formateada para el Agente (Bajo Consumo de Tokens)
```text
[Dependency: twitchio | Symbol: ChannelFollow]
Type: class (twitchio.models)
Signature: class ChannelFollow(id: str, user_id: str, user_name: str, followed_at: str)

Description:
Represents a follower event subscription or channel follower record.

Methods / Properties:
  - id: str (The subscription ID)
  - user_id: str (ID of the follower user)
  - user_name: str (Display name of the follower)
  - followed_at: datetime (Timestamp when user followed)
```

---

## 7. Resumen de Impacto Técnico
- **Cero lecturas a disco en tiempo de consulta**: Las consultas se resuelven contra la tabla `external_dependencies` en LanceDB.
- **Sin contaminación semántica**: `query_codebase` sigue buscando exclusivamente sobre `monorepo_code` (código de usuario).
- **Almacenamiento liviano**: Entre 2 MB y 8 MB para las dependencias directas de un proyecto estándar.
- **Rendimiento**: Consultas instantáneas (< 20 ms) desde LanceDB.
