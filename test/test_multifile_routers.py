"""End-to-end tests for multi-file router layout (Phase 5+6).

Sig: 2026-04-24 created
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.restgen.compiler import compile_config, validate_config


def _write(path: Path, content: str) -> None:
    """Sig: 2026-04-24 created"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_project(tmp: str) -> Path:
    """Write a multi-file router fixture and return the root path.

    Sig: 2026-04-24 created
    """
    root = Path(tmp) / "api.yaml"
    users = Path(tmp) / "routers" / "users.yaml"
    orders = Path(tmp) / "routers" / "orders.yaml"
    shared = Path(tmp) / "shared" / "pipelines.yaml"

    _write(
        root,
        """
name: shop
version: "1.0"
$import:
  users:  ./routers/users.yaml
  orders: ./routers/orders.yaml
  shared: ./shared/pipelines.yaml

models:
  User:
    fields:
      id:   { type: str, primary: true }
      name: { type: str }
  Order:
    fields:
      id:   { type: str, primary: true }
      total: { type: float }

routers: [users, orders]
""",
    )
    _write(
        users,
        """
router:
  prefix: /users
  tags: [Users]
  routes:
    - path: /
      method: GET
      action: db.list
      model: User
    - path: "/{id}"
      method: GET
      action: db.get
      model: User
""",
    )
    _write(
        orders,
        """
router:
  prefix: /orders
  tags: [Orders]
  routes:
    - path: "/{id}"
      method: GET
      pipeline: shared.fetch_order
""",
    )
    _write(
        shared,
        """
pipelines:
  fetch_order:
    steps:
      - { action: db.get, model: Order, args: { id: $path.id }, as: order }
""",
    )
    return root


def test_mirrored_layout_generates_routers_dir() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = _build_project(tmp)
        out = Path(tmp) / "gen"
        compile_config(config_path=root, output_dir=out, format_output=False)

        assert (out / "routers" / "users.py").exists()
        assert (out / "routers" / "orders.py").exists()
        assert (out / "routers" / "__init__.py").exists()
        # Flat routes.py must NOT exist in router mode.
        assert not (out / "routes.py").exists()


def test_app_py_wires_each_router_with_prefix_and_tags() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = _build_project(tmp)
        out = Path(tmp) / "gen"
        compile_config(config_path=root, output_dir=out, format_output=False)

        app_src = (out / "app.py").read_text()
        assert "from .routers.users import router as users_router" in app_src, app_src
        assert "from .routers.orders import router as orders_router" in app_src, app_src
        assert "include_router(users_router" in app_src
        assert "prefix='/users'" in app_src or 'prefix="/users"' in app_src
        assert "['Users']" in app_src or '["Users"]' in app_src


def test_router_module_uses_double_dot_imports() -> None:
    """Each `routers/<x>.py` imports from the parent package.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _build_project(tmp)
        out = Path(tmp) / "gen"
        compile_config(config_path=root, output_dir=out, format_output=False)

        users_src = (out / "routers" / "users.py").read_text()
        assert "from ..models import" in users_src
        assert "from ..dependencies import" in users_src
        # The APIRouter has no prefix — that's applied by include_router.
        assert "APIRouter()" in users_src, users_src


def test_empty_router_yields_E040() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models: { U: { fields: { id: { type: str, primary: true } } } }
routers:
  - { name: empty, prefix: /x, routes: [] }
""",
        )
        errors = validate_config(root)
        assert any(e.code == "E040" for e in errors), errors


def test_router_prefix_must_start_with_slash_E042() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models: { U: { fields: { id: { type: str, primary: true } } } }
routers:
  - name: bad
    prefix: "no-slash"
    routes:
      - { path: /, method: GET, action: db.list, model: U }
""",
        )
        errors = validate_config(root)
        assert any(e.code == "E042" for e in errors), errors


def test_duplicate_router_names_E041() -> None:
    """Sig: 2026-04-24 created"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(
            root,
            """
name: t
models: { U: { fields: { id: { type: str, primary: true } } } }
routers:
  - name: dup
    prefix: /a
    routes: [{ path: /, method: GET, action: db.list, model: U }]
  - name: dup
    prefix: /b
    routes: [{ path: /, method: GET, action: db.list, model: U }]
""",
        )
        errors = validate_config(root)
        assert any(e.code == "E041" for e in errors), errors


def test_generated_router_module_imports_compile() -> None:
    """The generated routers/*.py files must parse as valid Python.

    Sig: 2026-04-24 created
    """
    import ast as pyast

    with tempfile.TemporaryDirectory() as tmp:
        root = _build_project(tmp)
        out = Path(tmp) / "gen"
        compile_config(config_path=root, output_dir=out, format_output=False)

        for name in ("routers/users.py", "routers/orders.py", "app.py"):
            src = (out / name).read_text()
            pyast.parse(src)  # raises SyntaxError if malformed


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
