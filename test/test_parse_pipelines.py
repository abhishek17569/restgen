"""Tests for named pipelines (Phase 2): parse, flatten, validate, reference.

Runnable as a script or under pytest.

Sig: 2026-04-24 created
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.restgen.schema.linker import link
from src.restgen.schema.loader import load_config
from src.restgen.passes.parse import parse
from src.restgen.passes.validate import validate
from src.restgen.passes.resolve import resolve


def _compile_through_resolve(root_yaml: str, tmp_dir: str) -> tuple:
    """Load/link/parse/validate/resolve for a single YAML; return (app, errors).

    Sig: 2026-04-24 created
    """
    root = Path(tmp_dir) / "api.yaml"
    root.write_text(root_yaml, encoding="utf-8")
    raw = load_config(root)
    raw, link_errs = link(raw, root)
    app = parse(raw)
    validation_errors = validate(app)
    app = resolve(app, project_root=root.parent)
    return app, link_errs + validation_errors


def test_named_pipeline_top_level_basic() -> None:
    """A top-level `pipelines:` section produces NamedPipelineNode entries.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        app, errs = _compile_through_resolve(
            """
name: t
pipelines:
  greet:
    steps:
      - { action: side_effect, handler: h.say_hi }
""",
            tmp,
        )
        assert "greet" in app.named_pipeline_index
        assert len(app.named_pipeline_index["greet"].resolved_steps) == 1


def test_extends_prepend_override_append_order() -> None:
    """Flatten order is prepend → parent(with override) → extras → own → append.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        app, errs = _compile_through_resolve(
            """
name: t
pipelines:
  base:
    steps:
      - { action: side_effect, handler: h.a, as: a }
      - { action: side_effect, handler: h.b, as: b }
      - { action: side_effect, handler: h.c, as: c }
  child:
    extends: base
    prepend:
      - { action: side_effect, handler: h.pre, as: pre }
    override:
      b: { action: side_effect, handler: h.bprime, as: b }
    append:
      - { action: side_effect, handler: h.post, as: post }
""",
            tmp,
        )
        child = app.named_pipeline_index["child"]
        names = [s.as_name for s in child.resolved_steps]
        handlers = [s.handler for s in child.resolved_steps]
        assert names == ["pre", "a", "b", "c", "post"], names
        assert handlers[2] == "h.bprime", f"override not applied: {handlers}"


def test_override_with_unknown_parent_step_introduces_extra() -> None:
    """`override` keys that don't match any parent step are still included.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        app, errs = _compile_through_resolve(
            """
name: t
pipelines:
  base:
    steps:
      - { action: side_effect, handler: h.a, as: a }
  child:
    extends: base
    override:
      brand_new: { action: side_effect, handler: h.z, as: brand_new }
""",
            tmp,
        )
        names = [s.as_name for s in app.named_pipeline_index["child"].resolved_steps]
        assert "brand_new" in names and "a" in names


def test_route_pipeline_ref_inlines() -> None:
    """`pipeline: named_pipeline` on a route inlines the resolved steps.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        app, errs = _compile_through_resolve(
            """
name: t
models:
  User:
    fields: { id: { type: str, primary: true } }
pipelines:
  fetch_user:
    steps:
      - { action: db.get, model: User, args: { id: "$path.id" }, as: user }
routes:
  - path: /users/{id}
    method: GET
    pipeline: fetch_user
""",
            tmp,
        )
        route = app.routes[0]
        assert route.pipeline_ref == "fetch_user"
        assert route.pipeline is not None
        assert len(route.pipeline) == 1
        assert route.pipeline[0].as_name == "user"


def test_unknown_pipeline_ref_yields_E021() -> None:
    """Referencing a missing named pipeline produces E021.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        app, errs = _compile_through_resolve(
            """
name: t
models:
  User: { fields: { id: { type: str, primary: true } } }
routes:
  - path: /u
    method: GET
    pipeline: does_not_exist
""",
            tmp,
        )
        assert any(e.code == "E021" for e in errs), errs


def test_unknown_extends_yields_E022() -> None:
    """`extends` pointing to a missing pipeline produces E022.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        app, errs = _compile_through_resolve(
            """
name: t
pipelines:
  child:
    extends: ghost
    steps: []
""",
            tmp,
        )
        assert any(e.code == "E022" for e in errs), errs


def test_duplicate_as_name_yields_E023() -> None:
    """Two steps sharing the same `as:` within one authored block fires E023.

    Reappearing an inherited ``as:`` via append/prepend is treated as an
    intentional implicit override (not E023); only literal duplicates
    inside one authored list are errors.

    Sig: 2026-04-25 modified
    """
    with tempfile.TemporaryDirectory() as tmp:
        app, errs = _compile_through_resolve(
            """
name: t
pipelines:
  p:
    steps:
      - { action: side_effect, handler: h.a, as: x }
      - { action: side_effect, handler: h.b, as: x }
""",
            tmp,
        )
        assert any(e.code == "E023" for e in errs), errs


def test_rebind_via_append_is_not_E023() -> None:
    """Re-using an inherited `as:` via append rebinds the variable.

    The parent step still runs (producing its value) and the appended
    step reads and rewrites the same variable. Both steps appear in the
    flattened list in execution order; this is not an E023 error.

    Sig: 2026-04-27 modified
    """
    with tempfile.TemporaryDirectory() as tmp:
        app, errs = _compile_through_resolve(
            """
name: t
pipelines:
  base:
    steps:
      - { action: side_effect, handler: h.a, as: order }
  child:
    extends: base
    append:
      - { action: transform, handler: h.decorate, args: { order: $order }, as: order }
""",
            tmp,
        )
        assert not any(e.code == "E023" for e in errs), errs
        child = app.named_pipeline_index["child"]
        names = [s.as_name for s in child.resolved_steps]
        assert names == ["order", "order"], names
        # Ensure the order is: parent step first, appended step second.
        handlers = [s.handler for s in child.resolved_steps]
        assert handlers == ["h.a", "h.decorate"], handlers


def test_cross_file_named_pipeline_via_import() -> None:
    """An imported file's named pipeline is reachable as `alias.name`.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        shared = Path(tmp) / "shared" / "pipelines.yaml"
        shared.parent.mkdir()
        shared.write_text(
            """
pipelines:
  greet:
    steps:
      - { action: side_effect, handler: h.hi, as: g }
""",
            encoding="utf-8",
        )
        root.write_text(
            """
name: t
$import:
  shared: ./shared/pipelines.yaml
models:
  U: { fields: { id: { type: str, primary: true } } }
routes:
  - path: /g
    method: GET
    pipeline: shared.greet
""",
            encoding="utf-8",
        )
        raw = load_config(root)
        raw, link_errs = link(raw, root)
        app = parse(raw)
        val_errs = validate(app)
        app = resolve(app, project_root=root.parent)
        errs = link_errs + val_errs
        assert "shared.greet" in app.named_pipeline_index, list(
            app.named_pipeline_index.keys()
        )
        assert not any(e.severity == "error" for e in errs), errs
        route = app.routes[0]
        assert route.pipeline is not None and len(route.pipeline) == 1


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
