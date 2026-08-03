"""The api/ subpackage must stay free of Home Assistant.

The client is bundled rather than published to PyPI. Home Assistant Core
requires third-party API clients to be a separate package, so core submission
means extracting `api/` later -- and that is only a move rather than a rewrite
while nothing in it reaches for Home Assistant.

If this fails, move the HA-facing logic out of `api/`. Do not add an
exemption.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

API_DIR = Path(__file__).parent.parent / "custom_components" / "njtransit" / "api"

MODULES = sorted(API_DIR.glob("*.py"))


def imported_modules(source: str) -> set[str]:
    """Return every module name imported by a source file."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def test_api_modules_were_found() -> None:
    """Guard against the glob silently matching nothing."""
    assert MODULES, f"no modules found under {API_DIR}"


@pytest.mark.parametrize("module", MODULES, ids=lambda path: path.name)
def test_api_does_not_import_homeassistant(module: Path) -> None:
    """No module under api/ may import Home Assistant."""
    offenders = {
        name
        for name in imported_modules(module.read_text(encoding="utf-8"))
        if name == "homeassistant" or name.startswith("homeassistant.")
    }
    assert not offenders, f"{module.name} imports {sorted(offenders)}"
