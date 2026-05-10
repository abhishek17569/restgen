"""Compiler configuration settings.

Sig: 2026-04-14 created
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CompilerConfig:
    """Configuration for the restgen compiler.

    Args:
        output_dir: Target directory for generated files.
        format_output: Whether to auto-format generated code.
        verbose: Whether to print progress messages.

    Sig: 2026-04-14 created
    """
    output_dir: Path = Path("generated")
    format_output: bool = True
    verbose: bool = False
