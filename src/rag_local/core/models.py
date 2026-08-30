from enum import StrEnum

from pydantic import BaseModel


class EmbeddingTask(StrEnum):
    NL2CODE_QUERY = "nl2code_query"
    NL2CODE_DOCUMENT = "nl2code_document"
    CODE2CODE_QUERY = "code2code_query"
    CODE2CODE_DOCUMENT = "code2code_document"
    CODE2NL_QUERY = "code2nl_query"
    CODE2NL_DOCUMENT = "code2nl_document"
    CODE2COMPLETION_QUERY = "code2completion_query"
    CODE2COMPLETION_DOCUMENT = "code2completion_document"
    QA_QUERY = "qa_query"
    QA_DOCUMENT = "qa_document"
    RAW = "raw"


TASK_PREFIXES: dict[str, str] = {
    EmbeddingTask.NL2CODE_QUERY: (
        "Find the most relevant code snippet given the following query:\n"
    ),
    EmbeddingTask.NL2CODE_DOCUMENT: "Candidate code snippet:\n",
    EmbeddingTask.CODE2CODE_QUERY: (
        "Find an equivalent code snippet given the following code snippet:\n"
    ),
    EmbeddingTask.CODE2CODE_DOCUMENT: "Candidate code snippet:\n",
    EmbeddingTask.CODE2NL_QUERY: (
        "Find the most relevant comment given the following code snippet:\n"
    ),
    EmbeddingTask.CODE2NL_DOCUMENT: "Candidate comment:\n",
    EmbeddingTask.CODE2COMPLETION_QUERY: (
        "Find the most relevant completion given the following start of code snippet:\n"
    ),
    EmbeddingTask.CODE2COMPLETION_DOCUMENT: "Candidate completion:\n",
    EmbeddingTask.QA_QUERY: (
        "Find the most relevant answer given the following question:\n"
    ),
    EmbeddingTask.QA_DOCUMENT: "Candidate answer:\n",
    EmbeddingTask.RAW: "",
}


class ChunkMetadata(BaseModel):
    class_name: str = ""
    method_name: str = ""
    imports: list[str] | str = ""
    dependencies: list[str] | str = ""
    tags: list[str] | str = ""
    title: str = ""
    type: str = ""
    models: list[str] | str = ""
    directives: list[str] | str = ""
    lines_code: int = 0
    css_rules: str = ""
    class_parents: str = ""
    payload_schema: str = ""

    def __getitem__(self, item):
        try:
            return getattr(self, item)
        except AttributeError:
            raise KeyError(item) from None

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, item, default=None):
        return getattr(self, item, default)

    def __contains__(self, item):
        return hasattr(self, item)


class Chunk(BaseModel):
    text: str
    start_line: int
    end_line: int
    metadata: ChunkMetadata
    source: str = ""
    scope: str = ""

    def __getitem__(self, item):
        try:
            return getattr(self, item)
        except AttributeError:
            raise KeyError(item) from None

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, item, default=None):
        return getattr(self, item, default)

    def __contains__(self, item):
        return hasattr(self, item)
