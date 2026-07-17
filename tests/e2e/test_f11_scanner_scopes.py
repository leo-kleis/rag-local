import pytest
from pathlib import Path

from rag_local.services.scanner import detect_project_roots, get_file_scope


def test_scanner_detect_project_roots_limited_to_2_depth(setup_test_env):
    repo_root = setup_test_env["repo_root"]

    # Limpiar archivos firma creados por el fixture
    for path in repo_root.rglob("angular.json"):
        path.unlink()
    for path in repo_root.rglob("nest-cli.json"):
        path.unlink()
    for path in repo_root.rglob("pyproject.toml"):
        path.unlink()

    # 1. Archivo firma en nivel 1 (1 salto)
    (repo_root / "pyproject.toml").write_text("{}", encoding="utf-8")
    # 2. Archivo firma en nivel 2 (2 saltos)
    (repo_root / "frontend" / "angular.json").write_text("{}", encoding="utf-8")
    # 3. Archivo firma en nivel 3 (3 saltos) -> Debe ser ignorado
    deep_dir = repo_root / "backend" / "subfolder"
    deep_dir.mkdir(parents=True, exist_ok=True)
    (deep_dir / "nest-cli.json").write_text("{}", encoding="utf-8")

    angular_root, nest_root, python_root = detect_project_roots(repo_root)

    # El python_root debe ser repo_root (1 salto)
    assert python_root == repo_root
    # El angular_root debe ser repo_root / frontend (2 saltos)
    assert angular_root == repo_root / "frontend"
    # El nest_root debe ser None (3 saltos está fuera del límite de 2 saltos)
    assert nest_root is None


def test_scanner_scope_assignment_with_nesting(setup_test_env):
    repo_root = setup_test_env["repo_root"]

    # Definir raíces para el test (anidadas)
    angular_root = repo_root  # Angular en la raíz
    python_root = repo_root / "api-python"  # Python en una subcarpeta
    nest_root = repo_root / "backend"  # NestJS en una subcarpeta

    # Crear subcarpetas y archivos físicamente para que .resolve() funcione
    python_root.mkdir(parents=True, exist_ok=True)
    nest_root.mkdir(parents=True, exist_ok=True)

    f_angular = repo_root / "app.component.ts"
    f_angular.touch()

    f_python = python_root / "main.py"
    f_python.touch()

    f_nest = nest_root / "main.ts"
    f_nest.touch()

    # Caso 1: Archivo en la raíz del frontend de Angular
    scope_angular = get_file_scope(
        f_angular, angular_root, nest_root, python_root
    )
    assert scope_angular == "angular"

    # Caso 2: Archivo de Python dentro de la subcarpeta de la API de Python
    scope_python = get_file_scope(
        f_python, angular_root, nest_root, python_root
    )
    assert scope_python == "python"

    # Caso 3: Archivo de NestJS dentro de la subcarpeta de backend
    scope_nest = get_file_scope(f_nest, angular_root, nest_root, python_root)
    assert scope_nest == "nestjs"

    # Caso 4: Archivo huérfano fuera de las raíces permitidas
    with pytest.raises(ValueError):
        get_file_scope(
            Path("/some/other/path/file.py"),
            angular_root,
            nest_root,
            python_root,
        )

