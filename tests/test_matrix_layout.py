from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "integrated_agent" / "runtimes" / "matrix"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _package_imports(package: str) -> dict[str, set[str]]:
    root = ROOT / package
    return {
        path.name: _imported_modules(path)
        for path in sorted(root.glob("*.py"))
    }


def _leaks(imported: set[str], forbidden: tuple[str, ...]) -> list[str]:
    return [
        name
        for name in imported
        if any(name == item or name.startswith(f"{item}.") for item in forbidden)
    ]


def test_compose_package_does_not_import_reply_or_kb_chat() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.reply",
        "integrated_agent.runtimes.matrix.kb_chat",
    )
    for filename, imported in _package_imports("compose").items():
        leaked = _leaks(imported, forbidden)
        assert leaked == [], f"compose/{filename} imports {leaked}"


def test_reply_package_does_not_import_compose_or_kb_chat() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.compose",
        "integrated_agent.runtimes.matrix.kb_chat",
    )
    for filename, imported in _package_imports("reply").items():
        leaked = _leaks(imported, forbidden)
        assert leaked == [], f"reply/{filename} imports {leaked}"


def test_host_package_does_not_import_product_flows() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.compose",
        "integrated_agent.runtimes.matrix.reply",
        "integrated_agent.runtimes.matrix.kb_chat",
    )
    for path in sorted((ROOT / "host").glob("*.py")):
        leaked = _leaks(_imported_modules(path), forbidden)
        assert leaked == [], f"host/{path.name} imports {leaked}"


def test_kb_chat_still_isolated_from_compose_reply() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.compose",
        "integrated_agent.runtimes.matrix.reply",
        "integrated_agent.runtimes.matrix.host",
        "integrated_agent.runtimes.matrix.models",
    )
    for filename, imported in _package_imports("kb_chat").items():
        leaked = _leaks(imported, forbidden)
        assert leaked == [], f"kb_chat/{filename} imports {leaked}"
