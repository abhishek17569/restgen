"""Emit pipeline orchestration code from pipeline step trees.

Supports the four step-node types:

* ``PipelineStepNode`` — CRUD / handler step (legacy + Tier 2).
* ``IfStepNode`` — conditional with then/else branches.
* ``ForEachStepNode`` — iteration over a bound sequence.
* ``ReturnStepNode`` — early return with optional guard and status override.

Dispatch is ``isinstance``-based with an exhaustive ``else`` that raises,
so a new step type cannot silently fall through to wrong codegen.

Sig: 2026-04-24 modified
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from restgen.ir.nodes import (
    ActionKind,
    AppNode,
    ConditionNode,
    ErrorRef,
    ForEachStepNode,
    IfStepNode,
    PipelineStepNode,
    ReturnStepNode,
    RouteNode,
)
from restgen.codegen.ast_builder import (
    make_assign,
    make_await,
    make_constant,
    make_if_none_raise_404,
    make_if_none_raise_named,
    make_name,
    make_return,
)
from restgen.codegen.condition_emitter import lower_condition
from restgen.scope import Scope


# ---------------------------------------------------------------------------
# Lowering context
# ---------------------------------------------------------------------------

@dataclass
class _Ctx:
    """Threaded context for step lowering.

    Args:
        route: Route owning the current pipeline (for error refs).
        app: Application IR.
        scope: Lexical scope of ``$ref`` bindings.
        extra_imports: Accumulator for (module, name) pairs.

    Sig: 2026-04-24 created
    """

    route: RouteNode
    app: AppNode
    scope: Scope
    extra_imports: list[tuple[str, str]]


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------

def _resolve_ref(ref) -> ast.expr:
    """Resolve a ``$ref`` (or literal) to an AST expression.

    Handles ``$path.X``, ``$body``, ``$query.X``, ``$header.X``, ``$request``,
    and plain step-name references. Dotted refs (``$user.email``) emit an
    attribute chain.

    Sig: 2026-04-24 modified
    """
    if not isinstance(ref, str):
        return make_constant(ref)
    if not ref.startswith("$"):
        return make_constant(ref)

    stripped = ref[1:]

    if stripped.startswith("path."):
        return _attr_chain(stripped[5:].split("."))
    if stripped == "body":
        return make_name("body")
    if stripped.startswith("body."):
        return _attr_chain(["body"] + stripped[5:].split("."))
    if stripped.startswith("query."):
        return _attr_chain(stripped[6:].split("."))
    if stripped.startswith("header."):
        return _attr_chain(stripped[7:].split("."))
    if stripped == "request":
        return make_name("request")
    return _attr_chain(stripped.split("."))


def _attr_chain(parts: list[str]) -> ast.expr:
    """Build ``a.b.c`` attribute expression from a list of identifiers.

    Sig: 2026-04-24 created
    """
    parts = [p for p in parts if p]
    if not parts:
        return make_constant(None)
    expr: ast.expr = make_name(parts[0])
    for part in parts[1:]:
        expr = ast.Attribute(value=expr, attr=part, ctx=ast.Load())
    return expr


def _resolve_args(args: dict) -> list[ast.keyword]:
    """Resolve an args dict to ``ast.keyword`` nodes.

    Sig: 2026-04-24 modified
    """
    return [
        ast.keyword(arg=key, value=_resolve_ref(value))
        for key, value in args.items()
    ]


# ---------------------------------------------------------------------------
# Action-step lowering (existing behavior, kept verbatim where unchanged)
# ---------------------------------------------------------------------------

def _find_route_error(route: RouteNode | None, condition: str) -> ErrorRef | None:
    """Sig: 2026-04-15 created"""
    if route is None:
        return None
    for err in route.errors:
        if err.condition == condition:
            return err
    return None


def _error_class_name(error_name: str) -> str:
    """Sig: 2026-04-15 created"""
    if "_" not in error_name and error_name[0:1].isupper():
        pascal = error_name
    else:
        pascal = "".join(word.capitalize() for word in error_name.split("_"))
    if not pascal.endswith("Error"):
        pascal += "Error"
    return pascal


def _lower_db_get(step: PipelineStepNode, ctx: _Ctx) -> list[ast.stmt]:
    """Sig: 2026-04-24 modified"""
    model = step.model or "Model"
    if step.args:
        first_val = next(iter(step.args.values()))
        arg_expr = _resolve_ref(first_val)
    else:
        arg_expr = make_name("id")

    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="get", ctx=ast.Load()),
        args=[make_name(model), arg_expr],
        keywords=[],
    )
    var_name = step.as_name or "result"

    not_found_ref = _find_route_error(ctx.route, "not_found")
    if not_found_ref and not_found_ref.ref:
        none_check = make_if_none_raise_named(var_name, _error_class_name(not_found_ref.ref))
    else:
        none_check = make_if_none_raise_404(var_name)

    return [make_assign(var_name, make_await(call)), none_check]


def _lower_db_create(step: PipelineStepNode) -> list[ast.stmt]:
    """Sig: 2026-04-14 created"""
    model = step.model or "Model"
    if step.args:
        first_val = next(iter(step.args.values()))
        arg_expr = _resolve_ref(first_val)
    else:
        arg_expr = make_name("body")
    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="create", ctx=ast.Load()),
        args=[make_name(model), arg_expr],
        keywords=[],
    )
    var_name = step.as_name or "result"
    return [make_assign(var_name, make_await(call))]


def _lower_db_update(step: PipelineStepNode) -> list[ast.stmt]:
    """Sig: 2026-04-14 created"""
    model = step.model or "Model"
    resolved = _resolve_args(step.args)
    id_expr = make_name("id")
    data_expr = make_name("body")
    for kw in resolved:
        if kw.arg == "id":
            id_expr = kw.value
        elif kw.arg in ("data", "body"):
            data_expr = kw.value
    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="update", ctx=ast.Load()),
        args=[make_name(model), id_expr, data_expr],
        keywords=[],
    )
    var_name = step.as_name or "result"
    return [make_assign(var_name, make_await(call))]


def _lower_db_delete(step: PipelineStepNode) -> list[ast.stmt]:
    """Sig: 2026-04-14 created"""
    model = step.model or "Model"
    if step.args:
        first_val = next(iter(step.args.values()))
        arg_expr = _resolve_ref(first_val)
    else:
        arg_expr = make_name("id")
    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="delete", ctx=ast.Load()),
        args=[make_name(model), arg_expr],
        keywords=[],
    )
    return [ast.Expr(value=make_await(call))]


def _lower_db_list(step: PipelineStepNode) -> list[ast.stmt]:
    """Sig: 2026-04-14 created"""
    model = step.model or "Model"
    keywords = _resolve_args(step.args)
    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="list", ctx=ast.Load()),
        args=[make_name(model)],
        keywords=keywords,
    )
    var_name = step.as_name or "result"
    return [make_assign(var_name, make_await(call))]


def _lower_handler_step(
    step: PipelineStepNode,
    ctx: _Ctx,
    *,
    assign: bool = True,
) -> list[ast.stmt]:
    """Sig: 2026-04-24 modified"""
    handler = step.handler or ""
    parts = handler.rsplit(".", 1)
    if len(parts) == 2:
        module, func_name = parts
        ctx.extra_imports.append((module, func_name))
    else:
        func_name = handler

    keywords = _resolve_args(step.args)
    call = ast.Call(func=make_name(func_name), args=[], keywords=keywords)

    if assign and step.as_name:
        return [make_assign(step.as_name, make_await(call))]
    if assign and step.action != ActionKind.SIDE_EFFECT:
        return [make_assign("result", make_await(call))]
    return [ast.Expr(value=make_await(call))]


def _lower_action_step(step: PipelineStepNode, ctx: _Ctx) -> tuple[list[ast.stmt], str | None]:
    """Dispatch a legacy/Tier-2 action or handler step.

    Returns:
        ``(statements, last_var_name | None)``. The caller uses
        ``last_var_name`` to compute the synthetic trailing return.

    Sig: 2026-04-24 created
    """
    if step.action == ActionKind.DB_GET:
        return _lower_db_get(step, ctx), step.as_name or "result"
    if step.action == ActionKind.DB_CREATE:
        return _lower_db_create(step), step.as_name or "result"
    if step.action == ActionKind.DB_UPDATE:
        return _lower_db_update(step), step.as_name or "result"
    if step.action == ActionKind.DB_DELETE:
        return _lower_db_delete(step), None
    if step.action == ActionKind.DB_LIST:
        return _lower_db_list(step), step.as_name or "result"
    if step.action in (ActionKind.VALIDATE, ActionKind.TRANSFORM):
        stmts = _lower_handler_step(step, ctx, assign=True)
        return stmts, step.as_name or "result"
    if step.action == ActionKind.SIDE_EFFECT:
        stmts = _lower_handler_step(step, ctx, assign=bool(step.as_name))
        return stmts, step.as_name if step.as_name else None
    return [ast.Expr(value=make_constant(...))], None


# ---------------------------------------------------------------------------
# Control-flow step lowering
# ---------------------------------------------------------------------------

def _lower_if_step(step: IfStepNode, ctx: _Ctx) -> tuple[list[ast.stmt], str | None]:
    """Lower an ``if`` step to ``ast.If``. Scope rules: each branch pushes
    its own frame; names bound in **both** are promoted to the parent.

    Sig: 2026-04-24 created
    """
    test_expr, cond_imports = lower_condition(step.condition, _resolve_ref)
    ctx.extra_imports.extend(cond_imports)

    # Then branch
    ctx.scope.push()
    then_stmts, then_last = _lower_sequence(step.then_steps, ctx)
    then_bindings = ctx.scope.pop()

    # Else branch
    ctx.scope.push()
    else_stmts, else_last = _lower_sequence(step.else_steps, ctx)
    else_bindings = ctx.scope.pop()

    # Promote intersection
    ctx.scope.promote(then_bindings & else_bindings)

    if_node = ast.If(
        test=test_expr,
        body=then_stmts or [ast.Pass()],
        orelse=else_stmts,
    )
    # last_var when both branches end in the same binding is promotable
    last = step.as_name if step.as_name else (
        then_last if then_last and then_last == else_last else None
    )
    return [if_node], last


def _lower_for_each_step(step: ForEachStepNode, ctx: _Ctx) -> tuple[list[ast.stmt], str | None]:
    """Lower a ``for_each`` step to ``ast.For``. Loop var is bound in a
    fresh frame; inner ``as_name`` bindings die at loop exit.

    When ``step.as_name`` (collect) is set, the body's terminal ``as:``
    value is accumulated into a list.

    Sig: 2026-04-24 created
    """
    iter_expr = _resolve_ref(step.iterable_ref)

    prelude: list[ast.stmt] = []
    accum_name = step.as_name
    if accum_name:
        prelude.append(make_assign(accum_name, ast.List(elts=[], ctx=ast.Load())))
        ctx.scope.bind(accum_name)

    ctx.scope.push(initial={step.loop_var})
    body_stmts, body_last = _lower_sequence(step.body, ctx)
    ctx.scope.pop()

    if accum_name and body_last:
        body_stmts = list(body_stmts) + [
            ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(
                        value=make_name(accum_name),
                        attr="append",
                        ctx=ast.Load(),
                    ),
                    args=[make_name(body_last)],
                    keywords=[],
                )
            )
        ]

    for_node = ast.For(
        target=ast.Name(id=step.loop_var, ctx=ast.Store()),
        iter=iter_expr,
        body=body_stmts or [ast.Pass()],
        orelse=[],
    )
    return prelude + [for_node], accum_name


def _lower_return_step(step: ReturnStepNode, ctx: _Ctx) -> tuple[list[ast.stmt], str | None, bool]:
    """Lower a ``return`` step.

    Returns:
        ``(statements, last_var_name, unconditional_return)``. The third
        element lets the outer loop suppress the synthetic trailing return
        when the pipeline ends in an unconditional ``return``.

    Sig: 2026-04-24 created
    """
    if step.status is not None:
        value_expr: ast.expr
        if step.value_ref is None:
            value_expr = ast.Dict(keys=[], values=[])
        else:
            value_expr = _resolve_ref(step.value_ref)
        ctx.extra_imports.append(("fastapi.responses", "JSONResponse"))
        ret = ast.Return(
            value=ast.Call(
                func=make_name("JSONResponse"),
                args=[],
                keywords=[
                    ast.keyword(arg="content", value=value_expr),
                    ast.keyword(arg="status_code", value=make_constant(step.status)),
                ],
            )
        )
    elif step.value_ref is None:
        ret = ast.Return(value=None)
    else:
        ret = ast.Return(value=_resolve_ref(step.value_ref))

    if step.condition is not None:
        test_expr, cond_imports = lower_condition(step.condition, _resolve_ref)
        ctx.extra_imports.extend(cond_imports)
        return [ast.If(test=test_expr, body=[ret], orelse=[])], None, False

    return [ret], None, True


# ---------------------------------------------------------------------------
# Sequence lowering (exhaustive isinstance dispatch)
# ---------------------------------------------------------------------------

def _lower_sequence(
    steps: list,
    ctx: _Ctx,
) -> tuple[list[ast.stmt], str | None]:
    """Lower a sequence of pipeline steps under the current scope.

    Returns:
        ``(stmts, last_var_name | None)``. ``last_var_name`` tracks the
        most recent binding, used by the outer ``lower_pipeline`` to
        decide the synthetic trailing return.

    Sig: 2026-04-24 created
    """
    out: list[ast.stmt] = []
    last_var: str | None = None

    for step in steps:
        if isinstance(step, PipelineStepNode):
            stmts, last = _lower_action_step(step, ctx)
            if step.as_name:
                ctx.scope.bind(step.as_name)
            if last:
                last_var = last
        elif isinstance(step, IfStepNode):
            stmts, last = _lower_if_step(step, ctx)
            if step.as_name:
                ctx.scope.bind(step.as_name)
                last_var = step.as_name
            elif last:
                last_var = last
        elif isinstance(step, ForEachStepNode):
            stmts, last = _lower_for_each_step(step, ctx)
            if last:
                last_var = last
        elif isinstance(step, ReturnStepNode):
            stmts, _, _unconditional = _lower_return_step(step, ctx)
            out.extend(stmts)
            last_var = None
            continue
        else:
            raise AssertionError(f"Unknown pipeline step type: {type(step).__name__}")
        out.extend(stmts)

    return out, last_var


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lower_pipeline(
    pipeline: list,
    route: RouteNode,
    app: AppNode,
) -> tuple[list[ast.stmt], list[tuple[str, str]]]:
    """Generate pipeline body statements and the imports they require.

    Args:
        pipeline: The route's ordered step list (heterogeneous).
        route: The route that owns this pipeline.
        app: Application IR.

    Returns:
        ``(body_statements, extra_imports)``.

    Sig: 2026-04-24 modified
    """
    ctx = _Ctx(route=route, app=app, scope=Scope(), extra_imports=[])
    body, last_var = _lower_sequence(pipeline, ctx)

    # Suppress synthetic trailing return if the pipeline already ends in
    # an unconditional `return` at the top level. Otherwise, return the
    # last named binding (legacy behavior).
    if body and isinstance(body[-1], ast.Return):
        return body, ctx.extra_imports

    if last_var:
        body.append(make_return(make_name(last_var)))

    return body, ctx.extra_imports
