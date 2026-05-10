"""Tests for the condition emitter (Phase 3).

Sig: 2026-04-24 created
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.restgen.ir.nodes import ConditionNode
from src.restgen.codegen.condition_emitter import lower_condition


def _resolve(ref):
    """Minimal resolver: ``$x`` -> Name("x"), literal -> Constant."""
    if isinstance(ref, str) and ref.startswith("$"):
        return ast.Name(id=ref[1:].split(".", 1)[0], ctx=ast.Load())
    return ast.Constant(value=ref)


def test_eq_compiles_to_Compare() -> None:
    """Sig: 2026-04-24 created"""
    cond = ConditionNode(op="eq", left="$x", right="paid")
    expr, imports = lower_condition(cond, _resolve)
    assert isinstance(expr, ast.Compare)
    assert isinstance(expr.ops[0], ast.Eq)
    assert imports == []


def test_is_null() -> None:
    """Sig: 2026-04-24 created"""
    cond = ConditionNode(op="is_null", left="$x")
    expr, _ = lower_condition(cond, _resolve)
    assert isinstance(expr.ops[0], ast.Is)
    assert isinstance(expr.comparators[0], ast.Constant) and expr.comparators[0].value is None


def test_and_or_not() -> None:
    """Sig: 2026-04-24 created"""
    tree = ConditionNode(
        op="and",
        children=[
            ConditionNode(op="eq", left="$x", right=1),
            ConditionNode(
                op="not",
                children=[ConditionNode(op="eq", left="$y", right=2)],
            ),
        ],
    )
    expr, _ = lower_condition(tree, _resolve)
    assert isinstance(expr, ast.BoolOp)
    assert isinstance(expr.op, ast.And)
    assert isinstance(expr.values[1], ast.UnaryOp)
    assert isinstance(expr.values[1].op, ast.Not)


def test_in_with_list() -> None:
    """Sig: 2026-04-24 created"""
    cond = ConditionNode(op="in", left="$role", right=["admin", "staff"])
    expr, _ = lower_condition(cond, _resolve)
    assert isinstance(expr.ops[0], ast.In)
    assert isinstance(expr.comparators[0], ast.List)


def test_when_handler_emits_call_and_import() -> None:
    """Sig: 2026-04-24 created"""
    cond = ConditionNode(
        when_handler="helpers.check.is_premium",
        captures=["$user", "$plan"],
    )
    expr, imports = lower_condition(cond, _resolve)
    assert isinstance(expr, ast.Call)
    assert expr.func.id == "is_premium"
    kwargs = {kw.arg for kw in expr.keywords}
    assert kwargs == {"user", "plan"}
    assert ("helpers.check", "is_premium") in imports


def test_unknown_op_raises() -> None:
    """Sig: 2026-04-24 created"""
    cond = ConditionNode(op="bogus", left="$x", right=1)
    try:
        lower_condition(cond, _resolve)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown op")


def _run_all() -> int:
    """Sig: 2026-04-24 created"""
    tests = [name for name in globals() if name.startswith("test_")]
    failed = 0
    for name in tests:
        try:
            globals()[name]()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    if failed:
        print(f"\n{failed}/{len(tests)} tests failed")
    else:
        print(f"\n{len(tests)}/{len(tests)} tests passed")
    return failed


if __name__ == "__main__":
    raise SystemExit(_run_all())
