import contextlib
import json
import re
import tomllib
from pathlib import Path

from rag_local.core.logging import logger


def _normalize_pkg_name(name: str) -> str:
    """Normaliza el nombre de un paquete (ej. 'twitchio[starlette]' -> 'twitchio')."""
    cleaned = re.split(r"[\[\<\>\=\~\!\;]", name)[0].strip()
    return cleaned.lower().replace("_", "-")


def _find_candidate_files(project_path: Path, filename: str) -> list[Path]:
    """Busca archivos de configuración en la raíz y subdirectorios de monorepos."""
    found: list[Path] = []
    root_file = project_path / filename
    if root_file.is_file():
        found.append(root_file)

    ignored_prefixes = (".", "node_modules", "dist", "vendor", "__pycache__")
    for sub in project_path.iterdir():
        if not sub.is_dir() or sub.name.startswith(ignored_prefixes):
            continue
        sub_file = sub / filename
        if sub_file.is_file() and sub_file not in found:
            found.append(sub_file)
        # Buscar en segundo nivel (ej. apps/web, packages/core)
        for nested in sub.iterdir():
            if not nested.is_dir() or nested.name.startswith(ignored_prefixes):
                continue
            nested_file = nested / filename
            if nested_file.is_file() and nested_file not in found:
                found.append(nested_file)
    return found


def detect_python_dependencies(project_path: Path) -> dict[str, str]:
    """Detecta las dependencias directas de Python y sus versiones resueltas."""
    pyproject_files = _find_candidate_files(project_path, "pyproject.toml")
    uv_lock_files = _find_candidate_files(project_path, "uv.lock")

    direct_deps: set[str] = set()
    for pyproject_file in pyproject_files:
        try:
            with open(pyproject_file, "rb") as f:
                data = tomllib.load(f)
            deps_list = data.get("project", {}).get("dependencies", [])
            for dep in deps_list:
                pkg_name = _normalize_pkg_name(dep)
                if pkg_name:
                    direct_deps.add(pkg_name)
        except Exception as e:
            logger.warning(f"Error al leer {pyproject_file}: {e}")

    resolved_versions: dict[str, str] = {}
    for uv_lock_file in uv_lock_files:
        try:
            with open(uv_lock_file, "rb") as f:
                lock_data = tomllib.load(f)
            packages = lock_data.get("package", [])
            for pkg in packages:
                raw_name = pkg.get("name", "")
                norm_name = _normalize_pkg_name(raw_name)
                version = pkg.get("version", "")
                if (direct_deps and norm_name in direct_deps) or (
                    not direct_deps and norm_name and version
                ):
                    resolved_versions[norm_name] = version
        except Exception as e:
            logger.warning(f"Error al parsear {uv_lock_file}: {e}")

    # Fallback si no se encontró en lockfiles
    if direct_deps:
        for dep in direct_deps:
            if dep not in resolved_versions:
                resolved_versions[dep] = "latest"

    return resolved_versions


def detect_node_dependencies(project_path: Path) -> dict[str, str]:
    """Detecta las dependencias directas de Node/TypeScript en proyectos y monorepos."""
    package_json_files = _find_candidate_files(project_path, "package.json")
    if not package_json_files:
        return {}

    # Encontrar todos los directorios node_modules disponibles
    node_modules_candidates: list[Path] = []
    root_nm = project_path / "node_modules"
    if root_nm.is_dir():
        node_modules_candidates.append(root_nm)
    for p in project_path.glob("*/node_modules"):
        if p.is_dir() and p not in node_modules_candidates:
            node_modules_candidates.append(p)
    for p in project_path.glob("*/*/node_modules"):
        if p.is_dir() and p not in node_modules_candidates:
            node_modules_candidates.append(p)

    resolved_versions: dict[str, str] = {}
    for pjson in package_json_files:
        try:
            with open(pjson, encoding="utf-8", errors="replace") as f:
                data = json.load(f)

            deps = data.get("dependencies", {})
            for pkg_name, version_spec in deps.items():
                exact_version = ""
                # Intentar resolver la versión exacta desde los node_modules detectados
                for nm in node_modules_candidates:
                    pkg_json_in_nm = nm / pkg_name / "package.json"
                    if pkg_json_in_nm.is_file():
                        with (
                            contextlib.suppress(Exception),
                            open(
                                pkg_json_in_nm,
                                encoding="utf-8",
                                errors="replace",
                            ) as nf,
                        ):
                            installed_data = json.load(nf)
                            exact_version = installed_data.get("version", "")
                            if exact_version:
                                break
                if not exact_version:
                    exact_version = re.sub(r"^[\^~>=<]+", "", str(version_spec)).strip()
                resolved_versions[pkg_name] = exact_version or "latest"
        except Exception as e:
            logger.warning(f"Error al leer {pjson}: {e}")

    return resolved_versions


def detect_project_dependencies(project_path: Path) -> dict[str, dict[str, str]]:
    """Detecta todas las dependencias directas del proyecto agrupadas por lenguaje."""
    return {
        "python": detect_python_dependencies(project_path),
        "typescript": detect_node_dependencies(project_path),
    }
