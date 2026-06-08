# Use the official Astral uv image with Python 3.12 pre-installed
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Set the working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy the configuration files first to cache dependencies installation
COPY mise.toml pyproject.toml ./

# Install python dependencies using uv (fast install)
# Note: chromadb requires C/C++ compilation tools on some platforms,
# Debian-slim handles precompiled wheels for x86_64, avoiding gcc installation.
RUN uv pip install --system \
    google-genai>=0.1.0 \
    chromadb>=0.5.0 \
    python-dotenv>=1.0.1 \
    pydantic>=2.0.0 \
    fastmcp>=3.4.2

# Build the final runner stage
FROM python:3.12-slim-bookworm

WORKDIR /app

# Copy installed system site-packages from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy the RAG source code
COPY src/ ./src/
# Copy metadata/configurations (like .env and .gitignore)
COPY .env.example ./

# The container expects GEMINI_API_KEY as an env variable at runtime.
# ChromaDB requires a persistent volume or local directory mapped.
ENV GEMINI_API_KEY=""

# The MCP server runs over stdio (standard input/output),
# so we run the python script directly.
CMD ["python", "src/mcp_server.py"]
