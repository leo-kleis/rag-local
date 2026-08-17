from pathlib import Path

from rag_local.core import config


def setup_project_context(project_path: str) -> None:
    """Configura dinámicamente el proyecto activo.

    Mutaciones en config.REPO_ROOT y config.LANCEDB_PATH.
    """
    from rag_local.services.scanner import detect_project_roots

    if not project_path or not project_path.strip():
        raise ValueError("El parámetro 'project_path' es obligatorio.")

    repo_path = Path(project_path).resolve()

    # Sanitizar y prevenir Path Traversal o accesos a directorios del sistema/raíz
    repo_path_str = str(repo_path)
    is_system_path = (
        ".gemini" in repo_path_str
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
            "Acceso denegado: La ruta especificada es un directorio "
            "del sistema o raíz de disco."
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
