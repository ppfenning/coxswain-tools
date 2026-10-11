"""Every subprocess call in a tick-path module carries a timeout, or goes through a helper named bounded here."""

import ast
from pathlib import Path

import pytest

# One line per tick-path module, naming the helpers that bound its subprocess work. Each must exist and be bounded.
TICK_PATH_MODULES: dict[str, frozenset[str]] = {
    "agent_tools/store_cli.py": frozenset({"_run"}),
    "agent_tools/forge_github.py": frozenset({"_run", "_gh_run", "_gh_text"}),
    "agent_tools/chair_revert_ports.py": frozenset({"bounded"}),
    "agent_tools/chair_exec.py": frozenset({"run_argv"}),
}

SUBPROCESS_CALLS = frozenset({"run", "check_output", "check_call", "call", "Popen"})
REPO_ROOT = Path(__file__).resolve().parents[1]
Offender = tuple[str, int, str]


def _aliases(tree: ast.AST) -> tuple[frozenset[str], frozenset[str]]:
    """Pure. Local names bound to the subprocess module, and local names bound to one of its calls."""
    imports = [a for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    froms = [a for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "subprocess" for a in n.names]
    modules = frozenset(a.asname or a.name for a in imports if a.name == "subprocess")
    return modules, frozenset(a.asname or a.name for a in froms if a.name in SUBPROCESS_CALLS)


def _is_target(node: ast.AST, modules: frozenset[str], calls: frozenset[str]) -> bool:
    """Pure. True for a direct reference to a subprocess call: `subprocess.run`, an imported `run`."""
    if isinstance(node, ast.Attribute):
        return isinstance(node.value, ast.Name) and node.value.id in modules and node.attr in SUBPROCESS_CALLS
    return isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in calls


def _callee_targets(func: ast.AST, modules: frozenset[str], calls: frozenset[str]) -> list[ast.AST]:
    """Pure. The subprocess references a callee can evaluate to, through `a or b` and `a if c else b`."""
    if isinstance(func, ast.BoolOp):
        return [t for v in func.values for t in _callee_targets(v, modules, calls)]
    if isinstance(func, ast.IfExp):
        return _callee_targets(func.body, modules, calls) + _callee_targets(func.orelse, modules, calls)
    return [func] if _is_target(func, modules, calls) else []


def _has_timeout(call: ast.Call) -> bool:
    """Pure. A `timeout=` keyword that is not None. A `**kwargs` spread proves nothing and does not count."""
    return any(
        k.arg == "timeout" and not (isinstance(k.value, ast.Constant) and k.value.value is None) for k in call.keywords
    )


def _defaults(fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> list[tuple[str, ast.expr]]:
    """Pure. (parameter, default) pairs for every parameter that has one."""
    positional = [*fn.args.posonlyargs, *fn.args.args]
    tail = positional[len(positional) - len(fn.args.defaults) :]
    keyword = [(a.arg, d) for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults) if d is not None]
    return [(a.arg, d) for a, d in zip(tail, fn.args.defaults)] + keyword


def _handed_to_helper(fn: ast.AST, param: str, helpers: frozenset[str]) -> bool:
    """Pure. True when every read of `param` inside `fn` is an argument to a call of a bounded helper."""
    reads = {id(n) for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == param and isinstance(n.ctx, ast.Load)}
    handed = {
        id(a)
        for c in ast.walk(fn)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in helpers
        for a in [*c.args, *(k.value for k in c.keywords)]
    }
    return reads <= handed


def find_unbounded_calls(source: str, filename: str, helpers: frozenset[str] = frozenset()) -> list[Offender]:
    """Pure. Subprocess calls without a timeout, and subprocess references that escape uncalled."""
    tree = ast.parse(source, filename=filename)
    modules, calls = _aliases(tree)
    called = [
        (c, t) for c in ast.walk(tree) if isinstance(c, ast.Call) for t in _callee_targets(c.func, modules, calls)
    ]
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)]
    passed = {id(d) for f in fns for p, d in _defaults(f) if _handed_to_helper(f, p, helpers)}
    seen = {id(t) for _, t in called} | passed
    bases = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    unbounded = [(filename, c.lineno, f"{ast.unparse(t)} has no timeout") for c, t in called if not _has_timeout(c)]
    escapes = [
        (filename, n.lineno, f"{ast.unparse(n)} escapes uncalled")
        for n in ast.walk(tree)
        if (_is_target(n, modules, calls) and id(n) not in seen)
        or (isinstance(n, ast.Name) and n.id in modules and isinstance(n.ctx, ast.Load) and id(n) not in bases)
    ]
    return sorted(unbounded + escapes)


def find_unbounded_helpers(source: str, filename: str, helpers: frozenset[str]) -> list[Offender]:
    """Pure. A named helper that is missing, passes no timeout, defaults `timeout` to None, or is called with None."""
    tree = ast.parse(source, filename=filename)
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    missing = [(filename, 0, f"{h} is not defined") for h in sorted(helpers - defs.keys())]
    bodies = [(h, defs[h]) for h in sorted(helpers & defs.keys())]
    no_bound = [
        (filename, f.lineno, f"{h} passes no timeout")
        for h, f in bodies
        if not any(isinstance(c, ast.Call) and _has_timeout(c) for c in ast.walk(f))
    ]
    none_default = [
        (filename, f.lineno, f"{h} defaults timeout to None")
        for h, f in bodies
        for p, d in _defaults(f)
        if p == "timeout" and isinstance(d, ast.Constant) and d.value is None
    ]
    none_call = [
        (filename, c.lineno, f"{c.func.id} called with timeout=None")
        for c in ast.walk(tree)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in helpers
        for k in c.keywords
        if k.arg == "timeout" and isinstance(k.value, ast.Constant) and k.value.value is None
    ]
    return missing + no_bound + none_default + none_call


@pytest.mark.parametrize("module", TICK_PATH_MODULES)
def test_tick_path_calls_have_timeouts(module: str) -> None:
    source, helpers = (REPO_ROOT / module).read_text(), TICK_PATH_MODULES[module]
    offenders = find_unbounded_calls(source, module, helpers) + find_unbounded_helpers(source, module, helpers)
    report = "\n".join(f"{path}:{line}: {why}" for path, line, why in offenders)
    assert helpers, f"{module} names no bounded helper, so its scan proves nothing"
    assert not offenders, f"unbounded subprocess work on the tick path:\n{report}"


def test_scanner_reports_unbounded_call() -> None:
    source = 'import subprocess\nsubprocess.run(["ls"])\nsubprocess.run(["ls"], timeout=3)\n'
    assert find_unbounded_calls(source, "x.py") == [("x.py", 2, "subprocess.run has no timeout")]


def test_scanner_sees_aliases_wrapped_callees_and_unproven_bounds() -> None:
    source = (
        "import subprocess as sp\n"
        "from subprocess import check_output, Popen as P\n"
        'check_output(["a"], timeout=1)\n'
        'P(["b"])\n'
        '(run or sp.run)(["c"])\n'
        '(sp.call if x else run)(["d"], timeout=None)\n'
        'sp.run(["e"], **kw)\n'
    )
    assert [line for _, line, _ in find_unbounded_calls(source, "x.py")] == [4, 5, 6, 7]


def test_scanner_reports_references_that_escape_uncalled() -> None:
    source = (
        "import subprocess, functools\n"
        "p = subprocess.run\n"
        "q = functools.partial(subprocess.Popen)\n"
        'r = getattr(subprocess, "call")\n'
        "def f(run=subprocess.run):\n    return run([])\n"
        "def g(run=subprocess.run):\n    return _run(run)\n"
    )
    assert [line for _, line, _ in find_unbounded_calls(source, "x.py", frozenset({"_run"}))] == [2, 3, 4, 5]


def test_helper_check_reports_missing_unbounded_and_none() -> None:
    source = "def a(argv, timeout=None):\n    return go(argv, timeout=timeout)\ndef b(argv):\n    return go(argv)\nb([], timeout=None)\n"
    assert find_unbounded_helpers(source, "x.py", frozenset({"a", "b", "c"})) == [
        ("x.py", 0, "c is not defined"),
        ("x.py", 3, "b passes no timeout"),
        ("x.py", 1, "a defaults timeout to None"),
        ("x.py", 5, "b called with timeout=None"),
    ]
