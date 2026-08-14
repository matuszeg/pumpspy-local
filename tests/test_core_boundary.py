"""The ``core`` package must stay free of Home Assistant.

This is what keeps the integration shape reversible: if the proxy-inside-HA
approach ever has to be abandoned, ``core`` lifts out into a standalone service
as a packaging change rather than a rewrite. That property decays silently
unless something checks it, so this test checks it.
"""

import ast
from pathlib import Path

CORE = (
    Path(__file__).parent.parent
    / "custom_components"
    / "pumpspy_local"
    / "core"
)


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_modules_exist_to_be_checked():
    # Guards against this suite passing vacuously if core/ is moved or renamed.
    assert list(CORE.rglob("*.py")), f"no modules found under {CORE}"


def test_core_does_not_import_homeassistant():
    offenders = [
        path.relative_to(CORE).as_posix()
        for path in sorted(CORE.rglob("*.py"))
        if "homeassistant" in _imported_roots(path)
    ]

    assert offenders == [], (
        f"core must not import homeassistant, but these do: {offenders}"
    )
