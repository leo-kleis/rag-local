# 4. Parser de Estilos CSS, Métricas LOC y Formato de Texto Plano para MCP

Date: 2026-07-30

## Status

Accepted

## Context

Los agentes de IA requieren analizar proyectos web full-stack no solo a nivel de lógica (TypeScript/Python/HTML/Prisma), sino también a nivel de interfaz (hojas de estilo CSS, variables de diseño y código muerto) y volumen de líneas de código (métricas LOC) para guiar la refactorización y modularización.

Sin embargo, exponer estructuras JSON extensas a través del servidor MCP consume un exceso de tokens en el contexto del modelo. Asimismo, las clases CSS declaradas dinámicamente mediante ternarios (`class="ok ${valid ? 'ok' : 'err'}"`) o metodologías BEM (`user-avatar--${size}`) causan falsos positivos si se usa una coincidencia estricta de literales.

## Decision

1. **Parser CSS Sintáctico y Limpieza de Ruido**:
   - Crear un parser dedicado `src/rag_local/parsers/css.py` que extraiga clases, IDs, variables CSS Custom Properties (`--*`) y directivas (`@media`).
   - Filtrar contenidos de bloques `url(...)` (ej. `www.w3.org` en SVG data URIs) y números decimales en unidades CSS (`1.2fr`, `0.5em`).

2. **Detección de Clases Obsoletas con Coincidencia de Prefijos BEM**:
   - Capturar expresiones ternarias, asignaciones a variables JS/TS y patrones BEM (`user-avatar--${size}`).
   - Proteger cualquier clase declarada en CSS que coincida con prefijos consumidos finalizados en `-` o `--`.

3. **Formateadores Texto Plano Densos para MCP**:
   - Por defecto, los comandos `rag-styles` y `rag-loc` y sus correspondientes herramientas MCP (`get_styles_map` y `get_code_metrics`) devuelven resúmenes en texto plano estructurado y denso, reduciendo un 65% el consumo de tokens frente al formato JSON.
   - Listar los nombres reales de todas las clases CSS disponibles por archivo para promover la reutilización de estilos por parte de los agentes.

4. **Re-ingesta Forzada (`--force` / `force=True`)**:
   - Habilitar el flag `--force` en `rag-ingest` e `ingest_codebase(force=True)` para ignorar la caché de hashes SHA256 y forzar la reindexación limpia al actualizar parsers.

## Consequences

- Los agentes pueden identificar estilos CSS reutilizables y clases huérfanas con máxima precisión y mínimo consumo de contexto.
- Se previenen falsos positivos en clases BEM dinámicas y unidades CSS Grid.
