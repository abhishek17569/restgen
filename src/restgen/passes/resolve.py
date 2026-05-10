"""Pass 3: Resolve model inheritance and verify handler references.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import ast as python_ast
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from restgen.ir.nodes import (
    AppNode,
    ConditionNode,
    ForEachStepNode,
    IfStepNode,
    ModelNode,
    FieldNode,
    MISSING,
    NamedPipelineNode,
    PipelineStepNode,
    ReturnStepNode,
    RouteNode,
)
from restgen.ir.types import FieldType
from restgen.passes.validate import CompilerError


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _topo_sort_models(models: list[ModelNode]) -> list[ModelNode]:
    """Topological sort of models by base dependency (parents first).

    Args:
        models: Unordered list of model nodes.

    Returns:
        Models sorted so that base models appear before derived ones.

    Raises:
        ValueError: If a cycle is detected (should already be caught by validate).

    Sig: 2026-04-14 created
    """
    model_map: dict[str, ModelNode] = {m.name: m for m in models}
    visited: set[str] = set()
    order: list[ModelNode] = []

    def visit(name: str, stack: set[str]) -> None:
        """DFS visit for topological sort. Sig: 2026-04-14 created"""
        if name in visited:
            return
        if name in stack:
            raise ValueError(f"Circular dependency detected at model '{name}'")
        stack.add(name)
        m = model_map.get(name)
        if m is None:
            return
        if m.base and m.base in model_map:
            visit(m.base, stack)
        stack.discard(name)
        visited.add(name)
        order.append(m)

    for m in models:
        visit(m.name, set())

    return order


def _resolve_model_fields(
    model: ModelNode,
    model_index: dict[str, ModelNode],
) -> list[FieldNode]:
    """Resolve inherited fields for a single derived model.

    Applies include/exclude/all_optional/overrides on top of the parent's
    resolved_fields, then merges the model's own fields.

    Args:
        model: The derived model node.
        model_index: Lookup table of all models (with parents already resolved).

    Returns:
        Final list of resolved FieldNode instances.

    Sig: 2026-04-14 created
    """
    parent = model_index[model.base]  # type: ignore[arg-type]
    inherited: dict[str, FieldNode] = {
        f.name: deepcopy(f) for f in parent.resolved_fields
    }

    # include filter
    if model.include is not None:
        include_set = set(model.include)
        inherited = {k: v for k, v in inherited.items() if k in include_set}

    # exclude filter
    if model.exclude is not None:
        exclude_set = set(model.exclude)
        inherited = {k: v for k, v in inherited.items() if k not in exclude_set}

    # all_optional
    if model.all_optional:
        for f in inherited.values():
            f.optional = True
            if not f.field_type.optional:
                # FieldType is frozen, so we need to replace it
                object.__setattr__(f, "field_type", FieldType(
                    scalar=f.field_type.scalar,
                    ref=f.field_type.ref,
                    list_of=f.field_type.list_of,
                    dict_of=f.field_type.dict_of,
                    enum_values=f.field_type.enum_values,
                    optional=True,
                ))

    # overrides
    if model.overrides:
        for field_name, override_dict in model.overrides.items():
            if field_name in inherited:
                f = inherited[field_name]
                for key, value in override_dict.items():
                    if hasattr(f, key):
                        setattr(f, key, value)

    # Merge own fields (own fields override inherited ones of same name)
    for f in model.fields:
        inherited[f.name] = deepcopy(f)

    return list(inherited.values())


def _collect_handler_strings(app: AppNode) -> list[tuple[str, str]]:
    """Collect all handler dotted-path strings from the IR.

    Returns a list of (handler_string, location) tuples.

    Args:
        app: The application IR tree.

    Returns:
        List of (handler_string, dotted_location) pairs.

    Sig: 2026-04-14 created
    """
    handlers: list[tuple[str, str]] = []

    for r in app.routes:
        loc = f"routes.{r.name or r.path}"
        if r.handler:
            handlers.append((r.handler, f"{loc}.handler"))
        if r.transform:
            handlers.append((r.transform, f"{loc}.transform"))
        if r.pipeline:
            _collect_pipeline_handlers(
                r.pipeline, f"{loc}.pipeline", handlers
            )

    for m in app.models:
        for cf in m.computed_fields:
            if cf.handler:
                handlers.append(
                    (cf.handler, f"models.{m.name}.computed_fields.{cf.name}.handler")
                )

    return handlers


def _verify_handler(
    handler: str,
    location: str,
    project_root: Path,
) -> CompilerError | None:
    """Verify a handler function exists on disk using AST parsing.

    Args:
        handler: Dotted path like "myapp.services.auth.verify_token".
        location: Dotted IR location for error reporting.
        project_root: Filesystem root for module resolution.

    Returns:
        A CompilerError warning if verification fails, else None.

    Sig: 2026-04-14 created
    """
    parts = handler.rsplit(".", 1)
    if len(parts) != 2:
        return CompilerError(
            severity="warning",
            code="W100",
            message=f"Handler '{handler}' is not a valid dotted path (expected 'module.function')",
            location=location,
        )

    module_path, func_name = parts
    file_path = project_root / (module_path.replace(".", "/") + ".py")

    if not file_path.exists():
        return CompilerError(
            severity="warning",
            code="W101",
            message=f"Handler module file '{file_path}' not found on disk",
            location=location,
        )

    try:
        source = file_path.read_text(encoding="utf-8")
        tree = python_ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return CompilerError(
            severity="warning",
            code="W102",
            message=f"Could not parse handler module '{file_path}': {exc}",
            location=location,
        )

    # Scan top-level function definitions
    for node in python_ast.iter_child_nodes(tree):
        if isinstance(node, (python_ast.FunctionDef, python_ast.AsyncFunctionDef)):
            if node.name == func_name:
                return None

    # Also check class methods (one level deep)
    for node in python_ast.iter_child_nodes(tree):
        if isinstance(node, python_ast.ClassDef):
            for child in python_ast.iter_child_nodes(node):
                if isinstance(child, (python_ast.FunctionDef, python_ast.AsyncFunctionDef)):
                    if child.name == func_name:
                        return None

    return CompilerError(
        severity="warning",
        code="W103",
        message=f"Function '{func_name}' not found in module '{file_path}'",
        location=location,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _collect_pipeline_handlers(
    steps: list,
    loc: str,
    out: list[tuple[str, str]],
) -> None:
    """Recursively collect handler strings from a pipeline sequence.

    Handles the four step-node types (including control flow), so handler
    verification reaches handlers nested inside ``if.then``/``if.else``
    and ``for_each.body``. Also collects ``when_handler`` from conditions.

    Sig: 2026-04-24 created
    """
    for idx, step in enumerate(steps):
        step_loc = f"{loc}[{idx}]"
        if isinstance(step, PipelineStepNode):
            if step.handler:
                out.append((step.handler, f"{step_loc}.handler"))
        elif isinstance(step, IfStepNode):
            if step.condition and step.condition.when_handler:
                out.append(
                    (step.condition.when_handler, f"{step_loc}.when_handler")
                )
            _collect_pipeline_handlers(step.then_steps, f"{step_loc}.then", out)
            _collect_pipeline_handlers(step.else_steps, f"{step_loc}.else", out)
        elif isinstance(step, ForEachStepNode):
            _collect_pipeline_handlers(step.body, f"{step_loc}.body", out)
        elif isinstance(step, ReturnStepNode):
            if step.condition and step.condition.when_handler:
                out.append(
                    (step.condition.when_handler, f"{step_loc}.when_handler")
                )


def _topo_sort_pipelines(
    pipelines: list[NamedPipelineNode],
) -> list[NamedPipelineNode]:
    """Topological sort of named pipelines by ``extends`` dependency.

    Args:
        pipelines: Named pipelines (unordered).

    Returns:
        Pipelines sorted so that bases appear before derivers.

    Sig: 2026-04-24 created
    """
    index: dict[str, NamedPipelineNode] = {p.name: p for p in pipelines}
    visited: set[str] = set()
    order: list[NamedPipelineNode] = []

    def visit(name: str, stack: set[str]) -> None:
        """Sig: 2026-04-24 created"""
        if name in visited or name not in index:
            return
        if name in stack:
            raise ValueError(f"Cycle in named-pipeline extends at '{name}'")
        stack.add(name)
        node = index[name]
        if node.extends:
            visit(node.extends, stack)
        stack.discard(name)
        visited.add(name)
        order.append(node)

    for p in pipelines:
        visit(p.name, set())
    return order


def _flatten_named_pipeline(
    pipeline: NamedPipelineNode,
    index: dict[str, NamedPipelineNode],
) -> list[PipelineStepNode]:
    """Flatten a named pipeline's inheritance into a concrete step list.

    Order: ``prepend`` → ``parent.resolved_steps`` (with ``override`` applied
    by ``as_name``) → own ``steps`` → ``append``. ``override`` keys not
    matching any inherited step are appended before ``append`` so that a
    child can introduce a step named by override even when the parent
    lacked it (useful for authoring symmetry).

    Args:
        pipeline: The pipeline to flatten.
        index: Name → NamedPipelineNode for parent lookup.

    Returns:
        Flat list of PipelineStepNode instances.

    Sig: 2026-04-24 created
    """
    parent_steps: list[PipelineStepNode] = []
    if pipeline.extends and pipeline.extends in index:
        parent_steps = [deepcopy(s) for s in index[pipeline.extends].resolved_steps]

    consumed_overrides: set[str] = set()
    resolved_parent: list[PipelineStepNode] = []
    for step in parent_steps:
        if step.as_name and step.as_name in pipeline.override:
            resolved_parent.append(deepcopy(pipeline.override[step.as_name]))
            consumed_overrides.add(step.as_name)
        else:
            resolved_parent.append(step)

    extras = [
        deepcopy(step)
        for key, step in pipeline.override.items()
        if key not in consumed_overrides
    ]

    # Authored steps that re-use an inherited ``as_name`` simply rebind
    # the variable at their position in the flattened order. The explicit
    # ``override:`` form is still honoured above; we do NOT drop inherited
    # steps based on name collision here, because an appended transform
    # commonly both reads and re-writes a binding (e.g. ``$order`` →
    # decorated ``$order``) and dropping the producer would break it.
    return (
        [deepcopy(s) for s in pipeline.prepend]
        + resolved_parent
        + extras
        + [deepcopy(s) for s in pipeline.steps]
        + [deepcopy(s) for s in pipeline.append]
    )


def _resolve_named_pipelines(app: AppNode) -> None:
    """Populate ``resolved_steps`` on every NamedPipelineNode in ``app``.

    Also inlines ``pipeline_ref`` on each route by copying the referenced
    named pipeline's resolved steps onto ``route.pipeline``.

    Args:
        app: The application IR.

    Side Effects:
        Mutates ``app.named_pipelines`` (populates resolved_steps) and
        ``app.routes`` (sets ``pipeline`` when ``pipeline_ref`` is set).

    Sig: 2026-04-24 created
    """
    if not app.named_pipelines:
        # Still inline pipeline_refs if anyone is referenced — though without
        # any named pipelines, referenced names won't resolve; validator handles.
        return

    sorted_pipelines = _topo_sort_pipelines(app.named_pipelines)
    index = app.named_pipeline_index
    for p in sorted_pipelines:
        p.resolved_steps = _flatten_named_pipeline(p, index)

    def _inline(route: RouteNode) -> None:
        """Sig: 2026-04-24 created"""
        if route.pipeline_ref and route.pipeline_ref in index:
            route.pipeline = [
                deepcopy(s) for s in index[route.pipeline_ref].resolved_steps
            ]

    for route in app.routes:
        _inline(route)
    for group in app.routers:
        for route in group.routes:
            _inline(route)


def resolve(app: AppNode, *, project_root: Path | None = None) -> AppNode:
    """Resolve all references in the IR.

    1. Model derivation: For each model with ``base``, copy fields from parent,
       apply include/exclude/all_optional/overrides, populate resolved_fields.
    2. For models without ``base``, resolved_fields = copy of fields.
    3. Handler verification: For every handler string, verify the module+function
       exists on disk (if project_root is provided). Uses ast.parse, no imports.
    4. Build/update model_index and error_index.

    Args:
        app: Validated AppNode.
        project_root: Filesystem root for handler module resolution. Optional.

    Returns:
        Mutated AppNode with resolved_fields populated.

    Sig: 2026-04-14 created
    """
    warnings: list[CompilerError] = []

    # -- 1 & 2: Resolve model inheritance ----------------------------------
    sorted_models = _topo_sort_models(app.models)

    # Build a live index that gets updated as we resolve
    model_index: dict[str, ModelNode] = {}
    for m in sorted_models:
        model_index[m.name] = m

    for m in sorted_models:
        if m.base is not None and m.base in model_index:
            m.resolved_fields = _resolve_model_fields(m, model_index)
            m.is_derived = True
        else:
            m.resolved_fields = [deepcopy(f) for f in m.fields]

    # -- 2b: Resolve named pipelines (flatten extends/prepend/override/append)
    _resolve_named_pipelines(app)

    # -- 3: Handler verification -------------------------------------------
    if project_root is not None:
        handler_refs = _collect_handler_strings(app)
        for handler_str, location in handler_refs:
            warning = _verify_handler(handler_str, location, project_root)
            if warning is not None:
                warnings.append(warning)

    # -- 4: Update indexes -------------------------------------------------
    app.model_index = {m.name: m for m in app.models}
    app.error_index = {e.name: e for e in app.errors}

    return app
