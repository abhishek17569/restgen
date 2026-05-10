"""Pass 2: Validate IR for structural correctness.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from restgen.ir.nodes import (
    ActionKind,
    AppNode,
    BackgroundTaskRef,
    CacheConfig,
    ConditionNode,
    CookieNode,
    DependencyRef,
    FileParamNode,
    ForEachStepNode,
    HealthCheckConfig,
    HttpMethod,
    IfStepNode,
    ModelNode,
    MountConfig,
    NamedPipelineNode,
    OpenAPIExtras,
    ParamSource,
    PipelineStepNode,
    RateLimitConfig,
    ResponseHeaderNode,
    ResponseKind,
    ReturnStepNode,
    RouteNode,
    RouteParamNode,
    SecuritySchemeNode,
    StreamingConfig,
    TestConfig,
    WebSocketRouteNode,
)
from restgen.scope import Scope
from restgen.ir.types import ScalarType


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@dataclass
class CompilerError:
    """A validation error or warning produced by the compiler.

    Args:
        severity: Error level.
        code: Machine-readable error code.
        message: Human-readable description.
        location: Dotted path to the problematic node.

    Sig: 2026-04-14 created
    """

    severity: Literal["error", "warning"]
    code: str
    message: str
    location: str


class CompilationAborted(Exception):
    """Raised when critical errors make further compilation impossible.

    Sig: 2026-04-14 created
    """

    def __init__(self, errors: list[CompilerError]):
        self.errors = errors
        super().__init__(f"Compilation aborted with {len(errors)} error(s)")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")

# Built-in pipeline refs that don't need a prior step name.
_BUILTIN_REF_PREFIXES = ("$path.", "$body", "$query.", "$header.", "$request")

_VALID_SCALARS = {s.value for s in ScalarType}


def _extract_path_params(path: str) -> list[str]:
    """Extract parameter names from a URL path template.

    Sig: 2026-04-14 created
    """
    return _PATH_PARAM_RE.findall(path)


def _has_cycle(name: str, base_map: dict[str, str | None], visited: set[str], stack: set[str]) -> list[str] | None:
    """DFS cycle detection in the model derivation graph.

    Returns the cycle path if one is found, else None.

    Sig: 2026-04-14 created
    """
    visited.add(name)
    stack.add(name)
    parent = base_map.get(name)
    if parent is not None:
        if parent in stack:
            return [name, parent]
        if parent not in visited:
            cycle = _has_cycle(parent, base_map, visited, stack)
            if cycle is not None:
                return [name] + cycle
    stack.discard(name)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(app: AppNode) -> list[CompilerError]:
    """Validate IR for structural correctness.

    Checks performed:
    1. No duplicate model names
    2. No duplicate route names
    3. No duplicate error names
    4. Each route has exactly one of: action, pipeline, handler
    5. CRUD action routes must have a ``model`` field
    6. Model field types reference valid scalars
    7. Model ``base`` references exist in model_index
    8. No circular model derivation (A derives B derives A)
    9. Error refs in routes reference declared error names (or are inline)
    10. Pipeline step ``args`` with $refs reference valid prior step names
    11. Path params in route path match declared path_params
    12. request_model and response_model reference declared models
    13. Pipeline actions reference valid ActionKind values (handler
        presence for non-db actions)

    Args:
        app: The parsed AppNode IR tree.

    Returns:
        List of CompilerError. Empty means valid.

    Sig: 2026-04-14 created
    """
    errors: list[CompilerError] = []

    # Build quick lookup structures ----------------------------------------
    model_names: dict[str, int] = {}
    for idx, m in enumerate(app.models):
        model_names.setdefault(m.name, idx)

    route_names: dict[str, int] = {}
    for idx, r in enumerate(app.routes):
        if r.name:
            route_names.setdefault(r.name, idx)

    error_names: set[str] = set()
    for e in app.errors:
        error_names.add(e.name)

    # 1. Duplicate model names ---------------------------------------------
    seen_models: set[str] = set()
    for m in app.models:
        if m.name in seen_models:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E001",
                    message=f"Duplicate model name '{m.name}'",
                    location=f"models.{m.name}",
                )
            )
        seen_models.add(m.name)

    # 2. Duplicate route names ---------------------------------------------
    seen_routes: set[str] = set()
    for r in app.routes:
        if not r.name:
            continue
        if r.name in seen_routes:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E002",
                    message=f"Duplicate route name '{r.name}'",
                    location=f"routes.{r.name}",
                )
            )
        seen_routes.add(r.name)

    # 3. Duplicate error names ---------------------------------------------
    seen_errors: set[str] = set()
    for e in app.errors:
        if e.name in seen_errors:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E003",
                    message=f"Duplicate error name '{e.name}'",
                    location=f"errors.{e.name}",
                )
            )
        seen_errors.add(e.name)

    # 4. Each route has exactly one dispatch mechanism ---------------------
    for r in app.routes:
        loc = f"routes.{r.name or r.path}"
        dispatch_count = sum([
            r.action is not None,
            r.pipeline is not None,
            r.handler is not None,
            r.pipeline_ref is not None,
        ])
        if dispatch_count == 0:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E004",
                    message="Route must define exactly one of: action, pipeline, handler",
                    location=loc,
                )
            )
        elif dispatch_count > 1:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E004",
                    message="Route must define exactly one of: action, pipeline, handler (found multiple)",
                    location=loc,
                )
            )

    # 5. CRUD action routes must have a model field -----------------------
    _crud_actions = {
        ActionKind.DB_LIST,
        ActionKind.DB_GET,
        ActionKind.DB_CREATE,
        ActionKind.DB_UPDATE,
        ActionKind.DB_DELETE,
    }
    for r in app.routes:
        loc = f"routes.{r.name or r.path}"
        if r.action is not None and r.action in _crud_actions and not r.model:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E005",
                    message=f"CRUD action '{r.action.value}' requires a 'model' field",
                    location=loc,
                )
            )

    # 6. Model field types reference valid scalars -------------------------
    for m in app.models:
        for f in m.fields:
            _validate_field_type(f.field_type, f"models.{m.name}.fields.{f.name}", errors, seen_models)

    # 7. Model base references exist ---------------------------------------
    for m in app.models:
        if m.base is not None and m.base not in seen_models:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E007",
                    message=f"Model '{m.name}' derives from unknown model '{m.base}'",
                    location=f"models.{m.name}",
                )
            )

    # 8. Circular model derivation -----------------------------------------
    base_map: dict[str, str | None] = {m.name: m.base for m in app.models}
    visited: set[str] = set()
    for m in app.models:
        if m.name not in visited:
            stack: set[str] = set()
            cycle = _has_cycle(m.name, base_map, visited, stack)
            if cycle is not None:
                cycle_str = " -> ".join(cycle)
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E008",
                        message=f"Circular model derivation detected: {cycle_str}",
                        location=f"models.{m.name}",
                    )
                )

    # 9. Error refs in routes reference declared errors --------------------
    for r in app.routes:
        loc = f"routes.{r.name or r.path}"
        for i, eref in enumerate(r.errors):
            if eref.ref is not None and eref.ref not in error_names:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E009",
                        message=f"Error ref '{eref.ref}' not found in declared errors",
                        location=f"{loc}.errors[{i}]",
                    )
                )

    # 10. Pipeline step $ref validation (recursive walk; control flow aware)
    for r in app.routes:
        if r.pipeline is None:
            continue
        loc = f"routes.{r.name or r.path}"
        _validate_pipeline_sequence(r.pipeline, loc, Scope(), errors)

    # 11. Path params match declared path_params ---------------------------
    for r in app.routes:
        loc = f"routes.{r.name or r.path}"
        path_param_names = set(_extract_path_params(r.path))
        declared_param_names = {p.name for p in r.path_params}

        for pp in path_param_names - declared_param_names:
            errors.append(
                CompilerError(
                    severity="warning",
                    code="W011",
                    message=f"Path parameter '{{{pp}}}' in path but not declared in path_params",
                    location=loc,
                )
            )
        for dp in declared_param_names - path_param_names:
            errors.append(
                CompilerError(
                    severity="warning",
                    code="W011",
                    message=f"Declared path_param '{dp}' not found in path '{r.path}'",
                    location=loc,
                )
            )

    # 12. request_model and response_model reference declared models ------
    for r in app.routes:
        loc = f"routes.{r.name or r.path}"
        if r.request_model and r.request_model not in seen_models:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E012",
                    message=f"request_model '{r.request_model}' not found in declared models",
                    location=loc,
                )
            )
        if r.response_model and r.response_model not in seen_models:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E012",
                    message=f"response_model '{r.response_model}' not found in declared models",
                    location=loc,
                )
            )

    # 13. Pipeline actions: non-db actions need a handler (walks control flow)
    for r in app.routes:
        if r.pipeline is None:
            continue
        loc = f"routes.{r.name or r.path}"
        _validate_handler_presence(r.pipeline, loc, errors)

    # 14. Named pipeline references and inheritance -----------------------
    named_pipeline_names = {p.name for p in app.named_pipelines}

    for r in app.routes:
        if r.pipeline_ref and r.pipeline_ref not in named_pipeline_names:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E021",
                    message=(
                        f"Route references unknown named pipeline "
                        f"'{r.pipeline_ref}'"
                    ),
                    location=f"routes.{r.name or r.path}",
                )
            )

    # 15. Router-group validations ----------------------------------------
    seen_router_names: set[str] = set()
    for rg in app.routers:
        rloc = f"routers.{rg.name or '<anonymous>'}"
        if not rg.name:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E041",
                    message="Router has no `name`",
                    location=rloc,
                )
            )
        elif rg.name in seen_router_names:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E041",
                    message=f"Duplicate router name '{rg.name}'",
                    location=rloc,
                )
            )
        seen_router_names.add(rg.name)

        if not rg.routes:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E040",
                    message=f"Router '{rg.name}' declares no routes",
                    location=rloc,
                )
            )

        if rg.prefix and not rg.prefix.startswith("/"):
            errors.append(
                CompilerError(
                    severity="error",
                    code="E042",
                    message=(
                        f"Router '{rg.name}' prefix '{rg.prefix}' must start "
                        f"with '/'"
                    ),
                    location=rloc,
                )
            )

    pipeline_index = {p.name: p for p in app.named_pipelines}
    for p in app.named_pipelines:
        loc = f"pipelines.{p.name}"
        if p.extends and p.extends not in named_pipeline_names:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E022",
                    message=(
                        f"Pipeline '{p.name}' extends unknown pipeline "
                        f"'{p.extends}'"
                    ),
                    location=loc,
                )
            )

        # E023 fires only on conflicts within the user-authored sections
        # of a single file (prepend + steps + append + override values).
        # Conflicts that arise from re-binding an inherited step's name
        # are intentional and are treated as implicit overrides — the
        # later binding wins at codegen time.
        authored = (
            list(p.prepend) + list(p.steps) + list(p.append)
            + list(p.override.values())
        )
        seen_as_names: set[str] = set()
        for step_idx, step in enumerate(authored):
            name = getattr(step, "as_name", None)
            if name and name in seen_as_names:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E023",
                        message=(
                            f"Duplicate step as_name '{name}' in pipeline "
                            f"'{p.name}'"
                        ),
                        location=f"{loc}.steps[{step_idx}]",
                    )
                )
            if name:
                seen_as_names.add(name)

    # 16. Tier A feature validations (security, background tasks, files, ...)
    _validate_tier_a_features(app, errors)

    # 17. Tier B+C feature validations (websockets, health, cache, mounts, ...)
    _validate_tier_bc_features(app, errors)

    return errors


_VALID_COMPARE_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "in", "not_in"}
_VALID_BOOL_OPS = {"and", "or"}
_VALID_UNARY_OPS = {"not"}
_VALID_NULLARY_OPS = {"is_null", "is_not_null"}
_ALL_VALID_OPS = (
    _VALID_COMPARE_OPS | _VALID_BOOL_OPS | _VALID_UNARY_OPS | _VALID_NULLARY_OPS
)

_DB_ACTIONS = frozenset(
    {
        ActionKind.DB_LIST,
        ActionKind.DB_GET,
        ActionKind.DB_CREATE,
        ActionKind.DB_UPDATE,
        ActionKind.DB_DELETE,
    }
)


def _validate_pipeline_sequence(
    steps: list,
    loc: str,
    scope: Scope,
    errors: list[CompilerError],
) -> None:
    """Recursively walk a pipeline sequence, validating ``$ref`` resolution.

    Frames are pushed/popped around ``if`` branches and ``for_each`` bodies
    so that E035 fires when a binding from a dead frame is referenced.

    Sig: 2026-04-24 created
    """
    for step_idx, step in enumerate(steps):
        step_loc = f"{loc}[{step_idx}]"

        if isinstance(step, PipelineStepNode):
            for arg_key, arg_val in step.args.items():
                if isinstance(arg_val, str) and arg_val.startswith("$"):
                    if not scope.resolves(arg_val):
                        head = arg_val[1:].split(".", 1)[0]
                        errors.append(
                            CompilerError(
                                severity="error",
                                code="E010",
                                message=(
                                    f"Pipeline arg '{arg_key}' references "
                                    f"'${head}' which is not bound in scope"
                                ),
                                location=step_loc,
                            )
                        )
            if step.as_name:
                scope.bind(step.as_name)

        elif isinstance(step, IfStepNode):
            _validate_condition(step.condition, step_loc, scope, errors)
            scope.push()
            _validate_pipeline_sequence(
                step.then_steps, f"{step_loc}.then", scope, errors
            )
            then_bound = scope.pop()
            scope.push()
            _validate_pipeline_sequence(
                step.else_steps, f"{step_loc}.else", scope, errors
            )
            else_bound = scope.pop()
            scope.promote(then_bound & else_bound)
            if step.as_name:
                scope.bind(step.as_name)

        elif isinstance(step, ForEachStepNode):
            if step.iterable_ref and not scope.resolves(step.iterable_ref):
                head = step.iterable_ref[1:].split(".", 1)[0]
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E034",
                        message=(
                            f"for_each `in: {step.iterable_ref}` references "
                            f"unbound name '${head}'"
                        ),
                        location=step_loc,
                    )
                )
            if scope.has(step.loop_var):
                errors.append(
                    CompilerError(
                        severity="warning",
                        code="W038",
                        message=(
                            f"for_each loop var '{step.loop_var}' shadows an "
                            f"outer binding"
                        ),
                        location=step_loc,
                    )
                )
            scope.push(initial={step.loop_var})
            _validate_pipeline_sequence(
                step.body, f"{step_loc}.body", scope, errors
            )
            scope.pop()
            if step.as_name:
                scope.bind(step.as_name)

        elif isinstance(step, ReturnStepNode):
            if step.condition is not None:
                _validate_condition(step.condition, step_loc, scope, errors)
            if step.value_ref and not scope.resolves(step.value_ref):
                head = step.value_ref[1:].split(".", 1)[0]
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E036",
                        message=(
                            f"return value `{step.value_ref}` references "
                            f"unbound name '${head}'"
                        ),
                        location=step_loc,
                    )
                )

        else:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E099",
                    message=f"Unknown pipeline step type: {type(step).__name__}",
                    location=step_loc,
                )
            )


def _validate_condition(
    cond: ConditionNode,
    loc: str,
    scope: Scope,
    errors: list[CompilerError],
) -> None:
    """Validate a condition tree: known op + every ``$ref`` resolvable.

    Sig: 2026-04-24 created
    """
    if cond.when_handler:
        for ref in cond.captures:
            if isinstance(ref, str) and ref.startswith("$") and not scope.resolves(ref):
                head = ref[1:].split(".", 1)[0]
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E031",
                        message=(
                            f"Condition capture '${head}' is not bound in scope"
                        ),
                        location=loc,
                    )
                )
        return

    if cond.op is None:
        errors.append(
            CompilerError(
                severity="error",
                code="E030",
                message="Condition is missing `op` or `when_handler`",
                location=loc,
            )
        )
        return

    if cond.op not in _ALL_VALID_OPS:
        errors.append(
            CompilerError(
                severity="error",
                code="E030",
                message=f"Unknown condition op: {cond.op!r}",
                location=loc,
            )
        )
        return

    if cond.op in _VALID_BOOL_OPS:
        for child in cond.children:
            _validate_condition(child, loc, scope, errors)
        return

    if cond.op in _VALID_UNARY_OPS:
        if not cond.children:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E030",
                    message="`not` condition requires one child",
                    location=loc,
                )
            )
            return
        _validate_condition(cond.children[0], loc, scope, errors)
        return

    # leaf: compare / nullary ops reference left (and maybe right)
    for value, side in ((cond.left, "left"), (cond.right, "right")):
        if cond.op in _VALID_NULLARY_OPS and side == "right":
            continue
        if isinstance(value, str) and value.startswith("$") and not scope.resolves(value):
            head = value[1:].split(".", 1)[0]
            errors.append(
                CompilerError(
                    severity="error",
                    code="E031",
                    message=(
                        f"Condition {side} `{value}` references unbound name "
                        f"'${head}'"
                    ),
                    location=loc,
                )
            )
        elif isinstance(value, list):
            for item in value:
                if (
                    isinstance(item, str)
                    and item.startswith("$")
                    and not scope.resolves(item)
                ):
                    head = item[1:].split(".", 1)[0]
                    errors.append(
                        CompilerError(
                            severity="error",
                            code="E031",
                            message=(
                                f"Condition {side} list item `{item}` references "
                                f"unbound name '${head}'"
                            ),
                            location=loc,
                        )
                    )


def _validate_handler_presence(
    steps: list,
    loc: str,
    errors: list[CompilerError],
) -> None:
    """Walk a pipeline sequence verifying non-db actions carry a handler.

    Recurses into ``if.then``, ``if.else``, and ``for_each.body``.

    Sig: 2026-04-24 created
    """
    for idx, step in enumerate(steps):
        step_loc = f"{loc}[{idx}]"
        if isinstance(step, PipelineStepNode):
            if step.action not in _DB_ACTIONS and not step.handler:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E013",
                        message=(
                            f"Pipeline step action '{step.action.value}' "
                            f"requires a handler"
                        ),
                        location=step_loc,
                    )
                )
        elif isinstance(step, IfStepNode):
            _validate_handler_presence(step.then_steps, f"{step_loc}.then", errors)
            _validate_handler_presence(step.else_steps, f"{step_loc}.else", errors)
        elif isinstance(step, ForEachStepNode):
            _validate_handler_presence(step.body, f"{step_loc}.body", errors)
        # ReturnStepNode has no handler requirement


def _flatten_for_validate(
    p: NamedPipelineNode,
    index: dict[str, NamedPipelineNode],
    visiting: set[str],
) -> list:
    """Flatten a named pipeline for validation purposes only.

    Mirrors the resolve-time flatten but is defensive against broken
    ``extends`` chains (unknown parents → parent contributes no steps;
    cycles → short-circuit with an empty parent).

    Sig: 2026-04-24 created
    """
    if p.name in visiting:
        return list(p.steps)
    visiting.add(p.name)
    parent_steps: list = []
    if p.extends and p.extends in index:
        parent_steps = _flatten_for_validate(index[p.extends], index, visiting)
    visiting.discard(p.name)

    consumed: set[str] = set()
    resolved_parent = []
    for step in parent_steps:
        if step.as_name and step.as_name in p.override:
            resolved_parent.append(p.override[step.as_name])
            consumed.add(step.as_name)
        else:
            resolved_parent.append(step)

    extras = [
        step for key, step in p.override.items() if key not in consumed
    ]
    return (
        list(p.prepend)
        + resolved_parent
        + extras
        + list(p.steps)
        + list(p.append)
    )


def _validate_field_type(
    ft: "FieldType",
    location: str,
    errors: list[CompilerError],
    known_models: set[str],
) -> None:
    """Recursively validate that a FieldType references valid scalars/models.

    Args:
        ft: The field type to validate.
        location: Dotted path for error reporting.
        errors: Accumulator for validation errors.
        known_models: Set of declared model names.

    Sig: 2026-04-14 created
    """
    if ft.scalar is not None:
        # ScalarType is an enum, so if it parsed correctly it's already valid.
        # But we double-check the value is in the enum just in case.
        if ft.scalar.value not in _VALID_SCALARS:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E006",
                    message=f"Unknown scalar type '{ft.scalar.value}'",
                    location=location,
                )
            )
    elif ft.ref is not None:
        if ft.ref not in known_models:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E006",
                    message=f"Field type references unknown model '{ft.ref}'",
                    location=location,
                )
            )
    elif ft.list_of is not None:
        _validate_field_type(ft.list_of, location, errors, known_models)
    elif ft.dict_of is not None:
        _validate_field_type(ft.dict_of[0], f"{location}.key", errors, known_models)
        _validate_field_type(ft.dict_of[1], f"{location}.value", errors, known_models)


# ---------------------------------------------------------------------------
# Tier A feature validations
# ---------------------------------------------------------------------------

_BODY_METHODS = frozenset(
    {HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH}
)

# Headers FastAPI/Starlette computes itself; setting them manually produces
# corrupt responses at runtime, so reject them at compile time.
_RESERVED_RESPONSE_HEADERS = frozenset({"content-type", "content-length"})

# Accepted prefixes for runtime `$ref` values visible to a route handler.
# Matches the same surface the codegen can resolve at render time.
_VALID_REF_PREFIXES = (
    "$path.",
    "$query.",
    "$header.",
    "$cookie.",
    "$body",
    "$request",
    "$response.",
    "$result",
    "$user.",
    "$auth.",
)

# RFC 6265 cookie-name grammar (token = 1*<any CHAR except CTLs or separators>).
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.0-9A-Z^_`a-z|~]+$")

# File size suffix: optional whitespace, integer, optional unit (b/kb/mb/gb).
_MAX_SIZE_RE = re.compile(r"^\s*(\d+)\s*(b|kb|mb|gb)?\s*$", re.IGNORECASE)


def _validate_tier_a_features(
    app: AppNode, errors: list[CompilerError]
) -> None:
    """Validate Tier A feature nodes on routes and app-level security schemes.

    Checks:
        E050 — ``route.auth`` references a declared security scheme.
        E051 — Each security scheme has a non-empty, dotted ``verify_handler``.
        E052 — Each background task ``handler`` is in ``module.function`` form.
        E053 — ``file_params`` only appear on POST/PUT/PATCH routes.
        E054 — ``RouteParamNode`` with ``source=FORM`` only on POST/PUT/PATCH.
        E055 — ``response_type`` FILE or STREAMING requires a handler (not a
            db.* action).
        E056 — Each ``DependencyRef.handler`` is in ``module.function`` form.
        E057 — Cookie keys are non-empty.
        E058 — Each ``RouteParamNode`` declares a non-empty ``name``.
        E059 — ``FileParamNode.max_size``, if set, parses to a positive byte
            count (e.g. ``"10mb"``).
        E060 — ``ResponseHeaderNode.name`` is not a FastAPI-reserved header
            (``content-type``, ``content-length``).
        E061 — ``CookieNode.key`` is a valid RFC 6265 cookie-name token.
        E062 — Background task ``args`` values that are ``$`` references use a
            recognized prefix (``$path.``, ``$query.``, ``$body``, ...).

    Args:
        app: The parsed AppNode IR tree.
        errors: Accumulator for validation errors.

    Sig: 2026-05-07 modified
    """
    # E051 — security scheme verify_handler shape
    for scheme in app.security_schemes:
        if not scheme.verify_handler or "." not in scheme.verify_handler:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E051",
                    message=(
                        f"Security scheme '{scheme.name}' has invalid "
                        f"verify_handler"
                    ),
                    location=f"security_schemes.{scheme.name}",
                )
            )

    for r in app.routes:
        loc = r.path

        # E050 — route.auth references a declared security scheme
        if r.auth and r.auth not in app.security_index:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E050",
                    message=(
                        f"Unknown security scheme '{r.auth}' on route {r.path}"
                    ),
                    location=loc,
                )
            )

        # E052 — background task handler shape
        # E062 — background task args $refs use a recognized prefix
        for task in r.background_tasks:
            if "." not in task.handler:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E052",
                        message=(
                            f"Background task handler must be "
                            f"module.function format on route {r.path}"
                        ),
                        location=loc,
                    )
                )
            for arg_key, arg_val in task.args.items():
                if (
                    isinstance(arg_val, str)
                    and arg_val.startswith("$")
                    and not arg_val.startswith(_VALID_REF_PREFIXES)
                ):
                    errors.append(
                        CompilerError(
                            severity="error",
                            code="E062",
                            message=(
                                f"Background task arg '{arg_key}' has "
                                f"unresolvable $ref '{arg_val}' on route "
                                f"{r.path}"
                            ),
                            location=loc,
                        )
                    )

        # E053 — file uploads only on body-bearing methods
        if r.file_params and r.method not in _BODY_METHODS:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E053",
                    message=(
                        f"File upload not allowed on {r.method.value} "
                        f"route {r.path}"
                    ),
                    location=loc,
                )
            )

        # E059 — file_param max_size, when set, must parse to a positive int
        for fp in r.file_params:
            if fp.max_size is None:
                continue
            match = _MAX_SIZE_RE.match(fp.max_size)
            if match is None or int(match.group(1)) <= 0:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E059",
                        message=(
                            f"File param '{fp.name}' has invalid max_size "
                            f"'{fp.max_size}' on route {r.path}"
                        ),
                        location=loc,
                    )
                )

        # E060 — response headers must not collide with reserved FastAPI headers
        for header in r.response_headers:
            if header.name.lower() in _RESERVED_RESPONSE_HEADERS:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E060",
                        message=(
                            f"Response header '{header.name}' is reserved "
                            f"and cannot be set manually on route {r.path}"
                        ),
                        location=loc,
                    )
                )

        # E054 — form params only on body-bearing methods
        if r.method not in _BODY_METHODS:
            for p in r.params:
                if p.source is ParamSource.FORM:
                    errors.append(
                        CompilerError(
                            severity="error",
                            code="E054",
                            message=(
                                f"Form parameters not allowed on "
                                f"{r.method.value} route {r.path}"
                            ),
                            location=loc,
                        )
                    )
                    break

        # E055 — file/streaming responses require a handler
        if (
            r.response_type in (ResponseKind.FILE, ResponseKind.STREAMING)
            and not r.handler
        ):
            errors.append(
                CompilerError(
                    severity="error",
                    code="E055",
                    message=(
                        f"response_type '{r.response_type.value}' requires "
                        f"a handler on route {r.path}"
                    ),
                    location=loc,
                )
            )

        # E056 — dependency handler shape
        for dep in r.depends:
            if "." not in dep.handler:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E056",
                        message=(
                            f"Dependency handler must be module.function "
                            f"format on route {r.path}"
                        ),
                        location=loc,
                    )
                )

        # E057 — cookie keys non-empty
        # E061 — non-empty cookie keys must be valid RFC 6265 tokens
        for cookie in r.cookies:
            if not cookie.key:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E057",
                        message=f"Empty cookie key on route {r.path}",
                        location=loc,
                    )
                )
            elif not _COOKIE_NAME_RE.match(cookie.key):
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E061",
                        message=(
                            f"Invalid cookie name '{cookie.key}' on route "
                            f"{r.path}"
                        ),
                        location=loc,
                    )
                )

        # E058 — RouteParamNode name required
        for p in r.params:
            if not p.name:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E058",
                        message=f"Param name required on route {r.path}",
                        location=loc,
                    )
                )


# ---------------------------------------------------------------------------
# Tier B + C feature validations
# ---------------------------------------------------------------------------

# HTTP methods for which response caching is semantically meaningful.
_CACHEABLE_METHODS = frozenset({HttpMethod.GET})

# rate_limit.rate must look like "10/minute" or "100/hour".
_RATE_LIMIT_RE = re.compile(r"^\d+/(second|minute|hour|day)$")

# operation_id must be a valid Python-ish identifier.
_OPERATION_ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_tier_bc_features(
    app: AppNode, errors: list[CompilerError]
) -> None:
    """Validate Tier B+C feature nodes on routes and app-level config.

    Checks:
        E070 — WebSocket route declares a non-empty ``handler``.
        E071 — WebSocket ``on_connect``/``on_disconnect``, when set, are
            dotted ``module.function`` references.
        E072 — ``response_type=STREAMING`` requires a handler on the route.
        E073 — ``health_check.path`` and ``ready_path`` start with ``/``.
        E074 — Each custom health-check handler is in dotted form.
        E075 — ``cache.max_age`` is non-negative.
        E076 — ``cache`` only valid on GET/HEAD routes.
        E077 — ``response_type=REDIRECT`` requires a handler on the route.
        E078 — ``rate_limit.rate`` matches ``N/(second|minute|hour|day)``.
        E079 — Custom middleware carries either a ``handler`` or a
            ``class_path`` in its config.
        E080 — Custom middleware ``handler``/``class_path`` is dotted.
        E081 — ``openapi_extras.operation_id``, when set, is a valid
            identifier.
        E082 — Each ``MountConfig.path`` starts with ``/``.
        E083 — Each ``MountConfig.app_module`` is a dotted reference.

    Args:
        app: The parsed AppNode IR tree.
        errors: Accumulator for validation errors.

    Sig: 2026-05-07 created
    """
    # -- WebSocket routes -------------------------------------------------
    for ws in app.websocket_routes:
        if not isinstance(ws, WebSocketRouteNode):
            continue
        loc = f"websocket_routes.{ws.name or ws.path}"

        # E070 — handler required
        if not ws.handler:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E070",
                    message=f"WebSocket route '{ws.path}' must have a handler",
                    location=loc,
                )
            )

        # E071 — on_connect / on_disconnect dotted
        for attr_name in ("on_connect", "on_disconnect"):
            val = getattr(ws, attr_name)
            if val and "." not in val:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E071",
                        message=(
                            f"WebSocket {attr_name} must be module.function "
                            f"format on route '{ws.path}'"
                        ),
                        location=loc,
                    )
                )

    # -- Per-route Tier B+C checks ---------------------------------------
    for r in app.routes:
        loc = r.path

        # E072 — streaming response requires a handler
        if (
            isinstance(r.streaming, StreamingConfig)
            or r.response_type is ResponseKind.STREAMING
        ) and not r.handler:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E072",
                    message=(
                        f"Streaming response requires handler on route {r.path}"
                    ),
                    location=loc,
                )
            )

        # E077 — redirect response requires a handler
        if r.response_type is ResponseKind.REDIRECT and not r.handler:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E077",
                    message=(
                        f"Redirect response requires handler on route {r.path}"
                    ),
                    location=loc,
                )
            )

        # E075 / E076 — cache validity
        if isinstance(r.cache, CacheConfig):
            if r.cache.max_age < 0:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E075",
                        message=(
                            f"Cache max_age must be non-negative on route "
                            f"{r.path}"
                        ),
                        location=loc,
                    )
                )
            if r.method not in _CACHEABLE_METHODS:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E076",
                        message=f"Cache only valid on GET routes: {r.path}",
                        location=loc,
                    )
                )

        # E078 — rate limit format
        if (
            isinstance(r.rate_limit, RateLimitConfig)
            and r.rate_limit.rate
            and not _RATE_LIMIT_RE.match(r.rate_limit.rate)
        ):
            errors.append(
                CompilerError(
                    severity="error",
                    code="E078",
                    message=f"Invalid rate limit format on route {r.path}",
                    location=loc,
                )
            )

        # E081 — operation_id identifier shape
        if (
            isinstance(r.openapi_extras, OpenAPIExtras)
            and r.openapi_extras.operation_id
            and not _OPERATION_ID_RE.match(r.openapi_extras.operation_id)
        ):
            errors.append(
                CompilerError(
                    severity="error",
                    code="E081",
                    message=(
                        f"Invalid operation_id "
                        f"'{r.openapi_extras.operation_id}' on route {r.path}"
                    ),
                    location=loc,
                )
            )

    # -- Health check config ---------------------------------------------
    hc = app.health_check
    if isinstance(hc, HealthCheckConfig):
        hc_loc = "health_check"
        for path_attr, label in (("path", "path"), ("ready_path", "ready_path")):
            value = getattr(hc, path_attr, "")
            if value and not value.startswith("/"):
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E073",
                        message="Health check path must start with /",
                        location=f"{hc_loc}.{label}",
                    )
                )

        for idx, check in enumerate(hc.custom_checks):
            if "." not in check:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E074",
                        message=(
                            f"Health check handler '{check}' must be "
                            f"module.function format"
                        ),
                        location=f"{hc_loc}.custom_checks[{idx}]",
                    )
                )

    # -- Custom middleware (app + router level) --------------------------
    middleware_sources: list[tuple[str, list]] = [
        ("middleware", list(app.middleware)),
    ]
    for rg in app.routers:
        middleware_sources.append(
            (f"routers.{rg.name or '<anonymous>'}.middleware", list(rg.middleware))
        )

    for owner_loc, mws in middleware_sources:
        for idx, mw in enumerate(mws):
            if getattr(mw, "kind", "") != "custom":
                continue
            mw_loc = f"{owner_loc}[{idx}]"
            cfg = getattr(mw, "config", {}) or {}
            handler = cfg.get("handler")
            class_path = cfg.get("class_path")

            # E079 — must have at least one of handler / class_path
            if not handler and not class_path:
                errors.append(
                    CompilerError(
                        severity="error",
                        code="E079",
                        message=(
                            "Custom middleware must define 'handler' or "
                            "'class_path' in config"
                        ),
                        location=mw_loc,
                    )
                )
                continue

            # E080 — handler / class_path must be dotted
            for key, val in (("handler", handler), ("class_path", class_path)):
                if val and "." not in val:
                    errors.append(
                        CompilerError(
                            severity="error",
                            code="E080",
                            message=(
                                f"Custom middleware {key} '{val}' must be "
                                f"module.function format"
                            ),
                            location=mw_loc,
                        )
                    )

    # -- Mounts -----------------------------------------------------------
    for idx, mount in enumerate(app.mounts):
        if not isinstance(mount, MountConfig):
            continue
        mloc = f"mounts[{idx}]"
        if mount.path and not mount.path.startswith("/"):
            errors.append(
                CompilerError(
                    severity="error",
                    code="E082",
                    message=f"Mount path '{mount.path}' must start with /",
                    location=mloc,
                )
            )
        if mount.app_module and "." not in mount.app_module:
            errors.append(
                CompilerError(
                    severity="error",
                    code="E083",
                    message=(
                        f"Mount app_module '{mount.app_module}' must be "
                        f"module.attribute format"
                    ),
                    location=mloc,
                )
            )
