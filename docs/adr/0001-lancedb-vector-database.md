# 0001. LanceDB como Base de Datos de Vectores Embebida

## Título
0001. LanceDB como Base de Datos de Vectores Embebida

## Contexto
Se requiere una base de datos vectorial local para almacenar y consultar los embeddings de fragmentos de código del sistema RAG. La base de datos debe ser rápida, fácil de configurar, libre de dependencias externas y compatible con búsquedas híbridas.

## Decisión
Adoptamos LanceDB en su modalidad embebida sin servidor. LanceDB se integra directamente en el proceso de Python y almacena los datos en formato binario Lance optimizado para accesos aleatorios rápidos y escaneos de columnas. Ofrece soporte nativo para búsquedas de texto completo mediante Tantivy y búsquedas vectoriales, lo cual facilita la búsqueda híbrida sin infraestructura extra.

## Consecuencias
Simplifica significativamente la arquitectura al no requerir un servidor de base de datos independiente. Mejora la velocidad de lectura y el rendimiento de búsqueda. Como contrapartida, las escrituras concurrentes desde múltiples procesos requieren coordinación, pero dado el flujo de trabajo de indexación offline y consulta de este asistente de código local, esto no representa un problema.
