"""Build source-preserving query-isolation replacements for feature/clean_ni.

Run from the Ikaros repository root:
    python tools/build_db_query_refactor.py --out db_refactor_copy_in.zip

The script NEVER changes your working tree. It reads the working tree, moves
SQL-bearing functions / cursor blocks into database/isolated, and emits a ZIP
of replacement files. Review the generated manifest and run tests before use.
This is a conservative syntactic refactor, not an SQL dialect translator.
"""

from __future__ import annotations

import argparse
import ast
import copy
import dataclasses
import json
import re
import textwrap
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

# Audited from feature/clean_ni at 3b6d1dce6fc473318fc67f37fdf5ada0e2699006.
# Deliberately excludes database/, tests/, generated code and feed-only modules.
TARGETS: tuple[str, ...] = (
    "ikaros_app/update.py",
    "ikaros_app/derivative_approval.py",
    "ikaros_app/derivative_prices.py",
    "ikaros_app/derivative_specs.py",
    "ikaros_app/yield_curve_plot.py",
    "ingestion/iic.py",
    "ingestion/fw20_registry.py",
    "run/backfill_equities.py",
    "run/backfill_fx_nbp.py",
    "run/backfill_fx_snowflake.py",
    "run/backfill_rates.py",
    "run/collect_us_option_chains.py",
    "run/derive_fx_snowflake.py",
    "run/gpw_freshness.py",
    "run/ingest_gpw_derivatives.py",
    "run/ingest_gpw_derivatives_bulk.py",
    "run/ingest_index_prices.py",
    "run/ingest_index_weights.py",
    "run/kafka_contract_sink.py",
    "run/refresh_fw20_prices.py",
    "run/repair_equity_prices.py",
    "run/seed_equities.py",
    "run/seed_fx_reference.py",
    "run/seed_snowflake_fx.py",
)
DB_METHODS = {
    "cursor",
    "execute",
    "executemany",
    "fetchall",
    "fetchone",
    "_query",
    "fetch_pandas_all",
}
SQL_TEXT = re.compile(
    r"\b(?:SELECT\s+\S+\s+FROM|INSERT\s+INTO|MERGE\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE|ALTER\s+TABLE|TRUNCATE\s+TABLE|DROP\s+TABLE)\b",
    re.IGNORECASE | re.DOTALL,
)
NAME_HINT = re.compile(
    r"(?:^DDL$|SQL$|_SQL$|QUERY$|_QUERY$|^STAGE$|_STAGE$|^MERGE$|^INSERT$|^SELECT$|^KEY_WHERE$)"
)
EXCLUDE_ENTRYPOINTS = {"main", "run_schedule", "consume", "consumer_loop", "run"}


@dataclasses.dataclass
class Issue:
    file: str
    line: int
    problem: str


def is_database_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in DB_METHODS
    )


def is_sql(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and bool(SQL_TEXT.search(node.value))
    )


def contains_database_ops(node: ast.AST) -> bool:
    return any(is_database_call(n) or is_sql(n) for n in ast.walk(node))


def assigned_names(node: ast.AST) -> set[str]:
    return {
        n.id
        for n in ast.walk(node)
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))
    }


def loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = node.args
    return {
        x.arg
        for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]
        + ([a.vararg] if a.vararg else [])
        + ([a.kwarg] if a.kwarg else [])
    }


def proxy_body(node: ast.FunctionDef | ast.AsyncFunctionDef, dbpath: str, alias: str) -> str:
    """Keep exactly the public signature/defaults/decorators in the original module."""
    stub = copy.deepcopy(node)
    args = []
    args.extend(
        ast.Name(id=a.arg, ctx=ast.Load()) for a in [*node.args.posonlyargs, *node.args.args]
    )
    if node.args.vararg is not None:
        args.append(
            ast.Starred(value=ast.Name(id=node.args.vararg.arg, ctx=ast.Load()), ctx=ast.Load())
        )
    keywords = [
        ast.keyword(arg=k.arg, value=ast.Name(id=k.arg, ctx=ast.Load()))
        for k in node.args.kwonlyargs
    ]
    if node.args.kwarg is not None:
        keywords.append(
            ast.keyword(arg=None, value=ast.Name(id=node.args.kwarg.arg, ctx=ast.Load()))
        )
    importstmt = ast.ImportFrom(
        module=dbpath, names=[ast.alias(name=alias, asname="_isolated_impl")], level=0
    )
    call = ast.Call(
        func=ast.Name(id="_isolated_impl", ctx=ast.Load()), args=args, keywords=keywords
    )
    expr: ast.expr = ast.Await(value=call) if isinstance(node, ast.AsyncFunctionDef) else call
    stub.body = [importstmt, ast.Return(value=expr)]
    stub.decorator_list = []
    ast.fix_missing_locations(stub)
    return ast.unparse(stub)


def body_impl(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> str:
    implementation = copy.deepcopy(node)
    implementation.name = f"_body_{name}"
    implementation.decorator_list = []
    implementation.returns = None
    # Defaults are always applied by the original wrapper before delegation.
    implementation.args.defaults = []
    implementation.args.kw_defaults = [None] * len(implementation.args.kwonlyargs)
    for arg in [
        *implementation.args.posonlyargs,
        *implementation.args.args,
        *implementation.args.kwonlyargs,
    ]:
        arg.annotation = None
    if implementation.args.vararg:
        implementation.args.vararg.annotation = None
    if implementation.args.kwarg:
        implementation.args.kwarg.annotation = None
    ast.fix_missing_locations(implementation)
    prefix = "async " if isinstance(implementation, ast.AsyncFunctionDef) else ""
    awaiter = "await " if prefix else ""
    return (
        ast.unparse(implementation)
        + f"\n\n{prefix}def {name}(*args: Any, **kwargs: Any) -> Any:\n"
        + f"    _sync_origin_globals()\n    return {awaiter}_body_{name}(*args, **kwargs)\n"
    )


def replace_span(text: str, node: ast.AST, new: str) -> str:
    """Replace only one full AST span; leave other functions/comments intact."""
    lines = text.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    indent = " " * node.col_offset
    chunk = "\n".join(indent + line if line else "" for line in new.rstrip().splitlines()) + "\n"
    lines[start:end] = [chunk]
    return "".join(lines)


def db_constants(tree: ast.Module) -> list[ast.Assign | ast.AnnAssign]:
    result = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = assigned_names(node)
        if any(NAME_HINT.search(s) for s in names) or any(is_sql(c) for c in ast.walk(node)):
            result.append(node)
    return result


def has_unsupported_control(node: ast.AST) -> bool:
    return any(
        isinstance(
            n,
            (
                ast.Return,
                ast.Break,
                ast.Continue,
                ast.Yield,
                ast.YieldFrom,
                ast.Await,
                ast.Nonlocal,
                ast.Global,
            ),
        )
        for n in ast.walk(node)
    )


def extract_inline_cursor_blocks(
    f: ast.FunctionDef | ast.AsyncFunctionDef,
    dbpath: str,
    impls: list[str],
    patches: list[tuple[ast.AST, str]],
    issues: list[Issue],
    rel: str,
) -> None:
    """Keep an entrypoint in run/: extract smallest cursor-with blocks only."""
    # Find cursor-with blocks whose body is DB work and not nested in another.
    cursor_blocks = [
        n
        for n in ast.walk(f)
        if isinstance(n, (ast.With, ast.AsyncWith))
        and any(is_database_call(x) for item in n.items for x in ast.walk(item.context_expr))
    ]
    for block in cursor_blocks:
        if has_unsupported_control(block):
            issues.append(
                Issue(
                    rel,
                    block.lineno,
                    "cursor block contains control flow; manual extraction required",
                )
            )
            continue
        origin_locals = parameters(f)
        for n in ast.walk(f):
            if (
                getattr(n, "lineno", 10**9) < block.lineno
                and isinstance(n, ast.Name)
                and isinstance(n.ctx, ast.Store)
            ):
                origin_locals.add(n.id)
        inner_assigned = assigned_names(block)
        needed = sorted((loaded_names(block) - inner_assigned) & origin_locals)
        # Scope construction: body may assign vars then reference them after block.
        after = {
            n.id
            for n in ast.walk(f)
            if isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Load)
            and getattr(n, "lineno", 0) > block.end_lineno
        }
        returned = sorted(inner_assigned & after)
        # Treat alias of cursor context as a local to the moved block.
        cursor_vars = set()
        for item in block.items:
            if item.optional_vars:
                cursor_vars |= assigned_names(item.optional_vars)
        if set(returned) & cursor_vars:
            issues.append(Issue(rel, block.lineno, "cursor object escapes the query block"))
            continue
        name = f"_inline_{f.name}_{block.lineno}"
        parameters_text = ", ".join(needed)
        statement = textwrap.indent(ast.unparse(block), "    ")
        suffix = f"\n    return {', '.join(returned)}" if returned else ""
        impls.append(
            f"def {name}({parameters_text}):\n    _sync_origin_globals()\n{statement}{suffix}\n"
        )
        expr = f"{name}({', '.join(needed)})"
        lhs = f"{', '.join(returned)} = " if returned else ""
        patches.append((block, f"from {dbpath} import {name}\n{lhs}{expr}"))


def convert_module(rel: str, source: str) -> tuple[dict[str, str], list[Issue]]:
    tree = ast.parse(source, filename=rel)
    dbrel = "database/isolated/" + rel.replace("/", "_")
    dbmodule = dbrel[:-3].replace("/", ".")
    const_nodes = db_constants(tree)
    # If a constant assignment needs simple local symbol inputs, duplicate those
    # literal assignments in database; the original remains owner for consumers.
    patches: list[tuple[ast.AST, str]] = []
    implementations: list[str] = []
    issues: list[Issue] = []
    protected: list[str] = []
    for node in const_nodes:
        names = sorted(assigned_names(node))
        if not names:
            continue
        text = ast.unparse(node)
        implementations.append(text + "\n")
        for n in names:
            protected.append(n)
        patches.append((node, f"from {dbmodule} import {', '.join(names)}"))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not contains_database_ops(node):
                continue
            if node.name in EXCLUDE_ENTRYPOINTS:
                extract_inline_cursor_blocks(node, dbmodule, implementations, patches, issues, rel)
                continue
            if any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(node)):
                issues.append(Issue(rel, node.lineno, "global/nonlocal binding; manual move"))
                continue
            if node.decorator_list:
                issues.append(Issue(rel, node.lineno, "decorated SQL function; manual move"))
                continue
            name = node.name
            protected.append(name)
            implementations.append(body_impl(node, name))
            patches.append((node, proxy_body(node, dbmodule, name)))
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if not isinstance(
                    method, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) or not contains_database_ops(method):
                    continue
                if any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(method)):
                    issues.append(
                        Issue(rel, method.lineno, "global/nonlocal SQL method; manual move")
                    )
                    continue
                if method.decorator_list:
                    issues.append(Issue(rel, method.lineno, "decorated SQL method; manual move"))
                    continue
                name = f"{node.name}_{method.name}"
                protected.append(name)
                implementations.append(body_impl(method, name))
                patches.append((method, proxy_body(method, dbmodule, name)))

    if not patches:
        return {}, issues
    # Literal assignments used by SQL globals may be bound before the import.
    # They are required when SQL module-level f-strings mention e.g. TABLE.
    literal_globals = []
    for n in tree.body:
        if n in const_nodes or not isinstance(n, (ast.Assign, ast.AnnAssign)):
            continue
        try:
            ast.literal_eval(n.value)
        except (ValueError, TypeError, SyntaxError, AttributeError, MemoryError):
            continue
        literal_globals.append(ast.unparse(n))

    # Only carry origin imports actually referenced by module-level constants.
    # All other names are imported lazily by _sync_origin_globals on invocation.
    constant_inputs = set().union(*(loaded_names(x) for x in const_nodes)) if const_nodes else set()
    constant_imports: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        names = [
            item
            for item in node.names
            if (item.asname or item.name.split(".")[0]) in constant_inputs
        ]
        if names:
            copied = copy.deepcopy(node)
            copied.names = names
            constant_imports.append(ast.unparse(copied))
    orig = rel[:-3].replace("/", ".")
    reserved = repr(
        frozenset(
            ["_sync_origin_globals", "_origin_module", *protected]
            + [f"_body_{x}" for x in protected]
        )
    )
    header = (
        '"""SQL implementations isolated from '
        + orig
        + '. Generated from source; review before deploying."""\n'
        "from __future__ import annotations\n"
        "import importlib\n"
        + ("from typing import Any\n" if any("*args: Any" in s for s in implementations) else "")
        + "\n".join(constant_imports)
        + "\n\n"
        + "\n".join(literal_globals)
        + "\n\n"
        + f"_ORIGIN = {orig!r}\n_PROTECTED = {reserved}\n"
        + "def _sync_origin_globals() -> None:\n"
        + "    origin = importlib.import_module(_ORIGIN)\n"
        + "    for name, value in vars(origin).items():\n"
        + "        if not name.startswith('__') and name not in _PROTECTED:\n"
        + "            globals()[name] = value\n\n"
    )
    query_source = header + "\n\n".join(implementations) + "\n"
    modified = source
    for node, replacement in sorted(
        patches, key=lambda pair: (pair[0].lineno, pair[0].col_offset), reverse=True
    ):
        modified = replace_span(modified, node, replacement)
    # Ruff F401: do not leave imports referenced only by moved implementations.
    old_used = loaded_names(tree)
    new_tree = ast.parse(modified)
    new_used = loaded_names(new_tree)
    import_patches: list[tuple[ast.AST, str]] = []
    for imp in new_tree.body:
        if not isinstance(imp, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(imp, ast.ImportFrom) and imp.module == "__future__":
            continue
        retained = []
        for alias in imp.names:
            binding = alias.asname or alias.name.split(".")[0]
            if binding in old_used and binding not in new_used:
                continue
            retained.append(alias)
        if len(retained) == len(imp.names):
            continue
        if not retained:
            import_patches.append((imp, ""))
        else:
            new_imp = copy.deepcopy(imp)
            new_imp.names = retained
            import_patches.append((imp, ast.unparse(new_imp)))
    for node, replacement in sorted(import_patches, key=lambda pair: pair[0].lineno, reverse=True):
        modified = replace_span(modified, node, replacement)

    for path, contents in ((rel, modified), (dbrel, query_source)):
        try:
            compile(contents, path, "exec")
        except SyntaxError as exc:
            issues.append(Issue(rel, exc.lineno or 0, f"generated {path} invalid: {exc.msg}"))
    # Warn on remaining direct DB work; no false 'all clean' conclusion.
    result_ast = ast.parse(modified)
    for n in ast.walk(result_ast):
        if is_database_call(n):
            issues.append(Issue(rel, n.lineno, "unmoved cursor/execute call"))
        elif is_sql(n) and not (
            isinstance(n, ast.Constant) and n.value == ast.get_docstring(result_ast)
        ):
            issues.append(Issue(rel, n.lineno, "SQL literal remains outside database/"))
    return {rel: modified, dbrel: query_source}, issues


def build_package(root: Path, out: Path) -> tuple[int, list[Issue]]:
    files = {}
    issues: list[Issue] = []
    for path in TARGETS:
        original = root / path
        if not original.is_file():
            issues.append(Issue(path, 0, "file missing from supplied checkout"))
            continue
        replacements, warnings = convert_module(path, original.read_text(encoding="utf-8"))
        files.update(replacements)
        issues.extend(warnings)
    # Retain historical top-level SQL scripts for compatibility; provide copies
    # in database/ for a controlled git mv after references are adjusted.
    legacy_sql = sorted((root / "sql").rglob("*.sql")) if (root / "sql").is_dir() else []
    for path in legacy_sql:
        relative = path.relative_to(root / "sql")
        files[(Path("database/sql/legacy") / relative).as_posix()] = path.read_text(
            encoding="utf-8"
        )
    files["database/isolated/__init__.py"] = (
        '"""Database-only query implementations generated by the P1 refactoring utility."""\n'
    )
    report = {
        "source": "feature/clean_ni",
        "expected_commit": "3b6d1dce6fc473318fc67f37fdf5ada0e2699006",
        "modified_and_generated_files": sorted(files),
        "issues": [dataclasses.asdict(item) for item in issues],
        "legacy_sql_copies": [str(x.relative_to(root)) for x in legacy_sql],
    }
    files["docs/QUERY_REFACTOR_GENERATED_REPORT.json"] = json.dumps(report, indent=2) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return len(files), issues


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--out", type=Path, default=Path("output/db_refactor_copy_in.zip"))
    a = p.parse_args()
    count, issues = build_package(a.root, a.out)
    print(f"Built {a.out}: {count} files; remaining/manual checks: {len(issues)}")
    for issue in issues[:100]:
        print(f"  {issue.file}:{issue.line}: {issue.problem}")
    print("This command did not edit your checkout. Review the generated ZIP before extracting.")
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
