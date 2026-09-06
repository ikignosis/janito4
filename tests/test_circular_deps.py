"""Circular-dependency guard for the ``janito`` package (issue #110).

The detector lives in this file (no subprocess, no ``scripts/`` wrapper):
every ``*.py`` file under ``janito/`` is parsed with :mod:`ast` (nothing is
imported or executed) and both top-level and function-level (lazy) imports
are collected into an intra-package import graph.  Strongly connected
components (Tarjan) with more than one module -- or a self-loop -- are
reported as circular groups.

Two tests:

- ``test_detector_finds_known_cycle`` pins the detector itself against a
  synthetic two-module cycle (one edge lazy) in ``tmp_path``;
- ``test_no_circular_dependencies_in_package`` enforces the invariant on
  the real tree: any new cycle fails the suite.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EdgeInfo:
    src: str
    dst: str
    filepath: str
    lineno: int
    lazy: bool


@dataclass
class ModuleInfo:
    name: str
    path: Path
    is_package: bool


def _discover_modules(root: Path) -> dict[str, ModuleInfo]:
    """Map every ``*.py`` file under *root* to its dotted module name.

    The directory basename is treated as the top-level package name
    (e.g. ``janito`` for ``janito/``), so ``janito/foo/bar.py`` becomes
    ``janito.foo.bar``.
    """
    top = root.name
    modules: dict[str, ModuleInfo] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
            is_package = True
        else:
            is_package = False
        dotted = ".".join([top, *parts]) if parts else top
        modules[dotted] = ModuleInfo(name=dotted, path=path, is_package=is_package)
    return modules


class _ImportVisitor(ast.NodeVisitor):
    """Collect import edges from a single module's AST."""

    def __init__(self, module: ModuleInfo) -> None:
        self.module = module
        self.edges: list[tuple[str, int, bool]] = []  # (abs_module, lineno, lazy)
        self._depth = 0  # >0 while inside a function/class body
        self._type_checking = 0  # >0 while inside `if TYPE_CHECKING:`

    def _is_lazy(self) -> bool:
        return self._depth > 0

    def _record(self, abs_module: str, lineno: int) -> None:
        if abs_module and abs_module != self.module.name:
            self.edges.append((abs_module, lineno, self._is_lazy()))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Class-body imports still execute at import time, but methods
        # inside them are lazy; track depth so methods are marked lazy.
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            self._type_checking += 1
            for stmt in node.body:
                self.visit(stmt)
            self._type_checking -= 1
            for stmt in node.orelse:
                self.visit(stmt)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._type_checking:
            return
        for alias in node.names:
            self._record(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._type_checking:
            return
        abs_base = _resolve_importfrom(self.module.name, self.module.is_package, node.level or 0, node.module)
        if abs_base is None:
            return
        if node.module is None:
            # `from . import foo` -> dependency on `<pkg>.foo`
            for alias in node.names:
                if alias.name == "*":
                    self._record(abs_base, node.lineno)
                else:
                    self._record(f"{abs_base}.{alias.name}", node.lineno)
        else:
            self._record(abs_base, node.lineno)
            # `from pkg import submodule` may target a submodule rather than
            # a symbol: record each imported name too.  Unknown names resolve
            # back up to `abs_base` in `_best_match`, so this only adds edges
            # when the name is a real in-scope module.
            for alias in node.names:
                if alias.name != "*":
                    self._record(f"{abs_base}.{alias.name}", node.lineno)

    def visit_Call(self, node: ast.Call) -> None:
        if not self._type_checking:
            modname = _static_import_call(node)
            if modname is not None:
                self._record(modname, node.lineno)
        self.generic_visit(node)


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _resolve_importfrom(current: str, is_package: bool, level: int, module: str | None) -> str | None:
    """Resolve a ``from ... import ...`` to an absolute module name."""
    if level == 0:
        return module
    if is_package:
        package = current
    else:
        package = current.rpartition(".")[0]
    if not package:
        return None
    pkg_parts = package.split(".")
    # level=1 -> current package, level=2 -> parent, ...
    up = level - 1
    if up > len(pkg_parts):
        return None
    base_parts = pkg_parts[: len(pkg_parts) - up]
    if module:
        base_parts = [*base_parts, *module.split(".")]
    if not base_parts:
        return None
    return ".".join(base_parts)


def _static_import_call(node: ast.Call) -> str | None:
    """Return the module name for importlib.import_module('x') / __import__('x')."""
    func = node.func
    is_import_call = False
    if isinstance(func, ast.Attribute) and func.attr == "import_module":
        if isinstance(func.value, ast.Name) and func.value.id == "importlib":
            is_import_call = True
    elif isinstance(func, ast.Name) and func.id == "__import__":
        is_import_call = True
    if not is_import_call or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.strip()
    return None


def _best_match(target: str, modules: dict[str, ModuleInfo]) -> str | None:
    """Map an imported dotted name to the closest known in-scope module.

    ``from pkg.mod import symbol`` names the provider module ``pkg.mod``;
    if ``target`` itself is unknown, walk up to the nearest known parent.
    """
    candidate = target
    while candidate:
        if candidate in modules:
            return candidate
        if "." not in candidate:
            return None
        candidate = candidate.rpartition(".")[0]
    return None


def _build_graph(
    modules: dict[str, ModuleInfo],
) -> tuple[dict[str, set[str]], list[EdgeInfo]]:
    graph: dict[str, set[str]] = {name: set() for name in modules}
    details: list[EdgeInfo] = []
    for name, mod in modules.items():
        try:
            tree = ast.parse(mod.path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        visitor = _ImportVisitor(mod)
        visitor.visit(tree)
        for target, lineno, lazy in visitor.edges:
            # Only intra-package edges matter for cycles.
            top = next(iter(modules)).split(".")[0] if modules else ""
            if not target == top and not target.startswith(top + "."):
                continue
            dst = _best_match(target, modules)
            if dst is None or dst == name:
                continue
            graph[name].add(dst)
            details.append(
                EdgeInfo(
                    src=name,
                    dst=dst,
                    filepath=str(mod.path),
                    lineno=lineno,
                    lazy=lazy,
                )
            )
    return graph, details


def _pop_component(stack: list[str], on_stack: set[str], root: str) -> list[str]:
    """Pop one strongly connected component off the Tarjan stack."""
    component = []
    while True:
        member = stack.pop()
        on_stack.discard(member)
        component.append(member)
        if member == root:
            return component


def _strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's algorithm; returns SCCs with a cycle (size>1 or self-loop)."""
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = [0]

    def strongconnect(v: str) -> None:
        index_of[v] = lowlink[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(graph.get(v, ())):
            if w not in index_of:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index_of[w])
        if lowlink[v] == index_of[v]:
            scc = _pop_component(stack, on_stack, v)
            if len(scc) > 1 or (len(scc) == 1 and v in graph.get(v, ())):
                result.append(sorted(scc))

    for vertex in sorted(graph):
        if vertex not in index_of:
            strongconnect(vertex)
    return sorted(result)


@dataclass
class CircularGroup:
    """One strongly connected group of modules plus its import edges."""

    modules: list[str]
    edges: list[EdgeInfo] = field(default_factory=list)


def find_circular_dependencies(root: Path) -> list[CircularGroup]:
    """Scan ``*.py`` files under *root* and return each circular group.

    Args:
        root: The package directory to scan (its basename is treated as
            the top-level package name).

    Returns:
        One :class:`CircularGroup` per strongly connected component with a
        cycle; empty when the tree is acyclic.
    """
    modules = _discover_modules(root)
    if not modules:
        return []
    graph, details = _build_graph(modules)
    edge_lookup: dict[tuple[str, str], list[EdgeInfo]] = {}
    for info in details:
        edge_lookup.setdefault((info.src, info.dst), []).append(info)
    groups = []
    for scc in _strongly_connected(graph):
        member_edges = [
            info for (src, dst), infos in edge_lookup.items() for info in infos if src in scc and dst in scc
        ]
        groups.append(CircularGroup(modules=scc, edges=member_edges))
    return groups


def _package_dir() -> Path:
    return Path(__file__).parent.parent / "janito"


def test_detector_finds_known_cycle(tmp_path):
    """The detector flags a two-module cycle with one lazy edge."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "alpha.py").write_text("from mypkg import beta\n")
    (pkg / "beta.py").write_text("def f():\n    from mypkg import alpha\n")

    groups = find_circular_dependencies(pkg)

    assert len(groups) == 1
    assert groups[0].modules == ["mypkg.alpha", "mypkg.beta"]
    kinds = {(e.src, e.dst, e.lazy) for e in groups[0].edges}
    assert ("mypkg.alpha", "mypkg.beta", False) in kinds
    assert ("mypkg.beta", "mypkg.alpha", True) in kinds


def test_detector_ignores_type_checking_imports(tmp_path):
    """``if TYPE_CHECKING:`` imports never form runtime cycles."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "alpha.py").write_text(
        "from typing import TYPE_CHECKING\n" "if TYPE_CHECKING:\n" "    from mypkg import beta\n"
    )
    (pkg / "beta.py").write_text("from mypkg import alpha\n")

    assert find_circular_dependencies(pkg) == []


def test_no_circular_dependencies_in_package():
    """The ``janito`` tree itself must stay acyclic (issue #110)."""
    groups = find_circular_dependencies(_package_dir())

    rendered = []
    for group in groups:
        rendered.append("cycle: " + " -> ".join(group.modules))
        for edge in sorted(group.edges, key=lambda e: (e.src, e.dst)):
            tag = "lazy" if edge.lazy else "top-level"
            rendered.append(f"  {edge.src} -> {edge.dst} [{tag}] {edge.filepath}:{edge.lineno}")
    assert not groups, "circular dependencies found:\n" + "\n".join(rendered)
