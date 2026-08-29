from typing import TYPE_CHECKING, Any

from lancedb.pydantic import LanceModel, Vector

if TYPE_CHECKING:
    VectorType = Any
else:
    VectorType = Vector(1024)


class CodeChunk(LanceModel):
    id: str
    vector: VectorType
    text: str
    source: str
    scope: str
    start_line: int
    end_line: int
    class_name: str = ""
    method_name: str = ""
    imports: str = ""
    dependencies: str = ""
    tags: str = ""
    title: str = ""
    type: str = ""
    models: str = ""
    directives: str = ""
    lines_code: int = 0
    css_rules: str = ""
    class_parents: str = ""
    payload_schema: str = ""


class CodeRelationship(LanceModel):
    id: str
    source_file: str
    target_symbol: str
    relationship_type: str


class DependencySymbol(LanceModel):
    id: str
    vector: VectorType
    language: str
    package_manager: str
    package_name: str
    package_version: str
    source_module: str = ""
    symbol_name: str
    symbol_type: str = ""
    signature: str = ""
    docstring: str = ""
    declaration_text: str = ""
