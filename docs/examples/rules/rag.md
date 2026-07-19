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

1. **`get_config`** — verifica si el índice existe (`LANCEDB_INDEXADA: Sí/No`).
2. **`ingest_codebase`** — si el índice no existe o el usuario lo aprueba tras cambios estructurales. **No ejecutar de forma autónoma — proponer y esperar confirmación.**
3. **`get_project_map`** — obtén el mapa de clases, servicios y modelos del proyecto. Úsalo para conocer los nombres exactos que usa el código.
4. **`query_codebase`** — con los nombres encontrados en el mapa, obtén el código real.

```
Ejemplo:
  get_project_map() →
    [nestjs] Controllers: AuthController, BillingController
             Services: BillingService, UserService
    [nestjs/prisma] Models: User, Order, Payment

  → Ahora sé que debo buscar: query_codebase("BillingService PaymentController")
```

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

## Parámetros de la herramienta

```
query_codebase(
    query: str,          # Términos de búsqueda en inglés, usando nombres del código
    scope: str | None,   # "angular" | "nestjs" | "python" — filtra por framework
    project_path: str    # Ruta absoluta del workspace actual (OBLIGATORIO)
)
```

- **`project_path`**: Siempre pasa la ruta absoluta del workspace. Sin esto el RAG no sabe qué base de datos abrir.
- **`scope`**: Úsalo cuando sabes que la respuesta está en un framework específico. Reduce ruido y mejora precisión.
- **`query`**: En inglés. Usa los nombres exactos del código cuando los conoces.

---

## Mantener el índice actualizado

- Revisa la configuración con `get_config` antes de consultar, para saber si el índice existe.
- Si creas archivos nuevos o modificas estructuras significativas (clases, modelos Prisma, módulos Angular/NestJS), **propón al usuario ejecutar `ingest_codebase` y espera su confirmación antes de hacerlo**. No lo ejecutes de forma autónoma.