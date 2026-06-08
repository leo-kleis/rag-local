import json

from fastmcp import FastMCP

# Import modular RAG functions
from core_rag import generate_response, query_db
from ingest import run_ingestion

# Initialize the FastMCP server
mcp = FastMCP(
    name="Monorepo RAG Server",
    description="MCP Server exposing semantic search capabilities for Angular and NestJS codebase."
)


@mcp.tool
def query_codebase(query: str, scope: str | None = None) -> str:
    """Queries the local codebase (Angular frontend, NestJS backend, Prisma schema) semantically.

    Retrieves the most relevant code blocks and generates an analytical answer in English.

    Args:
        query: The natural language search query or technical question.
        scope: Optional filter. Use 'frontend' for Angular, 'backend' for NestJS/Fastify/Prisma,
          or omit to search both.
    """
    try:
        # 1. Retrieve most relevant code chunks from ChromaDB
        results = query_db(query, scope)
        
        # 2. Call Gemini API to generate the response (always in English for AI consumption)
        answer = generate_response(query, results, respond_in_english=True)
        return answer
    except Exception as e:
        return f"Error executing codebase query: {e}"


@mcp.tool
def index_codebase() -> str:
    """Triggers a full scan and indexing of the monorepo codebase.

    Scans for .ts, .html, and .prisma files, generates embeddings using gemini-embedding-2,
    and updates the local vector database.
    """
    try:
        # Run the ingestion script logic
        stats = run_ingestion()
        return json.dumps(stats, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, indent=2, ensure_ascii=False)


# Standard entrypoint to run stdio server
if __name__ == "__main__":
    mcp.run()
