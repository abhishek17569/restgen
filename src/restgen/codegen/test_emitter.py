"""Emit pytest test files from IR.

Sig: 2026-05-07 created
"""
from __future__ import annotations

import ast

from restgen.ir.nodes import (
    ActionKind,
    AppNode,
    HttpMethod,
    RouteNode,
    TestConfig,
)
from restgen.codegen.ast_builder import (
    make_async_func,
    make_await,
    make_constant,
    make_import_from,
    make_module,
    make_name,
    make_return,
)


_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def lower_tests(app: AppNode) -> dict[str, ast.Module] | None:
    """Emit pytest test modules for the generated FastAPI app.

    Produces ``tests/conftest.py`` (httpx AsyncClient fixture over ASGITransport)
    and ``tests/test_api.py`` (one or more test functions per route). Returns
    ``None`` when tests are disabled via ``app.test_config``.

    Args:
        app: The root IR AppNode.

    Returns:
        Mapping of relative file paths to AST modules, or ``None`` when
        test generation is disabled.

    Sig: 2026-05-07 created
    """
    cfg = app.test_config
    if cfg is None or not cfg.generate:
        return None

    return {
        "tests/conftest.py": _build_conftest(),
        "tests/test_api.py": _build_test_api(app, cfg),
    }


# ---------------------------------------------------------------------------
# conftest.py
# ---------------------------------------------------------------------------

def _build_conftest() -> ast.Module:
    """Build the conftest module with the httpx AsyncClient fixture.

    Returns:
        An ast.Module for ``tests/conftest.py``.

    Sig: 2026-05-07 created
    """
    body: list[ast.stmt] = [
        ast.Import(names=[ast.alias(name="pytest")]),
        make_import_from("httpx", ["AsyncClient", "ASGITransport"]),
        ast.ImportFrom(module="app", names=[ast.alias(name="app")], level=1),
    ]

    transport_assign = ast.Assign(
        targets=[ast.Name(id="transport", ctx=ast.Store())],
        value=ast.Call(
            func=make_name("ASGITransport"),
            args=[],
            keywords=[ast.keyword(arg="app", value=make_name("app"))],
        ),
        lineno=0,
    )

    async_with = ast.AsyncWith(
        items=[
            ast.withitem(
                context_expr=ast.Call(
                    func=make_name("AsyncClient"),
                    args=[],
                    keywords=[
                        ast.keyword(arg="transport", value=make_name("transport")),
                        ast.keyword(arg="base_url", value=make_constant("http://test")),
                    ],
                ),
                optional_vars=ast.Name(id="ac", ctx=ast.Store()),
            ),
        ],
        body=[ast.Expr(value=ast.Yield(value=make_name("ac")))],
    )

    fixture_decorator = ast.Attribute(
        value=make_name("pytest"),
        attr="fixture",
        ctx=ast.Load(),
    )

    client_fixture = make_async_func(
        name="client",
        args=[],
        body=[transport_assign, async_with],
        decorators=[fixture_decorator],
    )

    body.append(client_fixture)
    return make_module(body)


# ---------------------------------------------------------------------------
# test_api.py
# ---------------------------------------------------------------------------

def _build_test_api(app: AppNode, cfg: TestConfig) -> ast.Module:
    """Build the test_api module with per-route test functions.

    Args:
        app: The root IR AppNode.
        cfg: The resolved test configuration.

    Returns:
        An ast.Module for ``tests/test_api.py``.

    Sig: 2026-05-07 created
    """
    body: list[ast.stmt] = [
        ast.Import(names=[ast.alias(name="pytest")]),
        make_import_from("httpx", ["AsyncClient"]),
    ]

    marker = _anyio_marker(cfg)
    seen_names: set[str] = set()

    if app.health_check is not None and app.health_check.enabled:
        body.extend(_tests_for_health(app, marker, seen_names))

    for route in _iter_routes(app):
        body.extend(_tests_for_route(route, marker, seen_names))

    if len(body) == 2:
        # No routes produced tests; emit a placeholder so pytest collects cleanly.
        body.append(_placeholder_test(marker))

    return make_module(body)


def _iter_routes(app: AppNode) -> list[RouteNode]:
    """Yield every route across the app, including router-scoped routes.

    Returns:
        A flat list of RouteNodes with prefixes applied to their paths.

    Sig: 2026-05-07 created
    """
    routes: list[RouteNode] = list(app.routes)
    for group in app.routers:
        for route in group.routes:
            prefixed = _with_prefix(route, group.prefix)
            routes.append(prefixed)
    return routes


def _with_prefix(route: RouteNode, prefix: str) -> RouteNode:
    """Return a shallow view of ``route`` with ``prefix`` prepended to its path.

    Sig: 2026-05-07 created
    """
    if not prefix:
        return route
    joined = prefix.rstrip("/") + "/" + route.path.lstrip("/")
    clone = RouteNode(
        path=joined,
        method=route.method,
        name=route.name,
        action=route.action,
        handler=route.handler,
        request_model=route.request_model,
        response_model=route.response_model,
        path_params=route.path_params,
        file_params=route.file_params,
        model=route.model,
    )
    return clone


# ---------------------------------------------------------------------------
# Per-route test assembly
# ---------------------------------------------------------------------------

def _tests_for_route(
    route: RouteNode,
    marker: ast.expr,
    seen_names: set[str],
) -> list[ast.stmt]:
    """Produce zero or more test function defs for a single route.

    Args:
        route: The route to test.
        marker: The ``@pytest.mark.anyio`` (or asyncio) decorator AST.
        seen_names: Mutable set of already-emitted test names for deduplication.

    Returns:
        List of statements (AsyncFunctionDef nodes, plus optional comments).

    Sig: 2026-05-07 created
    """
    if route.file_params:
        return [_file_upload_placeholder(route, marker, seen_names)]

    action = route.action
    if action is ActionKind.DB_LIST:
        return [_test_db_list(route, marker, seen_names)]
    if action is ActionKind.DB_GET:
        return [
            _test_db_get_not_found(route, marker, seen_names),
        ]
    if action is ActionKind.DB_CREATE:
        return [_test_db_create(route, marker, seen_names)]
    if action is ActionKind.DB_UPDATE:
        return [_test_db_update(route, marker, seen_names)]
    if action is ActionKind.DB_DELETE:
        return [_test_db_delete_not_found(route, marker, seen_names)]

    # Handler or other routes: basic status-code smoke test.
    return [_test_handler_basic(route, marker, seen_names)]


def _test_db_list(
    route: RouteNode, marker: ast.expr, seen: set[str],
) -> ast.AsyncFunctionDef:
    """Test a DB_LIST route: ``GET`` returns 200.

    Sig: 2026-05-07 created
    """
    name = _unique_name(f"test_{_slug(route)}_list_ok", seen)
    body = [
        _assign_response(route.method, _static_path(route.path)),
        _assert_status(200),
    ]
    return _make_test_func(name, body, marker)


def _test_db_get_not_found(
    route: RouteNode, marker: ast.expr, seen: set[str],
) -> ast.AsyncFunctionDef:
    """Test a DB_GET route: unknown id returns 404.

    Sig: 2026-05-07 created
    """
    name = _unique_name(f"test_{_slug(route)}_get_not_found", seen)
    body = [
        _assign_response(route.method, _path_with_zero_ids(route)),
        _assert_status(404),
    ]
    return _make_test_func(name, body, marker)


def _test_db_create(
    route: RouteNode, marker: ast.expr, seen: set[str],
) -> ast.AsyncFunctionDef:
    """Test a DB_CREATE route: empty body returns 201 with an ``id``.

    Sig: 2026-05-07 created
    """
    name = _unique_name(f"test_{_slug(route)}_create", seen)
    body = [
        _assign_response(
            route.method,
            _static_path(route.path),
            json_body=ast.Dict(keys=[], values=[]),
        ),
        _assert_status(201),
        ast.Assert(
            test=ast.Compare(
                left=make_constant("id"),
                ops=[ast.In()],
                comparators=[
                    ast.Call(
                        func=ast.Attribute(
                            value=make_name("response"),
                            attr="json",
                            ctx=ast.Load(),
                        ),
                        args=[],
                        keywords=[],
                    ),
                ],
            ),
            msg=None,
        ),
    ]
    return _make_test_func(name, body, marker)


def _test_db_update(
    route: RouteNode, marker: ast.expr, seen: set[str],
) -> ast.AsyncFunctionDef:
    """Test a DB_UPDATE route: unknown id with empty body returns 404.

    Sig: 2026-05-07 created
    """
    name = _unique_name(f"test_{_slug(route)}_update_not_found", seen)
    body = [
        _assign_response(
            route.method,
            _path_with_zero_ids(route),
            json_body=ast.Dict(keys=[], values=[]),
        ),
        _assert_status(404),
    ]
    return _make_test_func(name, body, marker)


def _test_db_delete_not_found(
    route: RouteNode, marker: ast.expr, seen: set[str],
) -> ast.AsyncFunctionDef:
    """Test a DB_DELETE route: unknown id returns 404.

    Sig: 2026-05-07 created
    """
    name = _unique_name(f"test_{_slug(route)}_delete_not_found", seen)
    body = [
        _assign_response(route.method, _path_with_zero_ids(route)),
        _assert_status(404),
    ]
    return _make_test_func(name, body, marker)


def _test_handler_basic(
    route: RouteNode, marker: ast.expr, seen: set[str],
) -> ast.AsyncFunctionDef:
    """Smoke test for a handler route: asserts the response exists.

    Sig: 2026-05-07 created
    """
    name = _unique_name(f"test_{_slug(route)}_smoke", seen)
    path_expr = (
        _path_with_zero_ids(route) if route.path_params else _static_path(route.path)
    )
    needs_body = route.method in (HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH)
    json_body = ast.Dict(keys=[], values=[]) if needs_body else None
    body = [
        _assign_response(route.method, path_expr, json_body=json_body),
        ast.Assert(
            test=ast.Compare(
                left=ast.Attribute(
                    value=make_name("response"),
                    attr="status_code",
                    ctx=ast.Load(),
                ),
                ops=[ast.Lt()],
                comparators=[make_constant(600)],
            ),
            msg=None,
        ),
    ]
    return _make_test_func(name, body, marker)


def _tests_for_health(
    app: AppNode, marker: ast.expr, seen: set[str],
) -> list[ast.stmt]:
    """Emit a test for the configured health endpoint.

    Sig: 2026-05-07 created
    """
    hc = app.health_check
    if hc is None or not hc.enabled:
        return []
    name = _unique_name("test_health_ok", seen)
    body = [
        _assign_response(HttpMethod.GET, make_constant(hc.path)),
        _assert_status(200),
    ]
    return [_make_test_func(name, body, marker)]


def _file_upload_placeholder(route: RouteNode) -> ast.stmt:
    """Emit a commented-out placeholder skipping file upload tests.

    Sig: 2026-05-07 created
    """
    pass_stmt = ast.Pass()
    return pass_stmt


def _placeholder_test(marker: ast.expr) -> ast.AsyncFunctionDef:
    """Trivial passing test to keep pytest from erroring on empty modules.

    Sig: 2026-05-07 created
    """
    return _make_test_func(
        name="test_app_importable",
        body=[ast.Pass()],
        marker=marker,
    )


# ---------------------------------------------------------------------------
# AST fragment helpers
# ---------------------------------------------------------------------------

def _make_test_func(
    name: str, body: list[ast.stmt], marker: ast.expr,
) -> ast.AsyncFunctionDef:
    """Wrap a test body with the ``client: AsyncClient`` arg and anyio marker.

    Sig: 2026-05-07 created
    """
    args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg="client", annotation=make_name("AsyncClient"))],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )
    return make_async_func(
        name=name,
        args=args,
        body=body,
        decorators=[marker],
    )


def _anyio_marker(cfg: TestConfig) -> ast.expr:
    """Build the ``@pytest.mark.<async_mode>`` decorator expression.

    Sig: 2026-05-07 created
    """
    mode = cfg.async_mode or "anyio"
    return ast.Attribute(
        value=ast.Attribute(
            value=make_name("pytest"),
            attr="mark",
            ctx=ast.Load(),
        ),
        attr=mode,
        ctx=ast.Load(),
    )


def _assign_response(
    method: HttpMethod,
    path_expr: ast.expr,
    json_body: ast.expr | None = None,
) -> ast.Assign:
    """Build ``response = await client.<method>(<path>, json=...)``.

    Sig: 2026-05-07 created
    """
    method_attr = ast.Attribute(
        value=make_name("client"),
        attr=method.value.lower(),
        ctx=ast.Load(),
    )
    keywords: list[ast.keyword] = []
    if json_body is not None:
        keywords.append(ast.keyword(arg="json", value=json_body))
    call = ast.Call(func=method_attr, args=[path_expr], keywords=keywords)
    return ast.Assign(
        targets=[ast.Name(id="response", ctx=ast.Store())],
        value=make_await(call),
        lineno=0,
    )


def _assert_status(code: int) -> ast.Assert:
    """Build ``assert response.status_code == <code>``.

    Sig: 2026-05-07 created
    """
    return ast.Assert(
        test=ast.Compare(
            left=ast.Attribute(
                value=make_name("response"),
                attr="status_code",
                ctx=ast.Load(),
            ),
            ops=[ast.Eq()],
            comparators=[make_constant(code)],
        ),
        msg=None,
    )


def _static_path(path: str) -> ast.expr:
    """Represent a literal URL path as a string constant.

    Sig: 2026-05-07 created
    """
    return make_constant(path)


def _path_with_zero_ids(route: RouteNode) -> ast.expr:
    """Substitute path params with zero-UUID placeholders.

    Sig: 2026-05-07 created
    """
    substituted = route.path
    for param in route.path_params:
        token = "{" + param.name + "}"
        substituted = substituted.replace(token, _ZERO_UUID)
    # Any unresolved placeholder (e.g. path_params not populated) is swapped
    # for a zero-UUID too.
    while "{" in substituted and "}" in substituted:
        start = substituted.index("{")
        end = substituted.index("}", start)
        substituted = substituted[:start] + _ZERO_UUID + substituted[end + 1 :]
    return make_constant(substituted)


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def _slug(route: RouteNode) -> str:
    """Produce a filesystem-safe slug from a route's name or path.

    Sig: 2026-05-07 created
    """
    if route.name:
        return route.name
    cleaned = (
        route.path.strip("/")
        .replace("/", "_")
        .replace("{", "")
        .replace("}", "")
        .replace("-", "_")
    )
    return cleaned or "root"


def _unique_name(candidate: str, seen: set[str]) -> str:
    """Return ``candidate`` with a numeric suffix if already present in ``seen``.

    Sig: 2026-05-07 created
    """
    if candidate not in seen:
        seen.add(candidate)
        return candidate
    idx = 2
    while f"{candidate}_{idx}" in seen:
        idx += 1
    final = f"{candidate}_{idx}"
    seen.add(final)
    return final
