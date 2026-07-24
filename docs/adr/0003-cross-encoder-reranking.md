# 0003. Reordenamiento de Relevancia Usando Cross-Encoder BAAI/bge-reranker-base

## Título
0003. Reordenamiento de Relevancia Usando Cross-Encoder BAAI/bge-reranker-base

## Contexto
Las búsquedas vectoriales iniciales pueden recuperar documentos semánticamente cercanos en el espacio de embeddings pero que no responden con precisión a la pregunta del usuario. Se necesita un paso de reordenamiento que evalúe la relación exacta entre la consulta y cada fragmento recuperado.

## Decisión
Adoptamos el modelo `BAAI/bge-reranker-base` como Cross-Encoder para calcular un puntaje de relevancia preciso de los fragmentos recuperados en la fase de búsqueda inicial. A diferencia del Bi-Encoder, el Cross-Encoder procesa simultáneamente la consulta y el fragmento, capturando interacciones más profundas con soporte multilingüe completo (español e inglés) y optimizado para GPUs locales como la NVIDIA GTX 1080 Ti.

## Consecuencias
Obtenemos un orden de relevancia mucho más exacto antes de enviar los fragmentos al LLM, reduciendo el ruido en el prompt de contexto. La latencia de inferencia en GPU (~25 ms para el top-30 de candidatos) representa un balance óptimo entre precisión multilingüe y velocidad en el entorno local.
