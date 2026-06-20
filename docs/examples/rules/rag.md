---
trigger: always_on
glob:
description: "Reglas para utilizar el RAG local (rag-local)"
---

# Reglas de Uso de RAG Local (rag-local)

El proyecto cuenta con un RAG local embebido basado en LanceDB y la API de Gemini. Sigue estas directrices estrictas para su consumo:

- **Búsquedas Semánticas y de Texto**: Utiliza preferentemente la herramienta MCP `query_codebase` en lugar de realizar múltiples comandos `grep` recursivos secuenciales. El RAG combina similitud semántica y Full-Text Search (FTS).
- **Aislamiento de Base de Datos Vectorial**: Al invocar cualquier herramienta (`query_codebase`, `ingest_codebase`, `get_config`), es obligatorio pasar la ruta absoluta del workspace actual en el parámetro `project_path`. Esto le permite al RAG levantar/crear la base de datos de LanceDB en `<project_path>/.lancedb/`.
- **Mantener el Índice Actualizado**: Si creas nuevos archivos o aplicas modificaciones estructurales significativas (en Angular, NestJS, Prisma o Python), ejecuta la herramienta `ingest_codebase` para regenerar los hashes de caché e indexar el código de forma incremental.
- **Idioma y Filtros de Búsqueda**: 
  - Formula la consulta (`query`) en inglés para obtener mayor coincidencia semántica y reducir tokens.
  - Filtra mediante el parámetro `scope` (`'frontend'` para Angular, `'backend'` para NestJS/Prisma, o `'python'` para Python) para evitar ruido de resultados de otros entornos de ejecución.