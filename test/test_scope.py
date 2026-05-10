"""Tests for the shared Scope model (Phase 3).

Sig: 2026-04-24 created
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.restgen.scope import Scope


def test_builtin_prefixes_always_resolve() -> None:
    """Built-in refs resolve in an empty scope. Sig: 2026-04-24 created"""
    s = Scope()
    for ref in ("$path.id", "$body", "$body.name", "$query.limit", "$header.x", "$request"):
        assert s.resolves(ref), ref


def test_bind_and_pop() -> None:
    """Bindings die with their frame. Sig: 2026-04-24 created"""
    s = Scope()
    s.push()
    s.bind("x")
    assert s.resolves("$x")
    s.pop()
    assert not s.resolves("$x")


def test_promote_intersection() -> None:
    """Only names bound in both branches survive. Sig: 2026-04-24 created"""
    s = Scope()
    s.push()
    s.bind("a")
    s.bind("b")
    then_bound = s.pop()
    s.push()
    s.bind("b")
    s.bind("c")
    else_bound = s.pop()
    s.promote(then_bound & else_bound)
    assert s.resolves("$b")
    assert not s.resolves("$a")
    assert not s.resolves("$c")


def test_literal_refs_always_resolve() -> None:
    """Non-$ strings are literals, always resolve. Sig: 2026-04-24 created"""
    s = Scope()
    assert s.resolves("hello world")


def test_dotted_ref_head_checked() -> None:
    """`$user.email` checks for `user`. Sig: 2026-04-24 created"""
    s = Scope()
    s.bind("user")
    assert s.resolves("$user.email")
    assert not s.resolves("$admin.email")


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
