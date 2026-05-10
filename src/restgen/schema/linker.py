"""Link pass: resolve `$import` directives across multiple YAML files.

Position in pipeline: load → **link** → parse → validate → resolve → optimize → lower → emit.

The link pass walks `$import: {alias: path}` declarations in the root config,
recursively loads each referenced file, and attaches the result under the
reserved `__imports__` key. Downstream passes resolve dotted references
(``shared.fulfill_order``) by walking this tree. A hidden ``__source__``
key on each imported subdict records the originating file path relative
to the root, which later phases consume to mirror the DSL tree into the
generated output directory.

Sig: 2026-04-24 created
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from restgen.schema.loader import load_config
from restgen.passes.validate import CompilerError


IMPORTS_KEY = "__imports__"
SOURCE_KEY = "__source__"
IMPORT_DIRECTIVE = "$import"


def link(
    root_raw: dict[str, Any],
    root_path: Path,
) -> tuple[dict[str, Any], list[CompilerError]]:
    """Resolve `$import` aliases in ``root_raw`` recursively.

    Args:
        root_raw: The parsed root config dict (output of ``load_config``).
        root_path: Absolute path to the root config file.

    Returns:
        Tuple ``(merged, errors)``. ``merged`` is ``root_raw`` augmented with
        an ``__imports__`` mapping from alias to recursively-linked subdict.
        If ``root_raw`` declares no ``$import``, the merged dict is
        byte-equivalent to the input (no ``__imports__`` key added) —
        legacy single-file configs are unchanged.

    Sig: 2026-04-24 created
    """
    errors: list[CompilerError] = []
    project_root = root_path.parent.resolve()
    visiting: list[Path] = [root_path.resolve()]
    merged = _link_dict(
        raw=root_raw,
        current_path=root_path.resolve(),
        project_root=project_root,
        visiting=visiting,
        errors=errors,
    )
    return merged, errors


def _link_dict(
    *,
    raw: dict[str, Any],
    current_path: Path,
    project_root: Path,
    visiting: list[Path],
    errors: list[CompilerError],
) -> dict[str, Any]:
    """Recursively resolve `$import` aliases within a single raw dict.

    Args:
        raw: The dict to link.
        current_path: Absolute path of the file ``raw`` was loaded from.
        project_root: Absolute path of the root config's parent directory.
        visiting: DFS stack of absolute paths, used for cycle detection.
        errors: Accumulator for ``CompilerError`` entries.

    Returns:
        A new dict with ``$import`` replaced by an ``__imports__`` subtree.

    Side Effects:
        Appends to ``errors`` on cycles or unreadable imports.

    Sig: 2026-04-24 created
    """
    if IMPORT_DIRECTIVE not in raw:
        return raw

    imports_spec = raw[IMPORT_DIRECTIVE]
    if not isinstance(imports_spec, dict):
        errors.append(
            CompilerError(
                severity="error",
                code="E024",
                message=(
                    f"`$import` must be a mapping of alias to path, got "
                    f"{type(imports_spec).__name__}"
                ),
                location=str(_rel(current_path, project_root)),
            )
        )
        return {k: v for k, v in raw.items() if k != IMPORT_DIRECTIVE}

    resolved_imports: dict[str, Any] = {}
    base_dir = current_path.parent

    for alias, rel_path in imports_spec.items():
        if not isinstance(alias, str) or not alias:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E024",
                    message=f"Invalid `$import` alias: {alias!r}",
                    location=str(current_path),
                )
            )
            continue

        target = (base_dir / str(rel_path)).resolve()

        if target in visiting:
            chain = " -> ".join(str(_rel(p, project_root)) for p in visiting + [target])
            errors.append(
                CompilerError(
                    severity="error",
                    code="E020",
                    message=f"`$import` cycle detected: {chain}",
                    location=str(_rel(current_path, project_root)),
                )
            )
            continue

        try:
            sub_raw = load_config(target)
        except FileNotFoundError:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E025",
                    message=(
                        f"Imported file not found: {rel_path!r} "
                        f"(resolved to {target})"
                    ),
                    location=str(_rel(current_path, project_root)),
                )
            )
            continue
        except Exception as exc:  # malformed YAML, unreadable, etc.
            errors.append(
                CompilerError(
                    severity="error",
                    code="E026",
                    message=f"Failed to load {rel_path!r}: {exc}",
                    location=str(_rel(current_path, project_root)),
                )
            )
            continue

        visiting.append(target)
        linked_sub = _link_dict(
            raw=sub_raw,
            current_path=target,
            project_root=project_root,
            visiting=visiting,
            errors=errors,
        )
        visiting.pop()

        linked_sub[SOURCE_KEY] = _rel(target, project_root)
        resolved_imports[alias] = linked_sub

    merged = {k: v for k, v in raw.items() if k != IMPORT_DIRECTIVE}
    merged[IMPORTS_KEY] = resolved_imports
    return merged


def _rel(path: Path, root: Path) -> Path:
    """Return ``path`` relative to ``root`` when possible, else ``path``.

    Sig: 2026-04-24 created
    """
    try:
        return path.relative_to(root)
    except ValueError:
        return path
