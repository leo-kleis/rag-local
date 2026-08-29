from pathlib import Path

import pathspec

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
    path: Path, repo_root: Path, gitignore_patterns: list[str] | pathspec.PathSpec
) -> bool:
    """Comprueba si una ruta debe ser ignorada según los patrones de .gitignore."""
    try:
        rel_path = path.relative_to(repo_root)
    except ValueError:
        return False

    rel_path_str = str(rel_path).replace("\\", "/")
    if not rel_path_str:
        return False

    if isinstance(gitignore_patterns, pathspec.PathSpec):
        spec = gitignore_patterns
    else:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", gitignore_patterns)

    return spec.match_file(rel_path_str) or (
        not rel_path_str.endswith("/") and spec.match_file(f"{rel_path_str}/")
    )


def detect_project_roots(
    repo_root: Path,
) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    """Busca raíces de Angular, NestJS, Python y Next.js.

    Explora bajo repo_root con un máximo de 2 saltos de profundidad,
    podando inmediatamente directorios ignorados como .venv o node_modules.
    """
    angular_root = None
    nest_root = None
    python_root = None
    nextjs_root = None

    candidates: list[Path] = [repo_root]
    import contextlib

    with contextlib.suppress(Exception):
        for entry in repo_root.iterdir():
            if (
                entry.is_dir()
                and entry.name not in config.IGNORE_DIRS
                and not entry.name.startswith(".")
            ):
                candidates.append(entry)
                with contextlib.suppress(Exception):
                    for sub in entry.iterdir():
                        if (
                            sub.is_dir()
                            and sub.name not in config.IGNORE_DIRS
                            and not sub.name.startswith(".")
                        ):
                            candidates.append(sub)

    nextjs_signatures = ("next.config.ts", "next.config.js", "next.config.mjs")

    for dir_path in candidates:
        if angular_root is None and (dir_path / "angular.json").is_file():
            angular_root = dir_path
        if nest_root is None and (dir_path / "nest-cli.json").is_file():
            nest_root = dir_path
        if python_root is None and (dir_path / "pyproject.toml").is_file():
            python_root = dir_path
        if nextjs_root is None and any(
            (dir_path / sig).is_file() for sig in nextjs_signatures
        ):
            nextjs_root = dir_path

    return angular_root, nest_root, python_root, nextjs_root


def get_file_scope(
    file_path: Path,
    angular_root: Path | None,
    nest_root: Path | None,
    python_root: Path | None,
    nextjs_root: Path | None = None,
) -> str:
    """Determina si un archivo pertenece al scope de Angular, NestJS o Python.

    Cada proyecto ignora las subcarpetas que correspondan a raíces de otros
    proyectos detectados. Cuando hay superposición de raíces, gana el proyecto
    cuya raíz sea más específica (más profunda en la jerarquía).
    """
    abs_file = file_path.resolve()

    def is_in_project(file: Path, root: Path) -> bool:
        abs_root = root.resolve()
        return file == abs_root or abs_root in file.parents

    def claimed_by_more_specific(
        file: Path,
        current_root: Path,
        others: list[Path | None],
    ) -> bool:
        """True si otro proyecto más específico también contiene al archivo."""
        abs_current = current_root.resolve()
        for other in others:
            if other is None:
                continue
            abs_other = other.resolve()
            if abs_other == abs_current:
                continue
            more_specific = len(abs_other.parts) > len(abs_current.parts)
            if is_in_project(file, other) and more_specific:
                return True
        return False

    others_nextjs = [angular_root, nest_root, python_root]
    if (
        nextjs_root
        and is_in_project(abs_file, nextjs_root)
        and not claimed_by_more_specific(abs_file, nextjs_root, others_nextjs)
    ):
        return "nextjs-app"

    others_angular = [nest_root, python_root, nextjs_root]
    if (
        angular_root
        and is_in_project(abs_file, angular_root)
        and not claimed_by_more_specific(abs_file, angular_root, others_angular)
    ):
        return "angular"

    others_nest = [angular_root, python_root, nextjs_root]
    if (
        nest_root
        and is_in_project(abs_file, nest_root)
        and not claimed_by_more_specific(abs_file, nest_root, others_nest)
    ):
        return "nestjs"

    others_python = [angular_root, nest_root, nextjs_root]
    if (
        python_root
        and is_in_project(abs_file, python_root)
        and not claimed_by_more_specific(abs_file, python_root, others_python)
    ):
        return "python"

    raise ValueError(
        f"El archivo '{file_path}' no se encuentra dentro de ningún "
        f"proyecto detectado (Angular: {angular_root}, NestJS: {nest_root}, "
        f"Python: {python_root}, Next.js: {nextjs_root})."
    )


def scan_files() -> list[Path]:
    """Escanea recursivamente la raíz del repositorio buscando archivos de código."""
    import os

    files_to_process: list[Path] = []
    repo_root = config.REPO_ROOT
    allowed_exts = set(config.ALLOWED_EXTENSIONS)
    ignored_suffixes = tuple(config.IGNORED_FILE_SUFFIXES)
    ignored_dirs = set(config.IGNORE_DIRS)

    def scan_dir(
        current_dir: Path, active_gitignores: list[tuple[Path, pathspec.PathSpec]]
    ) -> None:
        local_gitignore = current_dir / ".gitignore"
        current_gitignores = list(active_gitignores)
        if local_gitignore.is_file():
            patterns = parse_gitignore(local_gitignore)
            if patterns:
                spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
                current_gitignores.append((current_dir, spec))

        try:
            with os.scandir(current_dir) as it:
                subdirs: list[Path] = []
                for entry in it:
                    name = entry.name
                    if name in ignored_dirs:
                        continue

                    entry_path = Path(entry.path)
                    ignored = False
                    for gitignore_dir, spec in current_gitignores:
                        if is_ignored_by_gitignore(entry_path, gitignore_dir, spec):
                            ignored = True
                            break
                    if ignored:
                        continue

                    try:
                        if entry.is_dir(follow_symlinks=False):
                            subdirs.append(entry_path)
                        elif entry.is_file(follow_symlinks=False):
                            name_lower = name.lower()
                            if any(name_lower.endswith(s) for s in ignored_suffixes):
                                continue
                            dot_idx = name.rfind(".")
                            if dot_idx != -1:
                                ext = name[dot_idx:]
                                if ext in allowed_exts:
                                    files_to_process.append(entry_path)
                    except OSError:
                        continue

                for subdir in subdirs:
                    scan_dir(subdir, current_gitignores)
        except OSError as e:
            logger.warning(f"No se pudo leer el directorio {current_dir}: {e}")

    scan_dir(repo_root, [])
    return files_to_process
