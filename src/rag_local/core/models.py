from pydantic import BaseModel


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
