"""Emit WebSocket route module from IR WebSocketRouteNodes.

Sig: 2026-05-07 created
"""
from __future__ import annotations

import ast

from restgen.ir.nodes import (
    AppNode,
    DependencyRef,
    FieldNode,
    WebSocketRouteNode,
)
from restgen.ir.types import FieldType, ScalarType
from restgen.codegen.ast_builder import (
    make_assign,
    make_async_func,
    make_await,
    make_constant,
    make_import_from,
    make_module,
    make_name,
    make_return,
)


# ---------------------------------------------------------------------------
# Scalar -> annotation mapping (aligned with route_emitter conventions)
# ---------------------------------------------------------------------------

_SCALAR_TO_ANNOTATION: dict[ScalarType, str] = {
    ScalarType.STR: "str",
    ScalarType.INT: "int",
    ScalarType.FLOAT: "float",
    ScalarType.BOOL: "bool",
    ScalarType.DATETIME: "datetime",
    ScalarType.DATE: "date",
    ScalarType.UUID: "UUID",
    ScalarType.BYTES: "bytes",
    ScalarType.ANY: "Any",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _last_segment(dotted: str) -> str:
    """Return the last dot-delimited segment of an import path.

    Args:
        dotted: Dotted path such as ``handlers.ws.on_message``.

    Returns:
        The final segment (``on_message``) or the input if it has no dots.

    Sig: 2026-05-07 created
    """
    return dotted.rsplit(".", 1)[-1] if dotted else dotted


def _split_handler(dotted: str) -> tuple[str, str] | None:
    """Split a dotted handler path into ``(module, name)`` for imports.

    Args:
        dotted: Dotted path such as ``handlers.ws.on_message``.

    Returns:
        ``(module, name)`` tuple, or None if the path has no module segment.

    Sig: 2026-05-07 created
    """
    if not dotted:
        return None
    parts = dotted.rsplit(".", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _scalar_annotation(ft: FieldType) -> str:
    """Return the Python annotation string for a scalar FieldType.

    Args:
        ft: The IR field type to render.

    Returns:
        Annotation identifier (``str`` when no scalar is set).

    Sig: 2026-05-07 created
    """
    if ft.scalar:
        return _SCALAR_TO_ANNOTATION.get(ft.scalar, "str")
    if ft.ref:
        return ft.ref
    return "str"


def _path_param_annotation(field: FieldNode) -> ast.expr:
    """Build the AST annotation node for a WebSocket path parameter.

    Sig: 2026-05-07 created
    """
    return make_name(_scalar_annotation(field.field_type))


def _depends_handler(dep: DependencyRef) -> ast.expr:
    """Build ``Depends(<last_segment>)`` for a DependencyRef.

    Sig: 2026-05-07 created
    """
    return ast.Call(
        func=make_name("Depends"),
        args=[make_name(_last_segment(dep.handler))],
        keywords=[],
    )


def _forwarded_kwargs(route: WebSocketRouteNode) -> list[ast.keyword]:
    """Build the keyword args forwarded to each handler invocation.

    Every lifecycle + message handler receives ``websocket=websocket`` plus
    the path params and any named dependencies.

    Args:
        route: The WebSocket route whose handlers are being invoked.

    Returns:
        List of ast.keyword arguments to attach to handler calls.

    Sig: 2026-05-07 created
    """
    kwargs: list[ast.keyword] = [
        ast.keyword(arg="websocket", value=make_name("websocket")),
    ]
    for pp in route.path_params:
        kwargs.append(ast.keyword(arg=pp.name, value=make_name(pp.name)))
    for dep in route.depends:
        if dep.as_name:
            kwargs.append(
                ast.keyword(arg=dep.as_name, value=make_name(dep.as_name))
            )
    return kwargs


def _build_ws_body(route: WebSocketRouteNode) -> list[ast.stmt]:
    """Build the async body for a WebSocket endpoint.

    Emits ``await websocket.accept()`` followed by an optional on_connect
    call, a ``while True`` receive loop dispatched to the message handler,
    and a ``WebSocketDisconnect`` handler that invokes on_disconnect when
    configured.

    Args:
        route: The WebSocket route being lowered.

    Returns:
        Ordered list of async function body statements.

    Sig: 2026-05-07 created
    """
    body: list[ast.stmt] = []

    accept_call = ast.Call(
        func=ast.Attribute(
            value=make_name("websocket"),
            attr="accept",
            ctx=ast.Load(),
        ),
        args=[],
        keywords=[],
    )
    body.append(ast.Expr(value=make_await(accept_call)))

    forwarded = _forwarded_kwargs(route)

    if route.on_connect:
        connect_call = ast.Call(
            func=make_name(_last_segment(route.on_connect)),
            args=[],
            keywords=forwarded,
        )
        body.append(ast.Expr(value=make_await(connect_call)))

    # while True: data = await websocket.receive_text(); await handler(...)
    receive_call = ast.Call(
        func=ast.Attribute(
            value=make_name("websocket"),
            attr="receive_text",
            ctx=ast.Load(),
        ),
        args=[],
        keywords=[],
    )
    data_assign = make_assign("data", make_await(receive_call))

    handler_call = ast.Call(
        func=make_name(_last_segment(route.handler) if route.handler else "handler"),
        args=[],
        keywords=[*forwarded, ast.keyword(arg="data", value=make_name("data"))],
    )
    loop_body: list[ast.stmt] = [
        data_assign,
        ast.Expr(value=make_await(handler_call)),
    ]

    loop = ast.While(
        test=make_constant(True),
        body=loop_body,
        orelse=[],
    )

    handler_body: list[ast.stmt]
    if route.on_disconnect:
        disconnect_call = ast.Call(
            func=make_name(_last_segment(route.on_disconnect)),
            args=[],
            keywords=forwarded,
        )
        handler_body = [ast.Expr(value=make_await(disconnect_call))]
    else:
        handler_body = [ast.Pass()]

    try_stmt = ast.Try(
        body=[loop],
        handlers=[
            ast.ExceptHandler(
                type=make_name("WebSocketDisconnect"),
                name=None,
                body=handler_body,
            )
        ],
        orelse=[],
        finalbody=[],
    )
    body.append(try_stmt)

    return body


def _build_ws_func(route: WebSocketRouteNode) -> ast.AsyncFunctionDef:
    """Build the decorated async function for a WebSocket route.

    Parameter ordering:
        1. ``websocket: WebSocket``.
        2. Path params (no defaults).
        3. Named ``Depends(...)`` dependencies.

    Args:
        route: The WebSocket route being lowered.

    Returns:
        An ast.AsyncFunctionDef with the ``@router.websocket(path)``
        decorator applied.

    Sig: 2026-05-07 created
    """
    args: list[ast.arg] = [
        ast.arg(arg="websocket", annotation=make_name("WebSocket"))
    ]
    defaults: list[ast.expr] = []

    for pp in route.path_params:
        args.append(ast.arg(arg=pp.name, annotation=_path_param_annotation(pp)))

    for dep in route.depends:
        if not dep.as_name or not dep.handler:
            continue
        args.append(ast.arg(arg=dep.as_name, annotation=None))
        defaults.append(_depends_handler(dep))

    arguments = ast.arguments(
        posonlyargs=[],
        args=args,
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=defaults,
    )

    decorator = ast.Call(
        func=ast.Attribute(
            value=make_name("router"),
            attr="websocket",
            ctx=ast.Load(),
        ),
        args=[make_constant(route.path)],
        keywords=[],
    )

    func_name = route.name or _default_ws_name(route.path)

    return make_async_func(
        name=func_name,
        args=arguments,
        body=_build_ws_body(route),
        decorators=[decorator],
    )


def _default_ws_name(path: str) -> str:
    """Derive a default function name from a WebSocket path.

    ``/ws/{room_id}`` becomes ``ws_ws_room_id``.

    Sig: 2026-05-07 created
    """
    cleaned = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    return f"ws_{cleaned}" if cleaned else "ws_root"


def _collect_handler_imports(
    routes: list[WebSocketRouteNode],
) -> list[tuple[str, str]]:
    """Collect unique ``(module, name)`` pairs for every handler ref.

    Covers message handlers, on_connect, on_disconnect, and DependencyRef
    callables across all routes.

    Args:
        routes: The WebSocket routes to inspect.

    Returns:
        Ordered list of unique import tuples in first-seen order.

    Sig: 2026-05-07 created
    """
    imports: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(dotted: str | None) -> None:
        if not dotted:
            return
        split = _split_handler(dotted)
        if split is None:
            return
        if split in seen:
            return
        seen.add(split)
        imports.append(split)

    for route in routes:
        _add(route.handler)
        _add(route.on_connect)
        _add(route.on_disconnect)
        for dep in route.depends:
            _add(dep.handler)

    return imports


def _needs_depends(routes: list[WebSocketRouteNode]) -> bool:
    """Return True when any route declares a named DependencyRef.

    Sig: 2026-05-07 created
    """
    return any(
        dep.handler and dep.as_name
        for route in routes
        for dep in route.depends
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lower_websockets(app: AppNode) -> ast.Module | None:
    """Generate a WebSocket routes module from an AppNode.

    Produces a module that exposes ``router = APIRouter()`` with one
    ``@router.websocket(path)``-decorated async function per WebSocket
    route. Returns None when the app declares no WebSocket routes.

    Args:
        app: The root IR application node.

    Returns:
        ast.Module for the WebSocket routes file, or None if there are
        no WebSocket routes to emit.

    Sig: 2026-05-07 created
    """
    routes = app.websocket_routes
    if not routes:
        return None

    body: list[ast.stmt] = []

    fastapi_names = {"APIRouter", "WebSocket", "WebSocketDisconnect"}
    if _needs_depends(routes):
        fastapi_names.add("Depends")
    body.append(make_import_from("fastapi", sorted(fastapi_names)))

    for module, name in _collect_handler_imports(routes):
        body.append(make_import_from(module, [name]))

    body.append(
        make_assign(
            "router",
            ast.Call(func=make_name("APIRouter"), args=[], keywords=[]),
        )
    )

    for route in routes:
        body.append(_build_ws_func(route))

    return make_module(body)
