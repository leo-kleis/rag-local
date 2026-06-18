# 0003. Reordenamiento de Relevancia Usando Cross-Encoder ms-marco-MiniLM-L-6-v2

## Título
0003. Reordenamiento de Relevancia Usando Cross-Encoder ms-marco-MiniLM-L-6-v2

## Contexto
Las búsquedas vectoriales iniciales pueden recuperar documentos semánticamente cercanos en el espacio de embeddings pero que no responden con precisión a la pregunta del usuario. Se necesita un paso de reordenamiento que evalúe la relación exacta entre la consulta y cada fragmento recuperado.

## Decisión
Adoptamos el modelo ms-marco-MiniLM-L-6-v2 como Cross-Encoder para calcular un puntaje de relevancia preciso de los fragmentos recuperados en la fase de búsqueda inicial. A diferencia del Bi-Encoder, el Cross-Encoder procesa simultáneamente la consulta y el fragmento, capturando interacciones más profundas.

## Consecuencias
Obtenemos un orden de relevancia mucho más exacto antes de enviar los fragmentos al LLM, reduciendo el ruido en el prompt de contexto. Se produce un ligero incremento en la latencia de recuperación, lo cual representa un excelente balance entre precisión y velocidad en el entorno local.
