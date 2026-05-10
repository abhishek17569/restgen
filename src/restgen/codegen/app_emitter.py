"""Emit the top-level FastAPI app module.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import ast

from restgen.ir.nodes import (
    DocsConfig,
    AppNode,
    BackgroundTaskRef,
    DependencyRef,
    ForEachStepNode,
    HealthCheckConfig,
    IfStepNode,
    MiddlewareNode,
    MountConfig,
    PipelineStepNode,
    ReturnStepNode,
    SecuritySchemeNode,
    WebSocketRouteNode,
)
from restgen.codegen.ast_builder import (
    make_name,
    make_constant,
    make_import_from,
    make_module,
    make_assign,
)


def _has_handler_refs(app: AppNode) -> bool:
    """Check if any route, security scheme, task, or dependency references a handler.

    Recurses into control-flow step bodies (if/else/for_each). Also checks
    security scheme ``verify_handler`` values, route ``background_tasks``
    handlers, route ``depends`` dependency handlers, WebSocket route
    handlers (``handler``, ``on_connect``, ``on_disconnect``, ``depends``),
    health check ``custom_checks``, custom middleware ``handler`` /
    ``class_path``, and mount ``app_module`` references so that the
    generated ``__init__.py`` emits the ``sys.path`` shim whenever any of
    these point at external modules.

    Sig: 2026-05-07 modified
    """
    for scheme in app.security_schemes:
        if isinstance(scheme, SecuritySchemeNode) and scheme.verify_handler:
            return True
    for route in app.routes:
        if route.handler:
            return True
        if route.pipeline and _pipeline_has_handler(route.pipeline):
            return True
        for task in route.background_tasks:
            if isinstance(task, BackgroundTaskRef) and task.handler:
                return True
        for dep in route.depends:
            if isinstance(dep, DependencyRef) and dep.handler:
                return True

    for ws in app.websocket_routes:
        if not isinstance(ws, WebSocketRouteNode):
            continue
        if ws.handler or ws.on_connect or ws.on_disconnect:
            return True
        for dep in ws.depends:
            if isinstance(dep, DependencyRef) and dep.handler:
                return True

    if isinstance(app.health_check, HealthCheckConfig):
        if app.health_check.custom_checks:
            return True

    for mw_source in (app.middleware, *[rg.middleware for rg in app.routers]):
        for mw in mw_source:
            if not isinstance(mw, MiddlewareNode):
                continue
            if mw.kind != "custom":
                continue
            cfg = mw.config or {}
            if cfg.get("handler") or cfg.get("class_path"):
                return True

    for mount in app.mounts:
        if isinstance(mount, MountConfig) and mount.app_module:
            return True

    return False


def _pipeline_has_handler(steps: list) -> bool:
    """Recursive search for any handler reference in a step sequence.

    Sig: 2026-04-24 created
    """
    for step in steps:
        if isinstance(step, PipelineStepNode):
            if step.handler:
                return True
        elif isinstance(step, IfStepNode):
            if step.condition and step.condition.when_handler:
                return True
            if _pipeline_has_handler(step.then_steps):
                return True
            if _pipeline_has_handler(step.else_steps):
                return True
        elif isinstance(step, ForEachStepNode):
            if _pipeline_has_handler(step.body):
                return True
        elif isinstance(step, ReturnStepNode):
            if step.condition and step.condition.when_handler:
                return True
    return False


def lower_init(app: AppNode) -> ast.Module | None:
    """Generate __init__.py AST with sys.path setup for handler imports.

    When the config uses handler refs (Tier 2/3), the generated package's
    ``__init__.py`` adds the parent directory to ``sys.path`` so that
    handler modules living next to the generated package are importable
    regardless of the working directory.

    Returns None if no handler refs are present (plain empty __init__.py).

    Args:
        app: The root IR AppNode.

    Returns:
        An ast.Module or None.

    Sig: 2026-04-15 created
    """
    if not _has_handler_refs(app):
        return None

    body: list[ast.stmt] = []
    # import sys; from pathlib import Path
    body.append(ast.Import(names=[ast.alias(name="sys")]))
    body.append(make_import_from("pathlib", ["Path"]))

    # sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    path_expr = ast.Call(
        func=make_name("str"),
        args=[
            ast.Attribute(
                value=ast.Attribute(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Call(
                                func=make_name("Path"),
                                args=[make_name("__file__")],
                                keywords=[],
                            ),
                            attr="resolve",
                            ctx=ast.Load(),
                        ),
                        args=[],
                        keywords=[],
                    ),
                    attr="parent",
                    ctx=ast.Load(),
                ),
                attr="parent",
                ctx=ast.Load(),
            ),
        ],
        keywords=[],
    )
    insert_call = ast.Expr(
        value=ast.Call(
            func=ast.Attribute(
                value=ast.Attribute(
                    value=make_name("sys"),
                    attr="path",
                    ctx=ast.Load(),
                ),
                attr="insert",
                ctx=ast.Load(),
            ),
            args=[make_constant(0), path_expr],
            keywords=[],
        ),
    )
    body.append(insert_call)

    return make_module(body)


def lower_app(app: AppNode) -> ast.Module:
    """Generate app.py AST that wires everything together.

    When the config uses handler refs (Tier 2/3), generates a ``sys.path``
    insert so that handler modules next to the generated package are
    importable regardless of the working directory.

    Generated code:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        from fastapi import FastAPI
        from .routes import router
        from .errors import register_error_handlers
        from .middleware import register_middleware
        from .dependencies import lifespan

        app = FastAPI(title="...", version="...", description="...", lifespan=lifespan)
        register_middleware(app)
        register_error_handlers(app)
        app.include_router(router)

    Args:
        app: The root IR AppNode containing application metadata.

    Returns:
        An ast.Module representing the complete app.py file.

    Sig: 2026-05-07 modified
    """
    body: list[ast.stmt] = []

    # -- sys.path setup (ensures handlers are importable in standalone mode) ---
    if _has_handler_refs(app):
        body.append(ast.Import(names=[ast.alias(name="sys")]))
        body.append(make_import_from("pathlib", ["Path"]))
        # sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        path_expr = ast.Call(
            func=make_name("str"),
            args=[
                ast.Attribute(
                    value=ast.Attribute(
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Call(
                                    func=make_name("Path"),
                                    args=[make_name("__file__")],
                                    keywords=[],
                                ),
                                attr="resolve",
                                ctx=ast.Load(),
                            ),
                            args=[],
                            keywords=[],
                        ),
                        attr="parent",
                        ctx=ast.Load(),
                    ),
                    attr="parent",
                    ctx=ast.Load(),
                ),
            ],
            keywords=[],
        )
        body.append(ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Attribute(value=make_name("sys"), attr="path", ctx=ast.Load()),
                    attr="insert",
                    ctx=ast.Load(),
                ),
                args=[make_constant(0), path_expr],
                keywords=[],
            ),
        ))

    # -- Imports ---------------------------------------------------------------
    body.append(make_import_from("fastapi", ["FastAPI"]))

    # Use try/except for sibling imports so the app works both as a package
    # (uvicorn generated.app:app) and standalone (cd generated && uvicorn app:app).
    relative_imports: list[ast.stmt] = []
    absolute_imports: list[ast.stmt] = []

    if app.routers:
        for group in app.routers:
            module_dotted = _router_module_dotted(group)
            alias = f"{group.name}_router"
            relative_imports.append(
                ast.ImportFrom(
                    module=module_dotted,
                    names=[ast.alias(name="router", asname=alias)],
                    level=1,
                )
            )
            absolute_imports.append(
                ast.ImportFrom(
                    module=module_dotted.lstrip("."),
                    names=[ast.alias(name="router", asname=alias)],
                    level=0,
                )
            )
    else:
        relative_imports.append(make_import_from(".routes", ["router"]))
        absolute_imports.append(make_import_from("routes", ["router"]))

    relative_imports.append(make_import_from(".errors", ["register_error_handlers"]))
    relative_imports.append(make_import_from(".middleware", ["register_middleware"]))
    relative_imports.append(make_import_from(".dependencies", ["lifespan"]))

    absolute_imports.append(make_import_from("errors", ["register_error_handlers"]))
    absolute_imports.append(make_import_from("middleware", ["register_middleware"]))
    absolute_imports.append(make_import_from("dependencies", ["lifespan"]))

    # try: from .X import ... except ImportError: from X import ...
    try_block = ast.Try(
        body=relative_imports,
        handlers=[
            ast.ExceptHandler(
                type=make_name("ImportError"),
                name=None,
                body=absolute_imports,
            )
        ],
        orelse=[],
        finalbody=[],
    )
    body.append(try_block)

    has_ws = bool(app.websocket_routes)
    has_health = isinstance(app.health_check, HealthCheckConfig) and app.health_check.enabled
    mount_targets = [m for m in app.mounts if isinstance(m, MountConfig) and m.app_module]

    if has_ws:
        ws_relative = [ast.ImportFrom(
            module="websockets",
            names=[ast.alias(name="router", asname="ws_router")],
            level=1,
        )]
        ws_absolute = [ast.ImportFrom(
            module="websockets",
            names=[ast.alias(name="router", asname="ws_router")],
            level=0,
        )]
        body.append(ast.Try(
            body=ws_relative,
            handlers=[ast.ExceptHandler(type=make_name("ImportError"), name=None, body=ws_absolute)],
            orelse=[], finalbody=[],
        ))

    if has_health:
        health_relative = [make_import_from(".health", ["health_router"])]
        health_absolute = [make_import_from("health", ["health_router"])]
        body.append(ast.Try(
            body=health_relative,
            handlers=[ast.ExceptHandler(type=make_name("ImportError"), name=None, body=health_absolute)],
            orelse=[], finalbody=[],
        ))

    # Mounts: each sub-app lives at ``app_module`` = "pkg.attr"; import attr
    # from pkg (absolute), keep an alias so multiple mounts don't collide.
    for idx, mount in enumerate(mount_targets):
        module_path, _, attr = mount.app_module.rpartition(".")
        alias = f"_mounted_app_{idx}"
        body.append(
            ast.ImportFrom(
                module=module_path,
                names=[ast.alias(name=attr, asname=alias)],
                level=0,
            )
        )

    # -- app = FastAPI(...) ----------------------------------------------------
    fastapi_keywords: list[ast.keyword] = [
        ast.keyword(arg="title", value=make_constant(app.title)),
        ast.keyword(arg="version", value=make_constant(app.version)),
    ]
    if app.description:
        fastapi_keywords.append(
            ast.keyword(arg="description", value=make_constant(app.description)),
        )
    # Docs configuration (custom URLs or disabled)
    if app.docs and not app.docs.enabled:
        fastapi_keywords.append(ast.keyword(arg="docs_url", value=make_constant(None)))
        fastapi_keywords.append(ast.keyword(arg="redoc_url", value=make_constant(None)))
        fastapi_keywords.append(ast.keyword(arg="openapi_url", value=make_constant(None)))
    elif app.docs:
        if app.docs.docs_url != "/docs":
            fastapi_keywords.append(
                ast.keyword(arg="docs_url", value=make_constant(app.docs.docs_url))
            )
        if app.docs.redoc_url != "/redoc":
            fastapi_keywords.append(
                ast.keyword(arg="redoc_url", value=make_constant(app.docs.redoc_url))
            )
        if app.docs.openapi_url != "/openapi.json":
            fastapi_keywords.append(
                ast.keyword(arg="openapi_url", value=make_constant(app.docs.openapi_url))
            )

    # Wire lifespan for repository connect/disconnect
    fastapi_keywords.append(
        ast.keyword(arg="lifespan", value=make_name("lifespan")),
    )

    app_assign = make_assign(
        "app",
        ast.Call(
            func=make_name("FastAPI"),
            args=[],
            keywords=fastapi_keywords,
        ),
    )
    body.append(app_assign)

    # -- register_middleware(app) -----------------------------------------------
    body.append(
        ast.Expr(
            value=ast.Call(
                func=make_name("register_middleware"),
                args=[make_name("app")],
                keywords=[],
            ),
        ),
    )

    # -- register_error_handlers(app) ------------------------------------------
    body.append(
        ast.Expr(
            value=ast.Call(
                func=make_name("register_error_handlers"),
                args=[make_name("app")],
                keywords=[],
            ),
        ),
    )

    # -- app.include_router(...) -----------------------------------------------
    if app.routers:
        for group in app.routers:
            alias = f"{group.name}_router"
            keywords: list[ast.keyword] = []
            if group.prefix:
                keywords.append(
                    ast.keyword(arg="prefix", value=make_constant(group.prefix))
                )
            if group.tags:
                keywords.append(
                    ast.keyword(
                        arg="tags",
                        value=ast.List(
                            elts=[make_constant(t) for t in group.tags],
                            ctx=ast.Load(),
                        ),
                    )
                )
            body.append(
                ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=make_name("app"),
                            attr="include_router",
                            ctx=ast.Load(),
                        ),
                        args=[make_name(alias)],
                        keywords=keywords,
                    ),
                ),
            )
    else:
        body.append(
            ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(
                        value=make_name("app"),
                        attr="include_router",
                        ctx=ast.Load(),
                    ),
                    args=[make_name("router")],
                    keywords=[],
                ),
            ),
        )

    # -- app.include_router(ws_router) / health_router -----------------------
    for router_name in (
        ("ws_router" if has_ws else None),
        ("health_router" if has_health else None),
    ):
        if router_name is None:
            continue
        body.append(
            ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(
                        value=make_name("app"),
                        attr="include_router",
                        ctx=ast.Load(),
                    ),
                    args=[make_name(router_name)],
                    keywords=[],
                ),
            ),
        )

    # -- app.mount(path, sub_app, name=...) ----------------------------------
    for idx, mount in enumerate(mount_targets):
        alias = f"_mounted_app_{idx}"
        mount_keywords: list[ast.keyword] = []
        if mount.name:
            mount_keywords.append(
                ast.keyword(arg="name", value=make_constant(mount.name))
            )
        body.append(
            ast.Expr(
                value=ast.Call(
                    func=ast.Attribute(
                        value=make_name("app"),
                        attr="mount",
                        ctx=ast.Load(),
                    ),
                    args=[make_constant(mount.path), make_name(alias)],
                    keywords=mount_keywords,
                ),
            ),
        )

    return make_module(body)


def _router_module_dotted(group) -> str:
    """Derive the Python dotted module name (for `from .X import`) for a router.

    Mirrors ``_router_module_path`` in the lower pass.

    Sig: 2026-04-24 created
    """
    from pathlib import PurePosixPath

    if group.source_path is not None:
        rel = str(group.source_path)
        if rel.endswith((".yaml", ".yml")):
            rel = rel.rsplit(".", 1)[0]
        if not rel.startswith("routers/"):
            rel = "routers/" + rel
        # convert path segments to dotted form
        return ".".join(PurePosixPath(rel).parts)
    return f"routers.{group.name}"
