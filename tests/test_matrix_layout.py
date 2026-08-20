from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "integrated_agent" / "runtimes" / "matrix"
OWNED_PACKAGES = ("compose", "host", "kb_chat", "rag", "reply")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _package_paths(package: str) -> list[Path]:
    return sorted((ROOT / package).rglob("*.py"))


def _leaks(imported: set[str], forbidden: tuple[str, ...]) -> list[str]:
    return [
        name
        for name in imported
        if any(name == item or name.startswith(f"{item}.") for item in forbidden)
    ]


def test_matrix_root_only_keeps_package_init() -> None:
    files = sorted(path.name for path in ROOT.glob("*.py"))
    assert files == ["__init__.py"]
    dirs = sorted(
        path.name
        for path in ROOT.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    )
    assert dirs == sorted(OWNED_PACKAGES)


def test_compose_package_does_not_import_reply_or_kb_chat() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.reply",
        "integrated_agent.runtimes.matrix.kb_chat",
    )
    for path in _package_paths("compose"):
        leaked = _leaks(_imported_modules(path), forbidden)
        assert leaked == [], f"compose/{path.relative_to(ROOT / 'compose')} imports {leaked}"


def test_reply_package_does_not_import_compose_or_kb_chat() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.compose",
        "integrated_agent.runtimes.matrix.kb_chat",
    )
    for path in _package_paths("reply"):
        leaked = _leaks(_imported_modules(path), forbidden)
        assert leaked == [], f"reply/{path.relative_to(ROOT / 'reply')} imports {leaked}"


def test_host_package_does_not_import_product_flows() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.compose",
        "integrated_agent.runtimes.matrix.reply",
        "integrated_agent.runtimes.matrix.kb_chat",
    )
    for path in _package_paths("host"):
        leaked = _leaks(_imported_modules(path), forbidden)
        assert leaked == [], f"host/{path.relative_to(ROOT / 'host')} imports {leaked}"


def test_rag_package_does_not_import_product_or_host() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.compose",
        "integrated_agent.runtimes.matrix.reply",
        "integrated_agent.runtimes.matrix.kb_chat",
        "integrated_agent.runtimes.matrix.host",
    )
    for path in _package_paths("rag"):
        leaked = _leaks(_imported_modules(path), forbidden)
        assert leaked == [], f"rag/{path.relative_to(ROOT / 'rag')} imports {leaked}"


def test_kb_chat_still_isolated_from_compose_reply_host() -> None:
    forbidden = (
        "integrated_agent.runtimes.matrix.compose",
        "integrated_agent.runtimes.matrix.reply",
        "integrated_agent.runtimes.matrix.host",
    )
    for path in _package_paths("kb_chat"):
        leaked = _leaks(_imported_modules(path), forbidden)
        assert leaked == [], f"kb_chat/{path.name} imports {leaked}"
