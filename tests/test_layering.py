"""Enforce the Clean Architecture layer table from CLAUDE.md by walking
imports, not by trusting directory placement alone.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

LAYER_OF = {
    "kernel": "kernel",
    "domain": "domain",
    "infrastructure": "infra",
    "application": "application",
    "interfaces": "interface",
}

# What each layer may import, by layer name. A layer may always import
# itself.
ALLOWED = {
    "kernel": set(),
    "domain": {"kernel"},
    "infra": {"kernel", "domain"},
    "application": {"kernel", "domain", "infra"},
    "interface": {"kernel", "domain", "infra", "application"},
}


def _layer_of_module(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "src":
        return None
    return LAYER_OF.get(parts[1])


def _iter_python_files():
    if not SRC.exists():
        return
    yield from SRC.rglob("*.py")


def test_no_python_files_yet_or_all_respect_layering():
    """No src/ files exist yet at kickoff; once they do, each one's
    imports must stay within what its layer is allowed to import.
    """
    violations = []
    for path in _iter_python_files():
        rel = path.relative_to(SRC.parent)
        module_parts = rel.with_suffix("").parts
        this_layer = _layer_of_module(".".join(module_parts))
        if this_layer is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_layer = _layer_of_module(node.module)
            elif isinstance(node, ast.Import):
                imported_layer = None
                for alias in node.names:
                    imported_layer = _layer_of_module(alias.name)
                    if imported_layer:
                        break
            else:
                continue
            if imported_layer is None or imported_layer == this_layer:
                continue
            if imported_layer not in ALLOWED[this_layer]:
                violations.append(
                    f"{rel}: layer '{this_layer}' may not import "
                    f"layer '{imported_layer}'"
                )
    assert not violations, "\n".join(violations)


def test_application_never_imports_agent_a2a_servers_directly():
    """The single most load-bearing rule in CLAUDE.md: a Supervisor that
    imports an A2A-hosted agent's module directly can silently degrade a
    network delegation into a local function call while every other test
    still passes.
    """
    app_dir = SRC / "application"
    if not app_dir.exists():
        return
    forbidden = {"docs_a2a_server", "web_search_a2a_server"}
    violations = []
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                if any(f in name for f in forbidden):
                    violations.append(f"{path}: imports {name}")
    assert not violations, "\n".join(violations)


def test_supervisor_never_imports_agent_orchestration_modules_directly():
    """A narrower instance of the same rule as above: `docs_agent.py` and
    `web_search_agent.py` (docs/decisions.md #20) are `application`-layer
    modules by file location, but they are only ever meant to run inside
    their own A2A server process (imported by `interfaces/*_a2a_server.py`
    alone). `supervisor.py` importing either directly would be the exact
    "network hop silently degrades to a local call" failure the layer
    table exists to catch — a plain cross-layer import check can't see
    this because both files sit in the same `application` layer.
    """
    supervisor_path = SRC / "application" / "supervisor.py"
    if not supervisor_path.exists():
        return
    forbidden = {"docs_agent", "web_search_agent"}
    tree = ast.parse(supervisor_path.read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            if any(f == name.rsplit(".", 1)[-1] for f in forbidden):
                violations.append(f"{supervisor_path}: imports {name}")
    assert not violations, "\n".join(violations)
