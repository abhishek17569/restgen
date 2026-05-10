"""Tests for the `link` pass (Phase 1).

Runnable both under pytest and as a plain script:

    source .venv/bin/activate && python test/test_linker.py

Sig: 2026-04-24 created
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Ensure project root is on sys.path when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.restgen.schema.linker import (
    IMPORT_DIRECTIVE,
    IMPORTS_KEY,
    SOURCE_KEY,
    link,
)
from src.restgen.schema.loader import load_config


def _write(path: Path, content: str) -> None:
    """Write ``content`` to ``path``, creating parent dirs.

    Sig: 2026-04-24 created
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_legacy_passthrough() -> None:
    """A config without `$import` is returned unchanged (no __imports__ key).

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(root, "name: legacy\nversion: '1.0'\n")
        raw = load_config(root)
        merged, errors = link(raw, root)
        assert errors == [], errors
        assert IMPORTS_KEY not in merged
        assert merged == raw


def test_single_import_resolves_with_source() -> None:
    """`$import` entries land under __imports__ with __source__ set.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        shared = Path(tmp) / "shared" / "pipelines.yaml"
        _write(root, "$import:\n  shared: ./shared/pipelines.yaml\nname: app\n")
        _write(shared, "pipelines:\n  noop:\n    steps: []\n")
        raw = load_config(root)
        merged, errors = link(raw, root)
        assert errors == [], errors
        assert IMPORT_DIRECTIVE not in merged
        assert "shared" in merged[IMPORTS_KEY]
        shared_sub = merged[IMPORTS_KEY]["shared"]
        assert shared_sub["pipelines"]["noop"]["steps"] == []
        assert shared_sub[SOURCE_KEY] == Path("shared/pipelines.yaml")


def test_nested_imports() -> None:
    """Imports of imports are resolved transitively.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        a = Path(tmp) / "a.yaml"
        b = Path(tmp) / "b.yaml"
        _write(root, "$import:\n  a: ./a.yaml\n")
        _write(a, "$import:\n  b: ./b.yaml\n")
        _write(b, "name: leaf\n")
        merged, errors = link(load_config(root), root)
        assert errors == [], errors
        assert merged[IMPORTS_KEY]["a"][IMPORTS_KEY]["b"]["name"] == "leaf"


def test_cycle_detection() -> None:
    """A->B->A produces an E020 with the full chain.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.yaml"
        b = Path(tmp) / "b.yaml"
        _write(a, "$import:\n  b: ./b.yaml\n")
        _write(b, "$import:\n  a: ./a.yaml\n")
        merged, errors = link(load_config(a), a)
        codes = [e.code for e in errors]
        assert "E020" in codes, f"expected E020, got {errors}"
        msg = next(e.message for e in errors if e.code == "E020")
        assert "a.yaml" in msg and "b.yaml" in msg


def test_missing_import_file() -> None:
    """Unreadable import path yields E025.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(root, "$import:\n  missing: ./nope.yaml\n")
        _, errors = link(load_config(root), root)
        codes = [e.code for e in errors]
        assert "E025" in codes, f"expected E025, got {errors}"


def test_malformed_import_directive() -> None:
    """`$import` must be a mapping, not a list.

    Sig: 2026-04-24 created
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "api.yaml"
        _write(root, "$import:\n  - ./foo.yaml\n")
        _, errors = link(load_config(root), root)
        codes = [e.code for e in errors]
        assert "E024" in codes, f"expected E024, got {errors}"


def test_legacy_url_shortener_unchanged() -> None:
    """The existing url_shortener config must link to itself (no imports).

    Sig: 2026-04-24 created
    """
    project_root = Path(__file__).resolve().parent.parent
    cfg = project_root / "examples" / "url_shortener" / "api.yaml"
    raw = load_config(cfg)
    merged, errors = link(raw, cfg)
    assert errors == []
    assert merged == raw
    assert IMPORTS_KEY not in merged


def _run_all() -> int:
    """Run every test in this module as a script.

    Sig: 2026-04-24 created
    """
    tests = [name for name in globals() if name.startswith("test_")]
    failed = 0
    for name in tests:
        try:
            globals()[name]()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # unexpected
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
