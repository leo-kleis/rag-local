# Stage 1: Builder
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy
ENV UV_COMPILE_BYTECODE=1
ENV UV_PYTHON_INSTALL_DIR=/opt/python
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Instalar dependencias para la compilación y descarga
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copiar binarios oficiales de uv
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

WORKDIR /app

# Descargar e instalar Python 3.12 independiente mediante uv
RUN uv python install 3.12

# Copiar archivos de definición de dependencias
COPY pyproject.toml uv.lock ./

# Instalar dependencias en /opt/venv usando cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copiar el código fuente e instalar el proyecto
COPY src ./src
COPY README.md ./README.md
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# Stage 2: Runtime
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 AS runtime

LABEL org.opencontainers.image.title="rag-local"
LABEL org.opencontainers.image.description="RAG local sobre código fuente con soporte GPU NVIDIA"
LABEL org.opencontainers.image.source="https://github.com/leo-kleis/rag-local"
LABEL org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUTF8=1
ENV UV_PYTHON_INSTALL_DIR=/opt/python
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV RAG_REPO_ROOT="/workspaces"

# Rutas de caché para usuario non-root (independiente de /root/)
ENV HF_HOME=/app/.cache/huggingface
ENV TORCH_HOME=/app/.cache/torch
ENV TZ="America/Santiago"

# Instalar librerías de sistema: tini (init process), OpenMP, git, certificados, tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgomp1 \
    git \
    curl \
    tini \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario y grupo no privilegiado y directorios de trabajo
RUN groupadd -r raggroup && \
    useradd -r -g raggroup -d /app -s /sbin/nologin raguser && \
    mkdir -p /app/.cache/huggingface /app/.cache/torch /app/.cache/daemon /app/.cache/rag-local && \
    chown -R raguser:raggroup /app

# Copiar uv, el entorno virtual y Python instalados con permisos de usuario directo
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/
COPY --from=builder --chown=raguser:raggroup /opt/python /opt/python
COPY --from=builder --chown=raguser:raggroup /opt/venv /opt/venv
COPY --from=builder --chown=raguser:raggroup /app /app

WORKDIR /app

# Puertos para FastMCP HTTP/SSE (8000) y WorkerDaemon (21239)
EXPOSE 8000 21239

# tini como PID 1: reenvía señales POSIX (SIGTERM) correctamente y hace reaping de zombies
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]

# Cambiar al usuario non-root antes de ejecutar
USER raguser

# Comando de inicio predeterminado (FastMCP)
CMD ["rag-mcp"]
