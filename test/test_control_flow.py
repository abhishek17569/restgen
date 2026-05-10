"""End-to-end control-flow tests (Phases 3 + 4): if/else, for_each, return.

Sig: 2026-04-24 created
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.restgen.compiler import compile_config


def _compile(yaml: str) -> Path:
    """Compile a YAML string to /tmp and return the generated dir.

    Sig: 2026-04-24 created
    """
    tmp = tempfile.mkdtemp(prefix="cf_")
    root = Path(tmp) / "api.yaml"
    root.write_text(yaml, encoding="utf-8")
    out = Path(tmp) / "gen"
    compile_config(config_path=root, output_dir=out, format_output=False)
    return out


def test_if_else_generates_ast_if() -> None:
    """A pipeline with if/else must produce an `if:` in generated routes.py.

    Sig: 2026-04-24 created
    """
    out = _compile(
        """
name: cf_if
models:
  Order:
    fields:
      id: { type: str, primary: true }
      status: { type: str }
routes:
  - path: /orders/{id}/maybe
    method: POST
    response_model: Order
    errors:
      not_found: NF
    pipeline:
      - { action: db.get, model: Order, args: { id: $path.id }, as: order }
      - if:
          when: { op: eq, left: $order.status, right: "paid" }
          then:
            - { action: transform, handler: handlers.x.finalize, args: { order: $order }, as: done }
          else:
            - { action: transform, handler: handlers.x.refund, args: { order: $order }, as: done }
      - return: { value: $done }
errors:
  NF: { status: 404, body: { message: "not found" } }
"""
    )
    src = (out / "routes.py").read_text()
    assert "if order.status == 'paid':" in src, src
    assert "finalize(order=order)" in src
    assert "refund(order=order)" in src
    assert "return done" in src


def test_for_each_generates_ast_for() -> None:
    """A for_each step produces a `for item in ...` loop.

    Sig: 2026-04-24 created
    """
    out = _compile(
        """
name: cf_loop
models:
  Order:
    fields:
      id: { type: str, primary: true }
      items: { type: list, items: any }
routes:
  - path: /orders/{id}/fanout
    method: POST
    response_model: Order
    errors:
      not_found: NF
    pipeline:
      - { action: db.get, model: Order, args: { id: $path.id }, as: order }
      - for_each:
          in: $order.items
          as: item
          body:
            - { action: side_effect, handler: handlers.fan.ping, args: { sku: $item } }
      - return: { value: $order }
errors:
  NF: { status: 404, body: { message: "nf" } }
"""
    )
    src = (out / "routes.py").read_text()
    assert "for item in order.items:" in src, src
    assert "ping(sku=item)" in src


def test_return_early_suppresses_trailing() -> None:
    """An unconditional top-level `return` suppresses the synthetic trailing return.

    Sig: 2026-04-24 created
    """
    out = _compile(
        """
name: cf_return
models:
  Order:
    fields: { id: { type: str, primary: true } }
routes:
  - path: /orders/{id}/x
    method: POST
    response_model: Order
    errors:
      not_found: NF
    pipeline:
      - { action: db.get, model: Order, args: { id: $path.id }, as: order }
      - return: { value: $order }
errors:
  NF: { status: 404, body: {} }
"""
    )
    src = (out / "routes.py").read_text()
    assert src.count("return order") == 1, src


def test_unbound_ref_raises_E010() -> None:
    """A step that references an undeclared binding should fail compile.

    Sig: 2026-04-24 created
    """
    from src.restgen.compiler import validate_config

    tmp = tempfile.mkdtemp(prefix="cf_bad_")
    root = Path(tmp) / "api.yaml"
    root.write_text(
        """
name: cf_bad
models:
  Order: { fields: { id: { type: str, primary: true } } }
routes:
  - path: /x
    method: POST
    response_model: Order
    pipeline:
      - { action: side_effect, handler: h.f, args: { nope: $ghost } }
""",
        encoding="utf-8",
    )
    errors = validate_config(root)
    assert any(e.code == "E010" for e in errors), errors


def test_for_each_inner_binding_dies_E035() -> None:
    """A binding made inside the loop body is not visible after the loop.

    Sig: 2026-04-24 created
    """
    from src.restgen.compiler import validate_config

    tmp = tempfile.mkdtemp(prefix="cf_scope_")
    root = Path(tmp) / "api.yaml"
    root.write_text(
        """
name: cf_scope
models:
  Order:
    fields:
      id: { type: str, primary: true }
      items: { type: list, items: any }
routes:
  - path: /x
    method: POST
    response_model: Order
    pipeline:
      - { action: db.get, model: Order, args: { id: $path.id }, as: order }
      - for_each:
          in: $order.items
          as: item
          body:
            - { action: side_effect, handler: h.f, args: { v: $item }, as: inner }
      - { action: side_effect, handler: h.g, args: { v: $inner } }
""",
        encoding="utf-8",
    )
    errors = validate_config(root)
    assert any(e.code == "E010" for e in errors), errors


def test_if_both_branches_bind_is_visible_after() -> None:
    """A name bound in both `then` and `else` survives the `if`.

    Sig: 2026-04-24 created
    """
    from src.restgen.compiler import validate_config

    tmp = tempfile.mkdtemp(prefix="cf_if_scope_")
    root = Path(tmp) / "api.yaml"
    root.write_text(
        """
name: cf_if_scope
models:
  Order:
    fields: { id: { type: str, primary: true }, status: { type: str } }
routes:
  - path: /x/{id}
    method: POST
    response_model: Order
    errors:
      not_found: NF
    pipeline:
      - { action: db.get, model: Order, args: { id: $path.id }, as: order }
      - if:
          when: { op: eq, left: $order.status, right: "paid" }
          then:
            - { action: transform, handler: h.a, args: { order: $order }, as: result }
          else:
            - { action: transform, handler: h.b, args: { order: $order }, as: result }
      - { action: side_effect, handler: h.use, args: { r: $result } }
errors:
  NF: { status: 404, body: {} }
""",
        encoding="utf-8",
    )
    errors = validate_config(root)
    assert not any(e.code == "E010" for e in errors), errors


def test_unknown_condition_op_E030() -> None:
    """Sig: 2026-04-24 created"""
    from src.restgen.compiler import validate_config

    tmp = tempfile.mkdtemp(prefix="cf_e030_")
    root = Path(tmp) / "api.yaml"
    root.write_text(
        """
name: e
models:
  Order: { fields: { id: { type: str, primary: true }, status: { type: str } } }
routes:
  - path: /x/{id}
    method: POST
    response_model: Order
    errors:
      not_found: NF
    pipeline:
      - { action: db.get, model: Order, args: { id: $path.id }, as: order }
      - if:
          when: { op: BADOP, left: $order.status, right: paid }
          then: []
errors:
  NF: { status: 404, body: {} }
""",
        encoding="utf-8",
    )
    errors = validate_config(root)
    assert any(e.code == "E030" for e in errors), errors


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
