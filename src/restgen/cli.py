"""CLI interface for restgen.

Sig: 2026-04-14 created
"""
from __future__ import annotations
import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build the restgen CLI argument parser.

    Sig: 2026-04-14 created
    """
    parser = argparse.ArgumentParser(
        prog="restgen",
        description="Compile REST API config to FastAPI Python code",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # compile
    compile_p = sub.add_parser("compile", help="Compile config to FastAPI code")
    compile_p.add_argument("config", type=Path, help="Path to YAML/JSON config")
    compile_p.add_argument("--out", "-o", type=Path, default=Path("generated"),
                           help="Output directory (default: generated/)")
    compile_p.add_argument("--no-format", action="store_true",
                           help="Skip code formatting")
    compile_p.add_argument("--dry-run", action="store_true",
                           help="Validate only, don't emit files")
    compile_p.add_argument("--verbose", "-v", action="store_true",
                           help="Verbose output")

    # validate
    validate_p = sub.add_parser("validate", help="Validate config without generating")
    validate_p.add_argument("config", type=Path, help="Path to YAML/JSON config")

    # schema
    schema_p = sub.add_parser("schema", help="Dump the DSL JSON Schema")
    schema_p.add_argument("--out", "-o", type=Path, help="Output file (default: stdout)")

    # init
    init_p = sub.add_parser("init", help="Generate a starter config file")
    init_p.add_argument("--out", "-o", type=Path, default=Path("api.yaml"),
                         help="Output path (default: api.yaml)")

    return parser


def main() -> None:
    """CLI entry point.

    Sig: 2026-04-14 created
    """
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "compile":
        from restgen.compiler import compile_config
        compile_config(
            config_path=args.config,
            output_dir=args.out,
            format_output=not args.no_format,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

    elif args.command == "validate":
        from restgen.compiler import validate_config
        errors = validate_config(config_path=args.config)
        if not errors:
            print("✓ Config is valid")
        else:
            for err in errors:
                prefix = "ERROR" if err.severity == "error" else "WARN"
                print(f"[{prefix}] {err.code}: {err.message} (at {err.location})")
            hard = [e for e in errors if e.severity == "error"]
            if hard:
                raise SystemExit(1)

    elif args.command == "schema":
        # TODO: Implement JSON Schema export
        print("JSON Schema export not yet implemented")

    elif args.command == "init":
        from restgen.compiler import init_config
        init_config(output=args.out)
