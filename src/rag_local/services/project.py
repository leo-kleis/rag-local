from pathlib import Path, PureWindowsPath

from rag_local.core import config


def resolve_container_project_path(project_path: str | Path) -> Path:
    """Resuelve la ruta del proyecto traduciendo rutas del host en contenedor."""
    p = Path(project_path)
    if p.exists():
        return p.resolve()

    # Si estamos en un contenedor con /workspaces montado
    workspaces_dir = Path("/workspaces")
    if workspaces_dir.is_dir():
        # Descomponer partes independientemente de si la ruta viene en formato
        # Windows o POSIX
        raw_str = str(project_path).replace("\\", "/")
        pure_win = PureWindowsPath(project_path)
        win_parts = [
            part
            for part in pure_win.parts
            if part and not part.endswith(":") and part not in ("\\", "/")
        ]
        posix_parts = [
            part for part in raw_str.split("/") if part and not part.endswith(":")
        ]

        for parts in (win_parts, posix_parts):
            for i in range(len(parts) - 1, -1, -1):
                candidate = workspaces_dir.joinpath(*parts[i:])
                if candidate.exists():
                    return candidate.resolve()

    return p.resolve()


def setup_project_context(project_path: str) -> None:
    """Configura dinámicamente el proyecto activo.

    Mutaciones en config.REPO_ROOT y config.LANCEDB_PATH.
    """
    from rag_local.services.scanner import detect_project_roots

    if not project_path or not project_path.strip():
        raise ValueError(
            "El parámetro 'project_path' es obligatorio. "
            "Proporciona la ruta absoluta al directorio del workspace."
        )

    repo_path = resolve_container_project_path(project_path)

    # Sanitizar y prevenir Path Traversal o accesos a directorios del sistema/raíz/home
    repo_path_str = str(repo_path)
    try:
        is_user_home = repo_path == Path.home() or repo_path == Path.home().parent
    except Exception:
        is_user_home = False

    is_system_path = (
        is_user_home
        or ".gemini" in repo_path_str
        or "AppData" in repo_path_str
        or "Windows" in repo_path_str
        or "Program Files" in repo_path_str
        or "System32" in repo_path_str
        or "Temp" in repo_path_str
        or repo_path_str == "/"
        or repo_path_str.endswith(":\\")
    )
    if is_system_path:
        raise ValueError(
            "Acceso denegado: La ruta especificada es un directorio personal (~), "
            "del sistema o raíz de disco. Proporciona un 'project_path' válido."
        )

    # Redireccionar repo_path al root real del monorepo si se detectan en subdirectorios
    angular_root, nest_root, python_root, nextjs_root = detect_project_roots(repo_path)
    if angular_root and angular_root != repo_path:
        repo_path = angular_root.parent
    elif nest_root and nest_root != repo_path:
        repo_path = nest_root.parent
    elif python_root and python_root != repo_path:
        repo_path = python_root
    elif nextjs_root and nextjs_root != repo_path:
        repo_path = nextjs_root

    # Validar que exista la ruta
    if not repo_path.exists():
        raise FileNotFoundError(f"La ruta especificada no existe: {repo_path}")
    if not repo_path.is_dir():
        raise NotADirectoryError(
            f"La ruta especificada no es un directorio: {repo_path}"
        )

    config.REPO_ROOT = repo_path
    config.LANCEDB_PATH = repo_path / ".lancedb"
