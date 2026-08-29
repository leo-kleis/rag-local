from rag_local.daemon.models import ModelWorker


class ONNXRankResult:
    """Representa el resultado individual del reordenamiento semántico."""

    def __init__(self, doc_id: int, score: float) -> None:
        self.doc_id = doc_id
        self.score = score


class ONNXReranker:
    """Wrapper compatible para inferencia de reordenamiento con ONNX Runtime."""

    def __init__(self, worker: ModelWorker) -> None:
        self.worker = worker

    def rank(self, query: str, docs: list[str]) -> list[ONNXRankResult]:
        raw = self.worker.sync_rerank(query, docs)
        return [
            ONNXRankResult(doc_id=int(item["doc_id"]), score=float(item["score"]))
            for item in raw
        ]


_reranker: ONNXReranker | None = None


def get_reranker() -> ONNXReranker:
    """Obtiene o inicializa el Reranker ONNX en GPU de forma perezosa."""
    global _reranker
    if _reranker is None:
        from rag_local.services.embeddings import get_standalone_worker

        worker = get_standalone_worker()
        _reranker = ONNXReranker(worker)
    return _reranker
