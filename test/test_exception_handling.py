"""Validate exception/error handling across compiler + generated code.

Covers:
* Compile-time validator fires the expected ``Exxx`` code for every
  broken input category (link, parse, validate, resolve).
* Linker raises/reports — not panics — on cycles, missing files, malformed
  ``$import``.
* Exhaustive step-type dispatch in pipeline_emitter raises AssertionError
  on unknown subclasses rather than silently emitting garbage.
* Generated ``errors.py`` module produces HTTPException subclasses with
  the right status codes and a registration function.
* Path-traversal guard in emit.py refuses to write outside ``output_dir``.

Sig: 2026-04-24 created
"""
from __future__ import annotations

import ast as pyast
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.restgen.compiler import compile_config, validate_config
from src.restgen.ir.nodes import (
    ConditionNode,
    ForEachStepNode,
    IfStepNode,
    PipelineStepNode,
    ReturnStepNode,
)


def _write(path: Path, content: str) -> None:
    """Sig: 2026-04-24 created"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# -- linker error paths -----------------------------------------------------

def test_link_cycle_is_reported_not_thrown() -> None:
    """A $import cycle produces an E020 error, not a Python exception.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.yaml"
        b = Path(tmp) / "b.yaml"
        _write(a, "$import:\n  b: ./b.yaml\n")
        _write(b, "$import:\n  a: ./a.yaml\n")
        errors = validate_config(a)
        assert any(e.code == "E020" for e in errors), errors


def test_missing_import_reported_as_E025() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(root, "$import:\n  x: ./nope.yaml\n")
        errors = validate_config(root)
        assert any(e.code == "E025" for e in errors), errors


def test_malformed_import_directive_E024() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(root, "$import:\n  - ./foo.yaml\n")
        errors = validate_config(root)
        assert any(e.code == "E024" for e in errors), errors


# -- pipeline validator: all Exxx codes ------------------------------------

def test_named_pipeline_unknown_extends_E022() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(root, "name: t\npipelines:\n  c:\n    extends: ghost\n    steps: []\n")
        errors = validate_config(root)
        assert any(e.code == "E022" for e in errors), errors


def test_duplicate_as_name_E023() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
pipelines:
  p:
    steps:
      - { action: side_effect, handler: h.a, as: x }
      - { action: side_effect, handler: h.b, as: x }
""",
        )
        errors = validate_config(root)
        assert any(e.code == "E023" for e in errors), errors


def test_return_unbound_E036() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models: { M: { fields: { id: { type: str, primary: true } } } }
routes:
  - path: "/x"
    method: POST
    response_model: M
    pipeline:
      - { return: { value: $ghost } }
""",
        )
        errors = validate_config(root)
        assert any(e.code == "E036" for e in errors), errors


def test_for_each_unbound_iterable_E034() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models: { M: { fields: { id: { type: str, primary: true } } } }
routes:
  - path: "/x"
    method: POST
    response_model: M
    pipeline:
      - for_each:
          in: $does_not_exist
          as: item
          body: []
""",
        )
        errors = validate_config(root)
        assert any(e.code == "E034" for e in errors), errors


def test_loop_var_shadow_W038() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models: { M: { fields: { id: { type: str, primary: true }, items: { type: list, items: any } } } }
routes:
  - path: "/x/{id}"
    method: POST
    response_model: M
    errors: { not_found: NF }
    pipeline:
      - { action: db.get, model: M, args: { id: $path.id }, as: item }
      - for_each:
          in: $item
          as: item
          body:
            - { action: side_effect, handler: h.f, args: { v: $item } }
errors: { NF: { status: 404, body: {} } }
""",
        )
        errors = validate_config(root)
        assert any(e.code == "W038" for e in errors), errors


def test_condition_missing_op_or_handler_E030() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models: { M: { fields: { id: { type: str, primary: true } } } }
routes:
  - path: "/x"
    method: POST
    response_model: M
    pipeline:
      - if:
          when: {}
          then: []
""",
        )
        errors = validate_config(root)
        assert any(e.code == "E030" for e in errors), errors


# -- emitter: exhaustiveness guard -----------------------------------------

def test_pipeline_emitter_rejects_unknown_step_type() -> None:
    """Unknown step subclasses raise AssertionError, not silent pass-through.

    Sig: 2026-04-24 created
    """
    from src.restgen.codegen.pipeline_emitter import _lower_sequence, _Ctx
    from src.restgen.ir.nodes import AppNode, RouteNode
    from src.restgen.scope import Scope

    class BogusStep:
        """Sig: 2026-04-24 created"""

        as_name = None

    ctx = _Ctx(
        route=RouteNode(),
        app=AppNode(),
        scope=Scope(),
        extra_imports=[],
    )
    try:
        _lower_sequence([BogusStep()], ctx)
    except AssertionError as exc:
        assert "BogusStep" in str(exc)
        return
    raise AssertionError("expected AssertionError for unknown step type")


# -- condition emitter: unknown op ----------------------------------------

def test_condition_emitter_raises_on_unknown_op() -> None:
    """Sig: 2026-04-24 created"""
    from src.restgen.codegen.condition_emitter import lower_condition

    cond = ConditionNode(op="bogus", left="$x", right=1)
    try:
        lower_condition(cond, lambda r: pyast.Constant(value=r))
    except ValueError as exc:
        assert "bogus" in str(exc)
        return
    raise AssertionError("expected ValueError for unknown condition op")


# -- emit: path-traversal guard -------------------------------------------

def test_emit_refuses_to_write_outside_output_dir() -> None:
    """A module with an absolute-escaping path must raise.

    Sig: 2026-04-24 created
    """
    from src.restgen.passes.emit import emit
    from src.restgen.codegen.ast_builder import make_module

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        bad_modules = {"../pwned.py": make_module([])}
        try:
            emit(bad_modules, output_dir=out, format_output=False)
        except ValueError as exc:
            assert "outside output_dir" in str(exc)
            return
        raise AssertionError("expected ValueError for traversing path")


# -- generated errors module -----------------------------------------------

def test_generated_errors_classes_and_status_codes() -> None:
    """Generated errors.py produces HTTPException subclasses with correct status.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: errtest
models: { M: { fields: { id: { type: str, primary: true } } } }
errors:
  NotFound: { status: 404, body: { message: "nf" } }
  Conflict: { status: 409, body: { message: "dup" } }
routes:
  - path: "/x"
    method: GET
    action: db.list
    model: M
""",
        )
        out = Path(tmp) / "gen"
        compile_config(config_path=root, output_dir=out, format_output=False)

        src = (out / "errors.py").read_text()
        assert "class NotFoundError(HTTPException)" in src
        assert "status_code=404" in src
        assert "class ConflictError(HTTPException)" in src
        assert "status_code=409" in src
        assert "def register_error_handlers" in src
        # Generated module must parse.
        pyast.parse(src)


def test_route_error_ref_raises_custom_exception() -> None:
    """`errors: { not_found: NotFound }` on a db.get route emits `raise NotFoundError()`.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: raisetest
models:
  Item:
    fields: { id: { type: str, primary: true } }
errors:
  NotFound: { status: 404, body: { message: "nf" } }
routes:
  - path: "/items/{id}"
    method: GET
    action: db.get
    model: Item
    errors:
      not_found: NotFound
""",
        )
        out = Path(tmp) / "gen"
        compile_config(config_path=root, output_dir=out, format_output=False)
        src = (out / "routes.py").read_text()
        assert "raise NotFoundError()" in src, src


def test_unknown_error_ref_E009() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models: { M: { fields: { id: { type: str, primary: true } } } }
routes:
  - path: "/x/{id}"
    method: GET
    action: db.get
    model: M
    errors:
      not_found: DoesNotExist
""",
        )
        errors = validate_config(root)
        assert any(e.code == "E009" for e in errors), errors


# -- compile_config: hard-error exit -----------------------------------------

def test_hard_errors_trigger_SystemExit() -> None:
    """Compilation with unrecoverable errors exits non-zero.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models:
  A: { base: Missing }
""",
        )
        out = Path(tmp) / "gen"
        try:
            compile_config(config_path=root, output_dir=out, format_output=False)
        except SystemExit as exc:
            assert exc.code != 0
            return
        raise AssertionError("expected SystemExit on hard errors")


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
