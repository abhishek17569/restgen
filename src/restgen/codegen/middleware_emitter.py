"""Emit middleware registration from IR MiddlewareNodes.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import ast
from typing import Any

from restgen.ir.nodes import AppNode, MiddlewareNode
from restgen.codegen.ast_builder import (
    make_name,
    make_constant,
    make_import_from,
    make_module,
)


# ---------------------------------------------------------------------------
# Known middleware mappings
# ---------------------------------------------------------------------------

_MIDDLEWARE_MAP: dict[str, tuple[str, str]] = {
    "cors": ("fastapi.middleware.cors", "CORSMiddleware"),
    "trustedhost": ("fastapi.middleware.trustedhost", "TrustedHostMiddleware"),
    "gzip": ("fastapi.middleware.gzip", "GZipMiddleware"),
    "rate_limit": ("slowapi", "Limiter"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_cors_call(mw: MiddlewareNode) -> ast.Expr:
    """Build ``app.add_middleware(CORSMiddleware, ...)`` statement.

    Args:
        mw: The CORS middleware node.

    Returns:
        An ast.Expr wrapping the add_middleware call.

    Sig: 2026-04-14 created
    """
    config = mw.config
    keywords: list[ast.keyword] = []

    if "origins" in config:
        keywords.append(
            ast.keyword(
                arg="allow_origins",
                value=ast.List(
                    elts=[make_constant(o) for o in config["origins"]],
                    ctx=ast.Load(),
                ),
            )
        )
    if "methods" in config:
        keywords.append(
            ast.keyword(
                arg="allow_methods",
                value=ast.List(
                    elts=[make_constant(m) for m in config["methods"]],
                    ctx=ast.Load(),
                ),
            )
        )
    if "headers" in config:
        keywords.append(
            ast.keyword(
                arg="allow_headers",
                value=ast.List(
                    elts=[make_constant(h) for h in config["headers"]],
                    ctx=ast.Load(),
                ),
            )
        )
    if config.get("credentials"):
        keywords.append(
            ast.keyword(arg="allow_credentials", value=make_constant(True))
        )

    call = ast.Call(
        func=ast.Attribute(value=make_name("app"), attr="add_middleware", ctx=ast.Load()),
        args=[make_name("CORSMiddleware")],
        keywords=keywords,
    )
    return ast.Expr(value=call)


def _build_trustedhost_call(mw: MiddlewareNode) -> ast.Expr:
    """Build ``app.add_middleware(TrustedHostMiddleware, ...)`` statement.

    Args:
        mw: The trusted host middleware node.

    Returns:
        An ast.Expr wrapping the add_middleware call.

    Sig: 2026-04-14 created
    """
    config = mw.config
    hosts = config.get("hosts", [])
    call = ast.Call(
        func=ast.Attribute(value=make_name("app"), attr="add_middleware", ctx=ast.Load()),
        args=[make_name("TrustedHostMiddleware")],
        keywords=[
            ast.keyword(
                arg="allowed_hosts",
                value=ast.List(
                    elts=[make_constant(h) for h in hosts],
                    ctx=ast.Load(),
                ),
            )
        ],
    )
    return ast.Expr(value=call)


def _build_gzip_call(mw: MiddlewareNode) -> ast.Expr:
    """Build ``app.add_middleware(GZipMiddleware, ...)`` statement.

    Args:
        mw: The gzip middleware node.

    Returns:
        An ast.Expr wrapping the add_middleware call.

    Sig: 2026-04-14 created
    """
    config = mw.config
    min_size = config.get("minimum_size", 1000)
    call = ast.Call(
        func=ast.Attribute(value=make_name("app"), attr="add_middleware", ctx=ast.Load()),
        args=[make_name("GZipMiddleware")],
        keywords=[
            ast.keyword(arg="minimum_size", value=make_constant(min_size))
        ],
    )
    return ast.Expr(value=call)


def _build_unknown_comment(mw: MiddlewareNode) -> ast.Expr:
    """Build a placeholder comment for unknown middleware kinds.

    Args:
        mw: The unrecognized middleware node.

    Returns:
        An ast.Expr with a string constant acting as a comment.

    Sig: 2026-04-14 created
    """
    return ast.Expr(
        value=make_constant(f"TODO: Unknown middleware kind: {mw.kind}")
    )


def _build_rate_limit_call(mw: MiddlewareNode) -> list[ast.stmt]:
    """Build slowapi rate-limiter setup statements.

    Generates:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.util import get_remote_address
        from slowapi.errors import RateLimitExceeded

        limiter = Limiter(key_func=get_remote_address, storage_uri="redis://...")
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    Args:
        mw: The rate_limit middleware node.

    Returns:
        A list of ast.stmt for the rate-limiter setup.

    Sig: 2026-04-15 created
    """
    config = mw.config
    storage_uri = config.get("storage_uri", "memory://")
    default_rate = config.get("rate", "10/minute")

    stmts: list[ast.stmt] = []

    # limiter = Limiter(key_func=get_remote_address, storage_uri=..., default_limits=[...])
    limiter_assign = ast.Assign(
        targets=[make_name("limiter")],
        value=ast.Call(
            func=make_name("Limiter"),
            args=[],
            keywords=[
                ast.keyword(arg="key_func", value=make_name("get_remote_address")),
                ast.keyword(arg="storage_uri", value=make_constant(storage_uri)),
                ast.keyword(
                    arg="default_limits",
                    value=ast.List(
                        elts=[make_constant(default_rate)],
                        ctx=ast.Load(),
                    ),
                ),
            ],
        ),
        lineno=0,
        col_offset=0,
        end_lineno=None,
        end_col_offset=None,
    )
    stmts.append(limiter_assign)

    # app.state.limiter = limiter
    state_assign = ast.Assign(
        targets=[
            ast.Attribute(
                value=ast.Attribute(
                    value=make_name("app"),
                    attr="state",
                    ctx=ast.Load(),
                ),
                attr="limiter",
                ctx=ast.Store(),
            )
        ],
        value=make_name("limiter"),
        lineno=0,
        col_offset=0,
        end_lineno=None,
        end_col_offset=None,
    )
    stmts.append(state_assign)

    # app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    handler_call = ast.Expr(
        value=ast.Call(
            func=ast.Attribute(
                value=make_name("app"),
                attr="add_exception_handler",
                ctx=ast.Load(),
            ),
            args=[
                make_name("RateLimitExceeded"),
                make_name("_rate_limit_exceeded_handler"),
            ],
            keywords=[],
        )
    )
    stmts.append(handler_call)

    return stmts


def _python_literal_to_ast(value: Any) -> ast.expr:
    """Convert a Python literal value into an AST expression.

    Handles primitives, lists, and dicts recursively. Falls back to
    ``ast.Constant`` for anything else.

    Sig: 2026-05-07 created
    """
    if isinstance(value, list):
        return ast.List(
            elts=[_python_literal_to_ast(v) for v in value],
            ctx=ast.Load(),
        )
    if isinstance(value, dict):
        return ast.Dict(
            keys=[make_constant(k) for k in value.keys()],
            values=[_python_literal_to_ast(v) for v in value.values()],
        )
    return make_constant(value)


_CUSTOM_META_KEYS = frozenset({"class_path", "handler"})


def _build_custom_middleware_call(mw: MiddlewareNode) -> list[ast.stmt]:
    """Build ``app.add_middleware(...)`` statements for a custom middleware.

    Two configuration patterns are supported:

    1. ``class_path`` — dotted path to a middleware class. Generates::

           from <module> import <ClassName>
           app.add_middleware(<ClassName>, **remaining_config)

    2. ``handler`` — dotted path to a dispatch function. Generates::

           from starlette.middleware.base import BaseHTTPMiddleware
           from <module> import <handler_func>
           app.add_middleware(BaseHTTPMiddleware, dispatch=<handler_func>)

    Args:
        mw: The custom middleware node.

    Returns:
        A list of ast.stmt containing the required imports followed by the
        ``app.add_middleware(...)`` call. When neither key is present, a
        placeholder comment is returned instead.

    Sig: 2026-05-07 created
    """
    config = mw.config
    stmts: list[ast.stmt] = []

    class_path = config.get("class_path")
    handler = config.get("handler")

    if class_path:
        module, _, cls_name = class_path.rpartition(".")
        if not module:
            return [_build_unknown_comment(mw)]
        stmts.append(make_import_from(module, [cls_name]))

        keywords: list[ast.keyword] = []
        for k, v in config.items():
            if k in _CUSTOM_META_KEYS:
                continue
            keywords.append(ast.keyword(arg=k, value=_python_literal_to_ast(v)))

        call = ast.Call(
            func=ast.Attribute(
                value=make_name("app"), attr="add_middleware", ctx=ast.Load()
            ),
            args=[make_name(cls_name)],
            keywords=keywords,
        )
        stmts.append(ast.Expr(value=call))
        return stmts

    if handler:
        module, _, func_name = handler.rpartition(".")
        if not module:
            return [_build_unknown_comment(mw)]
        stmts.append(
            make_import_from("starlette.middleware.base", ["BaseHTTPMiddleware"])
        )
        stmts.append(make_import_from(module, [func_name]))

        call = ast.Call(
            func=ast.Attribute(
                value=make_name("app"), attr="add_middleware", ctx=ast.Load()
            ),
            args=[make_name("BaseHTTPMiddleware")],
            keywords=[ast.keyword(arg="dispatch", value=make_name(func_name))],
        )
        stmts.append(ast.Expr(value=call))
        return stmts

    return [_build_unknown_comment(mw)]


_MIDDLEWARE_BUILDERS = {
    "cors": _build_cors_call,
    "trustedhost": _build_trustedhost_call,
    "gzip": _build_gzip_call,
    "rate_limit": _build_rate_limit_call,
    "custom": _build_custom_middleware_call,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lower_middleware(app: AppNode) -> ast.Module:
    """Generate middleware.py AST with register_middleware function.

    Args:
        app: The root IR application node containing middleware definitions.

    Returns:
        An ast.Module representing the complete middleware.py file.

    Sig: 2026-04-14 created
    """
    body: list[ast.stmt] = []

    # Collect needed imports
    body.append(make_import_from("fastapi", ["FastAPI"]))

    needed_imports: set[str] = set()
    for mw in app.middleware:
        kind = mw.kind.lower()
        if kind in _MIDDLEWARE_MAP:
            needed_imports.add(kind)

    for kind in sorted(needed_imports):
        module, cls_name = _MIDDLEWARE_MAP[kind]
        body.append(make_import_from(module, [cls_name]))

    # rate_limit needs extra imports
    if "rate_limit" in needed_imports:
        body.append(make_import_from("slowapi", ["_rate_limit_exceeded_handler"]))
        body.append(make_import_from("slowapi.util", ["get_remote_address"]))
        body.append(make_import_from("slowapi.errors", ["RateLimitExceeded"]))

    # Build register_middleware function body. Builders may return
    # ``ImportFrom`` statements that we hoist to the module scope, keeping
    # only the add_middleware calls in the function body.
    reg_body: list[ast.stmt] = []
    extra_module_imports: list[ast.ImportFrom] = []
    seen_imports: set[tuple[str, tuple[str, ...]]] = set()
    for mw in app.middleware:
        kind = mw.kind.lower()
        builder = _MIDDLEWARE_BUILDERS.get(kind)
        if builder:
            result = builder(mw)
            stmts = result if isinstance(result, list) else [result]
            for s in stmts:
                if isinstance(s, ast.ImportFrom):
                    key = (s.module or "", tuple(a.name for a in s.names))
                    if key not in seen_imports:
                        seen_imports.add(key)
                        extra_module_imports.append(s)
                else:
                    reg_body.append(s)
        else:
            reg_body.append(_build_unknown_comment(mw))

    for imp in extra_module_imports:
        body.append(imp)

    if not reg_body:
        reg_body.append(ast.Pass(lineno=0, col_offset=0, end_lineno=None, end_col_offset=None))

    reg_args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg="app", annotation=make_name("FastAPI"))],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    reg_func = ast.FunctionDef(
        name="register_middleware",
        args=reg_args,
        body=reg_body,
        decorator_list=[],
        returns=make_constant(None),
        lineno=0,
        col_offset=0,
        end_lineno=None,
        end_col_offset=None,
    )
    body.append(reg_func)

    return make_module(body)
