"""Guard the hexagonal boundaries.

The central rule of this repository is that dependencies point inwards:

    adapters -> application -> domain

A violation is easy to introduce and hard to notice by reading, so it is
asserted here instead of trusted. Imports are read from the syntax tree rather
than matched as text, so a package named in a comment or a docstring does not
count.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[2] / "pango_prediction"

WEB_PACKAGES = frozenset({"fastapi", "pydantic", "starlette", "uvicorn"})

INNER_LAYERS = ("domain", "application", "ports")


def imported_modules(path: Path) -> set[str]:
    """Every module name ``path`` imports, as written."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)

    return modules


def python_files(*layers: str) -> list[Path]:
    return sorted(
        path for layer in layers for path in (PACKAGE / layer).rglob("*.py")
    )


def test_the_layers_being_guarded_actually_exist() -> None:
    """Keep this file from passing vacuously if a directory is renamed."""
    assert python_files(*INNER_LAYERS)
    assert python_files("adapters")


def test_domain_application_and_ports_do_not_import_a_web_framework() -> None:
    """The domain has to stay runnable from a notebook or a bare test."""
    offenders: dict[str, set[str]] = {}

    for path in python_files(*INNER_LAYERS):
        web = {
            module
            for module in imported_modules(path)
            if module.split(".")[0] in WEB_PACKAGES
        }

        if web:
            offenders[str(path.relative_to(PACKAGE))] = web

    assert not offenders, f"web framework reached an inner layer: {offenders}"


def test_the_domain_does_not_import_the_outer_layers() -> None:
    """The domain is the innermost layer: it depends on nothing of ours."""
    offenders: dict[str, set[str]] = {}

    for path in python_files("domain"):
        outward = {
            module
            for module in imported_modules(path)
            if module.startswith(
                ("pango_prediction.application", "pango_prediction.adapters")
            )
            or module.startswith("pango_prediction.ports")
        }

        if outward:
            offenders[str(path.relative_to(PACKAGE))] = outward

    assert not offenders, f"the domain reached outwards: {offenders}"


def test_the_application_layer_does_not_import_an_adapter() -> None:
    """The application talks to ports, never to a concrete adapter."""
    offenders: dict[str, set[str]] = {}

    for path in python_files("application"):
        outward = {
            module
            for module in imported_modules(path)
            if module.startswith("pango_prediction.adapters")
        }

        if outward:
            offenders[str(path.relative_to(PACKAGE))] = outward

    assert not offenders, f"the application reached an adapter: {offenders}"
