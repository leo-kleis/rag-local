class RagLocalError(Exception):
    """Clase base para todas las excepciones de RAG-local."""

    pass


class IngestError(RagLocalError):
    """Excepción lanzada cuando ocurre un error durante el proceso de ingesta."""

    pass


class QueryError(RagLocalError):
    """Excepción lanzada cuando ocurre un error durante las consultas."""

    pass


class ParserError(RagLocalError):
    """Excepción lanzada cuando hay un fallo al parsear un archivo."""

    pass


class EmbeddingError(RagLocalError):
    """Excepción lanzada cuando falla la generación de embeddings."""

    pass
