import os
import sys

from fastmcp import Context

from rag_local.core import config as core_config
from rag_local.mcp.server import get_lock, mcp
from rag_local.services.project import setup_project_context
from rag_local.services.subprocess import run_cli_subprocess


@mcp.tool()
async def query_dependency(
    ctx: Context,
    project_path: str,
    package_name: str,
    symbol_name: str | None = None,
    query: str | None = None,
    language: str | None = None,
    limit: int = 5,
) -> str:
    """Queries external dependency contracts, signatures, and types from LanceDB.

    Retrieves type definitions, constructor parameters, interfaces, and docstrings
    for third-party packages installed in the project without reading disk files.

    Args:
        project_path: Absolute path to the project repository.
        package_name: Name of the third-party package (e.g. 'twitchio', 'preact').
        symbol_name: Optional class, interface, or function name to inspect.
        query: Optional semantic search query (e.g. 'oauth2 password bearer').
        language: Optional language filter ('python' or 'typescript').
        limit: Maximum number of symbol definitions to return (default 5).
    """
    async with get_lock():
        try:
            await ctx.report_progress(10, 100, message="Cargando configuración...")
            setup_project_context(project_path)
        except Exception as e:
            return f"Error de configuración: {e!s}"

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
            cmd = [
                sys.executable,
                "-m",
                "rag_local.cli.dependencies",
                "query",
                "--project-path",
                repo_path,
                "--package",
                package_name,
                "--limit",
                str(limit),
            ]
            if symbol_name and symbol_name.strip():
                cmd.extend(["--symbol", symbol_name.strip()])
            if query and query.strip():
                cmd.extend(["--query", query.strip()])
            if language and language.strip():
                cmd.extend(["--lang", language.strip()])

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            await ctx.report_progress(
                30, 100, message="Consultando base de datos de dependencias..."
            )
            res = await run_cli_subprocess(
                cmd=cmd,
                cwd=repo_path,
                env=env,
                timeout=core_config.DEFAULT_CLI_TIMEOUT,
            )

            if res.returncode == 0:
                await ctx.report_progress(100, 100, message="Contracts retrieved.")
                return res.stdout.decode("utf-8", errors="replace").strip()
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace").strip()
                if not err_msg:
                    err_msg = res.stdout.decode("utf-8", errors="replace").strip()
                return f"Failed to query dependencies: {err_msg}"
        except Exception as e:
            return f"Error while querying dependencies in RAG: {e!s}"


@mcp.tool()
async def ingest_dependencies(
    ctx: Context,
    project_path: str,
    package_name: str | None = None,
    language: str | None = None,
    force: bool = False,
) -> str:
    """Ingests and extracts third-party type contracts into the global cache.

    Scans project dependency lockfiles and extracts signatures, interfaces,
    constructors, and docstrings into LanceDB user-level global cache.

    Args:
        project_path: Absolute path to the project repository.
        package_name: Optional package filter to ingest a single library.
        language: Optional language filter ('python' or 'typescript').
        force: If True, forces re-extraction even if already cached.
    """
    async with get_lock():
        try:
            await ctx.report_progress(10, 100, message="Loading project context...")
            setup_project_context(project_path)
        except Exception as e:
            return f"Configuration error: {e!s}"

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
            cmd = [
                sys.executable,
                "-m",
                "rag_local.cli.ingest_deps",
                "--project-path",
                repo_path,
            ]
            if package_name and package_name.strip():
                cmd.extend(["--package", package_name.strip()])
            if language and language.strip():
                cmd.extend(["--lang", language.strip()])
            if force:
                cmd.append("--force")

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            async def handle_stderr_line(line: str) -> None:
                from rag_local.core.events import parse_sync_event

                event = parse_sync_event(line)
                if event is not None and event.message:
                    await ctx.report_progress(
                        event.progress, 100, message=event.message
                    )

            res = await run_cli_subprocess(
                cmd=cmd,
                cwd=repo_path,
                env=env,
                is_ingestion=True,
                timeout=core_config.DEFAULT_CLI_TIMEOUT * 2,
                on_stderr_line=handle_stderr_line,
            )

            if res.returncode == 0:
                await ctx.report_progress(100, 100, message="Ingestion complete.")
                out = res.stdout.decode("utf-8", errors="replace").strip()
                return out or "Dependency ingestion completed."
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace").strip()
                if not err_msg:
                    err_msg = res.stdout.decode("utf-8", errors="replace").strip()
                return f"Dependency ingestion error: {err_msg}"
        except Exception as e:
            return f"Error while ingesting dependencies in RAG: {e!s}"


@mcp.tool()
async def manage_dependencies(
    ctx: Context,
    project_path: str,
    action: str = "status",
    package_name: str | None = None,
    version: str | None = None,
    language: str | None = None,
) -> str:
    """Manages the global dependencies database and reports project status.

    Checks synchronization status, removes specific packages, or purges the
    entire global third-party library cache in LanceDB.

    Args:
        project_path: Absolute path to the project repository.
        action: Management action ('status', 'remove', or 'clean').
        package_name: Name of the package to remove (for action 'remove').
        version: Optional specific package version to remove.
        language: Optional language filter ('python' or 'typescript').
    """
    async with get_lock():
        try:
            await ctx.report_progress(10, 100, message="Loading project context...")
            setup_project_context(project_path)
        except Exception as e:
            return f"Configuration error: {e!s}"

        try:
            repo_path = str(core_config.REPO_ROOT.resolve())
            act = action.strip().lower()

            if act == "status":
                cmd = [
                    sys.executable,
                    "-m",
                    "rag_local.cli.dependencies",
                    "status",
                    "--project-path",
                    repo_path,
                ]
            elif act == "remove":
                if not package_name or not package_name.strip():
                    return "Error: package_name is required for action 'remove'."
                cmd = [
                    sys.executable,
                    "-m",
                    "rag_local.cli.dependencies",
                    "remove",
                    "--package",
                    package_name.strip(),
                ]
                if version and version.strip():
                    cmd.extend(["--version", version.strip()])
                if language and language.strip():
                    cmd.extend(["--lang", language.strip()])
            elif act == "clean":
                cmd = [
                    sys.executable,
                    "-m",
                    "rag_local.cli.dependencies",
                    "clean",
                    "--all",
                ]
            else:
                return f"Invalid action '{action}'. Use status, remove, or clean."

            env = os.environ.copy()
            env["RAG_REPO_ROOT"] = repo_path
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            await ctx.report_progress(30, 100, message=f"Running {act}...")
            res = await run_cli_subprocess(
                cmd=cmd,
                cwd=repo_path,
                env=env,
                timeout=core_config.DEFAULT_CLI_TIMEOUT,
            )

            if res.returncode == 0:
                await ctx.report_progress(100, 100, message="Completed.")
                return res.stdout.decode("utf-8", errors="replace").strip()
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace").strip()
                if not err_msg:
                    err_msg = res.stdout.decode("utf-8", errors="replace").strip()
                return f"Failed to manage dependencies: {err_msg}"
        except Exception as e:
            return f"Error while managing dependencies in RAG: {e!s}"
