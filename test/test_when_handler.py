"""End-to-end tests for the `when_handler` escape hatch (Phase 7).

Sig: 2026-04-24 created
"""
from __future__ import annotations

import ast as pyast
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.restgen.compiler import compile_config


def _compile(tmp: str) -> Path:
    """Sig: 2026-04-24 created"""
    root = Path(tmp) / "api.yaml"
    handlers = Path(tmp) / "handlers" / "eligibility.py"
    handlers.parent.mkdir(parents=True, exist_ok=True)
    handlers.write_text(
        """
async def is_premium(user, plan):
    return plan == 'premium'
""",
        encoding="utf-8",
    )
    root.write_text(
        """
name: wh
models:
  User:
    fields:
      id:   { type: str, primary: true }
      plan: { type: str }
routes:
  - path: "/users/{id}/check"
    method: POST
    response_model: User
    errors:
      not_found: NF
    pipeline:
      - { action: db.get, model: User, args: { id: $path.id }, as: user }
      - if:
          when:
            when_handler: handlers.eligibility.is_premium
            captures: [$user, "$user.plan"]
          then:
            - { action: side_effect, handler: handlers.eligibility.is_premium, args: { user: $user, plan: $user } }
          else: []
      - return: { value: $user }
errors:
  NF: { status: 404, body: { message: "not found" } }
""",
        encoding="utf-8",
    )
    out = Path(tmp) / "gen"
    compile_config(config_path=root, output_dir=out, format_output=False)
    return out


def test_when_handler_emits_call_in_if() -> None:
    """The generated `if` uses a direct call to the handler, not a comparison.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = _compile(tmp)
        src = (out / "routes.py").read_text()
        # The condition compiles to `is_premium(user=..., plan=...)`.
        assert "if is_premium(" in src, src
        assert "from handlers.eligibility import is_premium" in src


def test_generated_module_parses() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        out = _compile(tmp)
        pyast.parse((out / "routes.py").read_text())


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
