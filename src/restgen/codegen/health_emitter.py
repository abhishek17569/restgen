"""Emit health check endpoints from IR HealthCheckConfig.

Sig: 2026-05-07 created
"""
from __future__ import annotations

import ast

from restgen.ir.nodes import AppNode, HealthCheckConfig
from restgen.codegen.ast_builder import (
    make_name,
    make_constant,
    make_import_from,
    make_module,
    make_assign,
    make_async_func,
    make_return,
    make_await,
)


def _split_dotted(path: str) -> tuple[str, str]:
    """Split a dotted handler path into (module, name).

    Args:
        path: Dotted path like ``pkg.mod.func``.

    Returns:
        Tuple of (module, name). Raises if path has no dot.

    Sig: 2026-05-07 created
    """
    module, _, name = path.rpartition(".")
    if not module:
        raise ValueError(f"custom_checks entry must be a dotted path: {path!r}")
    return module, name


def _check_key(func_name: str) -> str:
    """Derive a dict-key name for a custom check from its function name.

    ``check_redis`` -> ``redis``; otherwise the function name itself.

    Args:
        func_name: The imported handler's attribute name.

    Returns:
        Key string used in the readiness response dict.

    Sig: 2026-05-07 created
    """
    if func_name.startswith("check_") and len(func_name) > len("check_"):
        return func_name[len("check_"):]
    return func_name


def _router_get_decorator(path: str, status_code: int | None = None) -> ast.Call:
    """Build ``@health_router.get(path, status_code=N)``.

    Args:
        path: URL path for the route.
        status_code: Optional HTTP status code to set on the decorator.

    Returns:
        An ast.Call suitable for use as a decorator.

    Sig: 2026-05-07 created
    """
    keywords: list[ast.keyword] = []
    if status_code is not None:
        keywords.append(ast.keyword(arg="status_code", value=make_constant(status_code)))
    return ast.Call(
        func=ast.Attribute(
            value=make_name("health_router"),
            attr="get",
            ctx=ast.Load(),
        ),
        args=[make_constant(path)],
        keywords=keywords,
    )


def _depends_call(dep_name: str) -> ast.Call:
    """Build ``Depends(dep_name)``.

    Args:
        dep_name: Name of the dependency callable.

    Returns:
        An ast.Call for ``Depends(...)``.

    Sig: 2026-05-07 created
    """
    return ast.Call(
        func=make_name("Depends"),
        args=[make_name(dep_name)],
        keywords=[],
    )


def _json_response_503(content_var: str) -> ast.Return:
    """Build ``return JSONResponse(status_code=503, content=<content_var>)``.

    Args:
        content_var: Name of the local dict variable to use as content.

    Returns:
        An ast.Return node.

    Sig: 2026-05-07 created
    """
    call = ast.Call(
        func=make_name("JSONResponse"),
        args=[],
        keywords=[
            ast.keyword(arg="status_code", value=make_constant(503)),
            ast.keyword(arg="content", value=make_name(content_var)),
        ],
    )
    return make_return(call)


def _dict_subscript_assign(var: str, key: str, value: str) -> ast.Assign:
    """Build ``<var>["<key>"] = "<value>"`` as an ast.Assign.

    Args:
        var: Target dict variable name.
        key: String key to set.
        value: String literal value to assign.

    Returns:
        An ast.Assign node.

    Sig: 2026-05-07 created
    """
    return ast.Assign(
        targets=[
            ast.Subscript(
                value=make_name(var),
                slice=make_constant(key),
                ctx=ast.Store(),
            )
        ],
        value=make_constant(value),
        lineno=0,
    )


def _build_health_func(path: str) -> ast.AsyncFunctionDef:
    """Build the liveness endpoint function.

    Generates::

        @health_router.get(path, status_code=200)
        async def health_check():
            return {"status": "healthy"}

    Args:
        path: The liveness endpoint path.

    Returns:
        An ast.AsyncFunctionDef for the liveness endpoint.

    Sig: 2026-05-07 created
    """
    body_return = make_return(
        ast.Dict(
            keys=[make_constant("status")],
            values=[make_constant("healthy")],
        )
    )
    return make_async_func(
        name="health_check",
        args=[],
        body=[body_return],
        decorators=[_router_get_decorator(path, status_code=200)],
    )


def _build_db_try(checks_var: str) -> ast.Try:
    """Build the DB health-check try/except block.

    Generates::

        try:
            await repo.health_check()
            checks["database"] = "connected"
        except Exception:
            return JSONResponse(status_code=503,
                                content={"status": "not_ready",
                                         "database": "disconnected"})

    Args:
        checks_var: Name of the checks dict variable.

    Returns:
        An ast.Try node.

    Sig: 2026-05-07 created
    """
    await_call = ast.Expr(
        value=make_await(
            ast.Call(
                func=ast.Attribute(
                    value=make_name("repo"),
                    attr="health_check",
                    ctx=ast.Load(),
                ),
                args=[],
                keywords=[],
            )
        )
    )
    set_connected = _dict_subscript_assign(checks_var, "database", "connected")

    failure_content = ast.Dict(
        keys=[make_constant("status"), make_constant("database")],
        values=[make_constant("not_ready"), make_constant("disconnected")],
    )
    failure_return = make_return(
        ast.Call(
            func=make_name("JSONResponse"),
            args=[],
            keywords=[
                ast.keyword(arg="status_code", value=make_constant(503)),
                ast.keyword(arg="content", value=failure_content),
            ],
        )
    )

    handler = ast.ExceptHandler(
        type=make_name("Exception"),
        name=None,
        body=[failure_return],
    )
    return ast.Try(
        body=[await_call, set_connected],
        handlers=[handler],
        orelse=[],
        finalbody=[],
    )


def _build_custom_check_try(func_name: str, checks_var: str) -> ast.Try:
    """Build a try/except block for a custom async check function.

    Generates::

        try:
            await <func_name>()
            checks["<key>"] = "connected"
        except Exception:
            checks["<key>"] = "disconnected"
            return JSONResponse(status_code=503, content=checks)

    Args:
        func_name: Local name of the imported check function.
        checks_var: Name of the checks dict variable.

    Returns:
        An ast.Try node.

    Sig: 2026-05-07 created
    """
    key = _check_key(func_name)

    await_call = ast.Expr(
        value=make_await(
            ast.Call(func=make_name(func_name), args=[], keywords=[]),
        )
    )
    set_connected = _dict_subscript_assign(checks_var, key, "connected")
    set_disconnected = _dict_subscript_assign(checks_var, key, "disconnected")

    handler = ast.ExceptHandler(
        type=make_name("Exception"),
        name=None,
        body=[set_disconnected, _json_response_503(checks_var)],
    )
    return ast.Try(
        body=[await_call, set_connected],
        handlers=[handler],
        orelse=[],
        finalbody=[],
    )


def _build_ready_func(cfg: HealthCheckConfig, custom_local_names: list[str]) -> ast.AsyncFunctionDef:
    """Build the readiness endpoint function.

    Args:
        cfg: Health check configuration from the IR.
        custom_local_names: Local names of imported custom check functions,
            in the order they should be invoked.

    Returns:
        An ast.AsyncFunctionDef for the readiness endpoint.

    Sig: 2026-05-07 created
    """
    checks_var = "checks"
    body: list[ast.stmt] = []

    # checks = {"status": "ready"}
    body.append(
        make_assign(
            checks_var,
            ast.Dict(
                keys=[make_constant("status")],
                values=[make_constant("ready")],
            ),
        )
    )

    if cfg.include_db:
        body.append(_build_db_try(checks_var))

    for func_name in custom_local_names:
        body.append(_build_custom_check_try(func_name, checks_var))

    body.append(make_return(make_name(checks_var)))

    args: list[tuple[str, ast.expr | None, ast.expr | None]] = []
    if cfg.include_db:
        args.append(("repo", None, _depends_call("get_repository")))

    return make_async_func(
        name="readiness_check",
        args=args,
        body=body,
        decorators=[_router_get_decorator(cfg.ready_path, status_code=200)],
    )


def lower_health(app: AppNode) -> ast.Module | None:
    """Generate a health.py AST module from an AppNode's health check config.

    Emits a ``health_router`` APIRouter plus liveness and readiness endpoints.
    Returns ``None`` when health checks are absent or disabled.

    Args:
        app: The root IR application node.

    Returns:
        An ast.Module for the generated health module, or None if disabled.

    Sig: 2026-05-07 created
    """
    cfg = app.health_check
    if cfg is None or not cfg.enabled:
        return None

    body: list[ast.stmt] = []

    # Imports
    fastapi_names = ["APIRouter"]
    if cfg.include_db:
        fastapi_names.append("Depends")
    body.append(make_import_from("fastapi", fastapi_names))
    body.append(make_import_from("fastapi.responses", ["JSONResponse"]))
    if cfg.include_db:
        body.append(make_import_from(".dependencies", ["get_repository"]))

    # Group custom-check imports by module to avoid duplicate ImportFrom lines
    # while preserving the order of first appearance.
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    local_names: list[str] = []
    for dotted in cfg.custom_checks:
        module, name = _split_dotted(dotted)
        if module not in grouped:
            grouped[module] = []
            order.append(module)
        if name not in grouped[module]:
            grouped[module].append(name)
        local_names.append(name)
    for module in order:
        body.append(make_import_from(module, grouped[module]))

    # health_router = APIRouter(tags=["health"])
    body.append(
        make_assign(
            "health_router",
            ast.Call(
                func=make_name("APIRouter"),
                args=[],
                keywords=[
                    ast.keyword(
                        arg="tags",
                        value=ast.List(
                            elts=[make_constant("health")],
                            ctx=ast.Load(),
                        ),
                    )
                ],
            ),
        )
    )

    # Endpoint functions
    body.append(_build_health_func(cfg.path))
    body.append(_build_ready_func(cfg, local_names))

    return make_module(body)
