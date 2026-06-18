# 0002. Segmentación Jerárquica Basada en AST con Tree-sitter

## Título
0002. Segmentación Jerárquica Basada en AST con Tree-sitter

## Contexto
Para indexar código fuente de TypeScript y archivos HTML de manera efectiva en el RAG, la segmentación puramente basada en caracteres o líneas rompe la estructura semántica de los bloques de código. Esto degrada la precisión de las búsquedas y la generación posterior.

## Decisión
Implementamos una estrategia de segmentación jerárquica basada en el Árbol de Sintaxis Abstracta usando tree-sitter para TypeScript e HTML. Analizamos el AST del código para identificar y extraer bloques lógicos completos, manteniendo su anidación y jerarquía.

## Consecuencias
Los fragmentos indexados conservan el contexto semántico completo del código y los metadatos de su jerarquía, lo que mejora drásticamente la calidad de las respuestas y evita recuperar fragmentos incompletos. Sin embargo, esto introduce complejidad en la lógica de procesamiento y dependencias adicionales en bibliotecas de tree-sitter, requiriendo compilaciones específicas según el lenguaje.
