"""Top-level compiler orchestrator for restgen.

Chains all passes: load → parse → validate → resolve → optimize → lower → emit.

Sig: 2026-04-14 created
"""
from __future__ import annotations
import sys
from pathlib import Path

from restgen.schema.loader import load_config
from restgen.schema.linker import link
from restgen.passes.parse import parse
from restgen.passes.validate import validate, CompilerError, CompilationAborted
from restgen.passes.resolve import resolve
from restgen.passes.optimize import optimize
from restgen.passes.lower import lower
from restgen.passes.emit import emit


def _report_errors(errors: list[CompilerError]) -> None:
    """Print compiler errors to stderr.

    Sig: 2026-04-14 created
    """
    for err in errors:
        prefix = "ERROR" if err.severity == "error" else "WARN"
        print(f"  [{prefix}] {err.code}: {err.message} (at {err.location})", file=sys.stderr)


def compile_config(
    config_path: Path,
    output_dir: Path,
    *,
    format_output: bool = True,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[Path]:
    """Run the full compilation pipeline.

    Args:
        config_path: Path to the YAML/JSON config file.
        output_dir: Directory for generated Python files.
        format_output: Whether to format output with black/ruff.
        dry_run: If True, validate only without emitting files.
        verbose: Print progress to stdout.

    Returns:
        List of generated file paths (empty if dry_run).

    Raises:
        SystemExit: On validation errors.
        FileNotFoundError: If config file doesn't exist.

    Sig: 2026-04-14 created
    """
    if verbose:
        print(f"Loading config: {config_path}")
    raw = load_config(config_path)

    if verbose:
        print("Linking imports...")
    raw, link_errors = link(raw, config_path)
    if link_errors:
        hard_link_errors = [e for e in link_errors if e.severity == "error"]
        if hard_link_errors:
            print(
                f"Compilation failed with {len(hard_link_errors)} import error(s):",
                file=sys.stderr,
            )
            _report_errors(hard_link_errors)
            raise SystemExit(1)

    if verbose:
        print("Parsing config → IR...")
    app = parse(raw)

    if verbose:
        print("Validating IR...")
    errors = validate(app)
    warnings = [e for e in errors if e.severity == "warning"]
    hard_errors = [e for e in errors if e.severity == "error"]

    if warnings and verbose:
        print(f"  {len(warnings)} warning(s):")
        _report_errors(warnings)

    if hard_errors:
        print(f"Compilation failed with {len(hard_errors)} error(s):", file=sys.stderr)
        _report_errors(hard_errors)
        raise SystemExit(1)

    if verbose:
        print("Resolving references...")
    app = resolve(app, project_root=config_path.parent)

    if verbose:
        print("Optimizing IR...")
    app = optimize(app)

    if dry_run:
        if verbose:
            print("Dry run: validation passed, no files emitted.")
        return []

    if verbose:
        print("Lowering IR → AST...")
    modules = lower(app)

    if verbose:
        print(f"Emitting Python files to {output_dir}/...")
    paths = emit(modules, output_dir=output_dir, format_output=format_output, app=app)

    if verbose:
        print(f"Done. Generated {len(paths)} file(s).")
        for p in paths:
            print(f"  {p}")

    return paths


def validate_config(config_path: Path) -> list[CompilerError]:
    """Validate a config file without generating code.

    Args:
        config_path: Path to the YAML/JSON config file.

    Returns:
        List of compiler errors/warnings.

    Sig: 2026-04-14 created
    """
    raw = load_config(config_path)
    raw, link_errors = link(raw, config_path)
    app = parse(raw)
    return link_errors + validate(app)


def init_config(output: Path) -> None:
    """Generate a starter config file.

    Args:
        output: Path to write the starter config.

    Sig: 2026-04-14 created
    """
    starter = '''# restgen API configuration
# Documentation: https://github.com/restgen/restgen

name: my_api
version: "1.0"

database:
  type: memory  # Options: memory, postgres, sqlite, mongo

models:
  Item:
    fields:
      id: { type: uuid, primary: true, auto: true }
      name: { type: str, min_length: 1, max_length: 100 }
      description: { type: str, optional: true }
      price: { type: float, ge: 0 }
      created_at: { type: datetime, auto_now: true }

  ItemCreate:
    base: Item
    include: [name, description, price]

  ItemUpdate:
    base: Item
    include: [name, description, price]
    all_optional: true

errors:
  NotFound:
    status: 404
    body: { message: "Resource not found" }

routes:
  - path: /items
    method: GET
    action: db.list
    model: Item
    response_model: Item
    pagination: true
    filters: [name]

  - path: /items/{id}
    method: GET
    action: db.get
    model: Item
    response_model: Item
    errors:
      not_found: NotFound

  - path: /items
    method: POST
    action: db.create
    model: Item
    request_model: ItemCreate
    response_model: Item

  - path: /items/{id}
    method: PUT
    action: db.update
    model: Item
    request_model: ItemUpdate
    response_model: Item
    errors:
      not_found: NotFound

  - path: /items/{id}
    method: DELETE
    action: db.delete
    model: Item
    errors:
      not_found: NotFound

middleware:
  - kind: cors
    config:
      origins: ["*"]
      methods: ["*"]
      headers: ["*"]
'''
    output.write_text(starter, encoding="utf-8")
    print(f"Created starter config: {output}")
