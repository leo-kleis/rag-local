import fnmatch
from pathlib import Path

from rag_local.core import config
from rag_local.core.logging import logger


def get_relative_path(path: Path) -> str:
    """Retorna la ruta relativa al repositorio con barras inclinadas."""
    try:
        rel_path = path.relative_to(config.REPO_ROOT)
        return str(rel_path).replace("\\", "/")
    except ValueError:
        return str(path)


def parse_gitignore(gitignore_path: Path) -> list[str]:
    """Carga y procesa los patrones de exclusión de un archivo .gitignore."""
    if not gitignore_path.exists():
        return []
    patterns = []
    try:
        with open(gitignore_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    except Exception as e:
        logger.warning(
            f"No se pudo leer el archivo .gitignore en {gitignore_path}: {e}"
        )
    return patterns


def is_ignored_by_gitignore(
    path: Path, repo_root: Path, gitignore_patterns: list[str]
) -> bool:
    """Comprueba si una ruta debe ser ignorada según los patrones de .gitignore."""
    try:
        rel_path = path.relative_to(repo_root)
    except ValueError:
        return False

    rel_path_str = str(rel_path).replace("\\", "/")

    for pattern in gitignore_patterns:
        # Ignorar barras diagonales finales para directorios
        pat = pattern.rstrip("/")
        if not pat:
            continue

        # Si el patrón no contiene '/', buscar coincidencia
        # en cualquier segmento de la ruta
        if "/" not in pat:
            if any(fnmatch.fnmatch(part, pat) for part in rel_path.parts):
                return True
        else:
            # Si el patrón empieza con /, quitarlo para coincidencia relativa
            if pat.startswith("/"):
                pat = pat[1:]

            if fnmatch.fnmatch(rel_path_str, pat) or fnmatch.fnmatch(
                rel_path_str, f"{pat}/*"
            ):
                return True

            # Comprobar si coincide con alguna parte intermedia
            parts_str = rel_path_str.split("/")
            for i in range(len(parts_str)):
                sub_path = "/".join(parts_str[i:])
                if fnmatch.fnmatch(sub_path, pat) or fnmatch.fnmatch(
                    sub_path, f"{pat}/*"
                ):
                    return True
    return False


def detect_project_roots(
    repo_root: Path,
) -> tuple[Path | None, Path | None, Path | None]:
    """Busca recursivamente raíces de Angular, NestJS y Python bajo repo_root."""
    angular_root = None
    nest_root = None
    python_root = None

    # Buscar archivos firma angular.json, nest-cli.json y pyproject.toml
    for path in repo_root.rglob("angular.json"):
        parts = path.relative_to(repo_root).parts
        if not any(ignored in parts for ignored in config.IGNORE_DIRS):
            angular_root = path.parent
            break

    for path in repo_root.rglob("nest-cli.json"):
        parts = path.relative_to(repo_root).parts
        if not any(ignored in parts for ignored in config.IGNORE_DIRS):
            nest_root = path.parent
            break

    for path in repo_root.rglob("pyproject.toml"):
        parts = path.relative_to(repo_root).parts
        if not any(ignored in parts for ignored in config.IGNORE_DIRS):
            python_root = path.parent
            break

    return angular_root, nest_root, python_root


def get_file_scope(
    file_path: Path,
    angular_root: Path | None,
    nest_root: Path | None,
    python_root: Path | None,
) -> str:
    """Determina si un archivo pertenece al scope de Angular, NestJS o Python.

    Retorna 'frontend' (Angular), 'backend' (NestJS) o 'python' (Python).
    """
    abs_file = file_path.resolve()

    if angular_root:
        abs_angular = angular_root.resolve()
        if abs_file == abs_angular or abs_angular in abs_file.parents:
            return "frontend"

    if nest_root:
        abs_nest = nest_root.resolve()
        if abs_file == abs_nest or abs_nest in abs_file.parents:
            return "backend"

    if python_root:
        abs_python = python_root.resolve()
        if abs_file == abs_python or abs_python in abs_file.parents:
            return "python"

    raise ValueError(
        f"El archivo '{file_path}' no se encuentra dentro de ningún proyecto detectado "
        f"(Angular: {angular_root}, NestJS: {nest_root}, Python: {python_root})."
    )


def scan_files() -> list[Path]:
    """Escanea recursivamente la raíz del repositorio buscando archivos de código."""
    files_to_process: list[Path] = []
    repo_root = config.REPO_ROOT

    def scan_dir(
        current_dir: Path, active_gitignores: list[tuple[Path, list[str]]]
    ) -> None:
        try:
            rel_parts = current_dir.relative_to(repo_root).parts
        except ValueError:
            return

        # Filtrar por directorios ignorados en configuración
        if any(ignored in rel_parts for ignored in config.IGNORE_DIRS):
            return

        # Cargar y parsear las reglas del archivo .gitignore local si existe
        local_gitignore = current_dir / ".gitignore"
        current_gitignores = list(active_gitignores)
        if local_gitignore.is_file():
            patterns = parse_gitignore(local_gitignore)
            if patterns:
                current_gitignores.append((current_dir, patterns))

        try:
            entries = list(current_dir.iterdir())
        except OSError as e:
            logger.warning(f"No se pudo leer el directorio {current_dir}: {e}")
            return

        subdirs: list[Path] = []
        files: list[Path] = []

        for entry in entries:
            # Comprobar si coincide con las reglas de cualquier .gitignore
            # de la jerarquía
            ignored = False
            for gitignore_dir, patterns in current_gitignores:
                if is_ignored_by_gitignore(entry, gitignore_dir, patterns):
                    ignored = True
                    break

            if ignored:
                continue

            if entry.is_dir() and not entry.is_symlink():
                subdirs.append(entry)
            elif entry.is_file():
                files.append(entry)

        # Procesar los archivos del directorio actual
        for file_path in files:
            if file_path.suffix not in config.ALLOWED_EXTENSIONS:
                continue

            try:
                file_rel_parts = file_path.relative_to(repo_root).parts
            except ValueError:
                continue

            if any(ignored in file_rel_parts for ignored in config.IGNORE_DIRS):
                continue

            files_to_process.append(file_path)

        # Procesar recursivamente subdirectorios
        for subdir in subdirs:
            scan_dir(subdir, current_gitignores)

    scan_dir(repo_root, [])
    return files_to_process
