"""Lower a ConditionNode to a Python `ast.expr` boolean expression.

Covers:

* Leaf comparisons: eq / ne / gt / ge / lt / le / in / not_in /
  is_null / is_not_null.
* Combinators: and / or (n-ary) and not (unary).
* Handler escape: ``when_handler: mod.func`` emits a Python call and
  reports the required import.

All lowering is done via the `ast` module — no `eval`, no string
concatenation, no runtime interpreter. The generated boolean is plugged
directly into ``ast.If(test=...)`` by the pipeline emitter.

Sig: 2026-04-24 created
"""
from __future__ import annotations

import ast

from restgen.ir.nodes import ConditionNode


# ---------------------------------------------------------------------------
# Operator maps
# ---------------------------------------------------------------------------

_COMPARE_OPS: dict[str, type[ast.cmpop]] = {
    "eq": ast.Eq,
    "ne": ast.NotEq,
    "gt": ast.Gt,
    "ge": ast.GtE,
    "lt": ast.Lt,
    "le": ast.LtE,
    "in": ast.In,
    "not_in": ast.NotIn,
}

_BOOL_OPS: dict[str, type[ast.boolop]] = {
    "and": ast.And,
    "or": ast.Or,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lower_condition(
    cond: ConditionNode,
    resolve_ref,  # callable: (ref_str) -> ast.expr
) -> tuple[ast.expr, list[tuple[str, str]]]:
    """Lower a ConditionNode to a boolean AST expression.

    Args:
        cond: The condition tree to lower.
        resolve_ref: Callback that maps a ``$ref`` string (or literal) to
            an AST expression. Provided by the pipeline emitter so that
            scope-aware resolution is shared across all lowering paths.

    Returns:
        Tuple of (expression, extra_imports). ``extra_imports`` is a list
        of ``(module, name)`` pairs the caller must add to the generated
        module's import block (populated only when ``when_handler`` is used).

    Raises:
        ValueError: If the condition's operator is unknown.

    Sig: 2026-04-24 created
    """
    extra_imports: list[tuple[str, str]] = []

    if cond.when_handler:
        handler = cond.when_handler
        module, _, func_name = handler.rpartition(".")
        if module:
            extra_imports.append((module, func_name))
        kwargs: list[ast.keyword] = []
        for ref in cond.captures:
            head = ref.lstrip("$").split(".", 1)[0]
            kwargs.append(ast.keyword(arg=head, value=resolve_ref(ref)))
        expr = ast.Call(
            func=ast.Name(id=func_name or handler, ctx=ast.Load()),
            args=[],
            keywords=kwargs,
        )
        return expr, extra_imports

    op = cond.op or ""

    if op in _BOOL_OPS:
        child_exprs: list[ast.expr] = []
        for child in cond.children:
            child_expr, child_imports = lower_condition(child, resolve_ref)
            child_exprs.append(child_expr)
            extra_imports.extend(child_imports)
        return ast.BoolOp(op=_BOOL_OPS[op](), values=child_exprs), extra_imports

    if op == "not":
        if not cond.children:
            raise ValueError("`not` condition requires one child")
        child_expr, child_imports = lower_condition(cond.children[0], resolve_ref)
        extra_imports.extend(child_imports)
        return ast.UnaryOp(op=ast.Not(), operand=child_expr), extra_imports

    if op == "is_null":
        return (
            ast.Compare(
                left=resolve_ref(cond.left),
                ops=[ast.Is()],
                comparators=[ast.Constant(value=None)],
            ),
            extra_imports,
        )

    if op == "is_not_null":
        return (
            ast.Compare(
                left=resolve_ref(cond.left),
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            ),
            extra_imports,
        )

    if op in _COMPARE_OPS:
        right = cond.right
        right_expr = _lower_right(right, resolve_ref)
        return (
            ast.Compare(
                left=resolve_ref(cond.left),
                ops=[_COMPARE_OPS[op]()],
                comparators=[right_expr],
            ),
            extra_imports,
        )

    raise ValueError(f"Unknown condition op: {op!r}")


def _lower_right(
    value,
    resolve_ref,
) -> ast.expr:
    """Lower a comparator's right-hand value (ref, literal, or list).

    Sig: 2026-04-24 created
    """
    if isinstance(value, str):
        return resolve_ref(value)
    if isinstance(value, list):
        return ast.List(
            elts=[_lower_right(v, resolve_ref) for v in value],
            ctx=ast.Load(),
        )
    if isinstance(value, dict):
        return ast.Dict(
            keys=[ast.Constant(value=k) for k in value.keys()],
            values=[_lower_right(v, resolve_ref) for v in value.values()],
        )
    return ast.Constant(value=value)
