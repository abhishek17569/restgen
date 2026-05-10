"""Emit FastAPI route functions from IR RouteNodes.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import ast
from typing import Any

from restgen.ir.nodes import (
    AppNode,
    RouteNode,
    ActionKind,
    HttpMethod,
    FieldNode,
    FilterNode,
    PaginationConfig,
    ErrorRef,
    BackgroundTaskRef,
    FileParamNode,
    ResponseHeaderNode,
    CookieNode,
    DependencyRef,
    RouteParamNode,
    ParamSource,
    ResponseKind,
    CacheConfig,
    RateLimitConfig,
    OpenAPIExtras,
    StreamingConfig,
)
from restgen.ir.types import FieldType, ScalarType
from restgen.codegen.ast_builder import (
    make_name,
    make_constant,
    make_import_from,
    make_annotation,
    make_async_func,
    make_class,
    make_module,
    make_assign,
    make_await,
    make_return,
    make_raise_http_exception,
    make_if_none_raise_404,
    make_if_none_raise_named,
    make_field_call,
    collect_imports,
)
from restgen.codegen.pipeline_emitter import lower_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_class_name(error_name: str) -> str:
    """Derive exception class name from error name, ensuring 'Error' suffix.

    Sig: 2026-04-15 created
    """
    if "_" not in error_name and error_name[0:1].isupper():
        pascal = error_name
    else:
        pascal = "".join(word.capitalize() for word in error_name.split("_"))
    if not pascal.endswith("Error"):
        pascal += "Error"
    return pascal


def _find_error_ref(route: RouteNode, condition: str) -> ErrorRef | None:
    """Find an ErrorRef by condition name on a route.

    Sig: 2026-04-15 created
    """
    for err in route.errors:
        if err.condition == condition:
            return err
    return None


def _build_none_check(
    var_name: str, route: RouteNode, condition: str = "not_found",
) -> ast.If:
    """Build a None-check that raises the route's error ref or a generic 404.

    If the route has an error ref for the given condition with a named ref,
    raises the corresponding error class. For inline errors, uses
    HTTPException with the specified status/body. Falls back to generic 404.

    Args:
        var_name: Variable to check for None.
        route: The RouteNode to look up error refs on.
        condition: The error condition name to match.

    Returns:
        An ast.If node.

    Sig: 2026-04-15 created
    """
    err = _find_error_ref(route, condition)
    if err is not None:
        if err.ref:
            # Named error → raise NotFoundError()
            return make_if_none_raise_named(var_name, _error_class_name(err.ref))
        elif err.status:
            # Inline error → raise HTTPException(status_code=N, detail="...")
            detail = err.body.get("message", "Error") if err.body else "Error"
            return ast.If(
                test=ast.Compare(
                    left=make_name(var_name),
                    ops=[ast.Is()],
                    comparators=[make_constant(None)],
                ),
                body=[make_raise_http_exception(err.status, make_constant(detail))],
                orelse=[],
            )
    return make_if_none_raise_404(var_name)


def _collect_error_imports(routes: list[RouteNode]) -> list[str]:
    """Collect error class names referenced by routes for imports.

    Sig: 2026-04-15 created
    """
    names: set[str] = set()
    for route in routes:
        for err in route.errors:
            if err.ref:
                names.add(_error_class_name(err.ref))
    return sorted(names)


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

_ACTION_STATUS: dict[ActionKind, int] = {
    ActionKind.DB_LIST: 200,
    ActionKind.DB_GET: 200,
    ActionKind.DB_CREATE: 201,
    ActionKind.DB_UPDATE: 200,
    ActionKind.DB_DELETE: 204,
}


def _scalar_annotation(ft: FieldType) -> str:
    """Return Python annotation string for a scalar FieldType.

    Sig: 2026-04-14 created
    """
    if ft.scalar:
        return _SCALAR_TO_ANNOTATION.get(ft.scalar, "Any")
    if ft.ref:
        return ft.ref
    return "Any"


def _path_param_annotation(field: FieldNode) -> ast.expr:
    """Build an AST annotation node for a path parameter.

    Sig: 2026-04-14 created
    """
    return make_name(_scalar_annotation(field.field_type))


def _depends_repo() -> ast.expr:
    """Build ``Depends(get_repository)`` AST node.

    Sig: 2026-04-14 created
    """
    return ast.Call(
        func=make_name("Depends"),
        args=[make_name("get_repository")],
        keywords=[],
    )


def _query_default(value: Any, alias: str | None = None) -> ast.expr:
    """Build ``Query(default=...)`` AST node.

    Sig: 2026-04-14 created
    """
    keywords = [ast.keyword(arg="default", value=make_constant(value))]
    if alias:
        keywords.append(ast.keyword(arg="alias", value=make_constant(alias)))
    return ast.Call(func=make_name("Query"), args=[], keywords=keywords)


_PARAM_SOURCE_FACTORIES: dict[ParamSource, str] = {
    ParamSource.QUERY: "Query",
    ParamSource.FORM: "Form",
    ParamSource.COOKIE: "Cookie",
    ParamSource.HEADER: "Header",
}


def _param_factory_call(factory: str, default: Any, required: bool, alias: str | None = None) -> ast.expr:
    """Build a ``Factory(default, alias=...)`` AST call for an extended param.

    Args:
        factory: FastAPI dependency factory name (``Query``/``Form``/``Cookie``/``Header``).
        default: Literal default value when the param is optional.
        required: If True, uses ``...`` (Ellipsis) as the positional argument.
        alias: Optional wire-name alias to forward to the factory.

    Returns:
        An ast.Call producing e.g. ``Form(...)`` or ``Cookie(None, alias="sid")``.

    Sig: 2026-05-07 created
    """
    if required:
        positional: ast.expr = make_constant(...)
    else:
        positional = make_constant(default)
    keywords: list[ast.keyword] = []
    if alias:
        keywords.append(ast.keyword(arg="alias", value=make_constant(alias)))
    return ast.Call(func=make_name(factory), args=[positional], keywords=keywords)


def _ref_to_ast(expr: str) -> ast.expr:
    """Resolve a ``$result.field``/``$path.name`` ref or literal to an AST expr.

    Args:
        expr: A string that is either a ``$ref`` expression or a literal value.

    Returns:
        - ``$result.x.y`` -> ``result.x.y`` (ast.Attribute chain)
        - ``$path.id``    -> ``id`` (ast.Name)
        - anything else   -> ast.Constant(expr)

    Sig: 2026-05-07 created
    """
    if not isinstance(expr, str) or not expr.startswith("$"):
        return make_constant(expr)
    body = expr[1:]
    parts = body.split(".")
    if not parts:
        return make_constant(expr)
    head = parts[0]
    if head == "path":
        if len(parts) >= 2:
            return make_name(parts[1])
        return make_constant(expr)
    node: ast.expr = make_name(head)
    for attr in parts[1:]:
        node = ast.Attribute(value=node, attr=attr, ctx=ast.Load())
    return node


def _ref_to_str_ast(expr: str) -> ast.expr:
    """Coerce a ref/literal to a ``str(...)`` call when it resolves to non-str.

    ``$`` refs are wrapped in ``str(...)`` because header/cookie values must be
    strings; plain literals are passed through as ast.Constant unchanged.

    Sig: 2026-05-07 created
    """
    if isinstance(expr, str) and expr.startswith("$"):
        return ast.Call(
            func=make_name("str"),
            args=[_ref_to_ast(expr)],
            keywords=[],
        )
    return make_constant(expr)


def _file_param_arg(fp: FileParamNode) -> tuple[ast.arg, ast.expr]:
    """Build (arg, default) for an UploadFile parameter.

    Sig: 2026-05-07 created
    """
    if fp.multiple:
        annotation: ast.expr = ast.Subscript(
            value=make_name("list"),
            slice=make_name("UploadFile"),
            ctx=ast.Load(),
        )
    else:
        annotation = make_name("UploadFile")
    default = ast.Call(
        func=make_name("File"),
        args=[make_constant(...)],
        keywords=[],
    )
    return ast.arg(arg=fp.name, annotation=annotation), default


def _route_param_arg(rp: RouteParamNode) -> tuple[ast.arg, ast.expr]:
    """Build (arg, default) for an extended RouteParamNode (form/header/cookie/query).

    Sig: 2026-05-07 created
    """
    annotation_name = _scalar_annotation(rp.field_type)
    annotation: ast.expr = make_name(annotation_name)
    if rp.optional:
        annotation = ast.Subscript(
            value=make_name("Optional"),
            slice=annotation,
            ctx=ast.Load(),
        )
    factory = _PARAM_SOURCE_FACTORIES.get(rp.source, "Query")
    required = not rp.optional and rp.default is None
    default_expr = _param_factory_call(factory, rp.default, required, rp.alias)
    return ast.arg(arg=rp.name, annotation=annotation), default_expr


def _depends_handler(handler: str) -> ast.expr:
    """Build ``Depends(handler_last_segment)`` for a dotted handler path.

    Sig: 2026-05-07 created
    """
    last = handler.rsplit(".", 1)[-1] if handler else "dependency"
    return ast.Call(
        func=make_name("Depends"),
        args=[make_name(last)],
        keywords=[],
    )


def _background_task_stmt(task: BackgroundTaskRef) -> ast.stmt:
    """Emit ``background_tasks.add_task(handler, **kwargs)``.

    Sig: 2026-05-07 created
    """
    handler_name = task.handler.rsplit(".", 1)[-1] if task.handler else "task"
    keywords = [
        ast.keyword(arg=k, value=_ref_to_ast(v))
        for k, v in task.args.items()
    ]
    call = ast.Call(
        func=ast.Attribute(
            value=make_name("background_tasks"),
            attr="add_task",
            ctx=ast.Load(),
        ),
        args=[make_name(handler_name)],
        keywords=keywords,
    )
    return ast.Expr(value=call)


def _response_header_stmt(hdr: ResponseHeaderNode) -> ast.stmt:
    """Emit ``response.headers["Name"] = value``.

    Sig: 2026-05-07 created
    """
    target = ast.Subscript(
        value=ast.Attribute(
            value=make_name("response"),
            attr="headers",
            ctx=ast.Load(),
        ),
        slice=make_constant(hdr.name),
        ctx=ast.Store(),
    )
    return ast.Assign(targets=[target], value=_ref_to_str_ast(hdr.value))


def _cookie_stmt(cookie: CookieNode) -> ast.stmt:
    """Emit ``response.set_cookie(key=..., value=..., ...)``.

    Sig: 2026-05-07 created
    """
    keywords: list[ast.keyword] = [
        ast.keyword(arg="key", value=make_constant(cookie.key)),
        ast.keyword(arg="value", value=_ref_to_str_ast(cookie.value)),
    ]
    if cookie.max_age is not None:
        keywords.append(ast.keyword(arg="max_age", value=make_constant(cookie.max_age)))
    if cookie.path != "/":
        keywords.append(ast.keyword(arg="path", value=make_constant(cookie.path)))
    if cookie.domain is not None:
        keywords.append(ast.keyword(arg="domain", value=make_constant(cookie.domain)))
    if cookie.secure:
        keywords.append(ast.keyword(arg="secure", value=make_constant(True)))
    if cookie.httponly:
        keywords.append(ast.keyword(arg="httponly", value=make_constant(True)))
    if cookie.samesite and cookie.samesite != "lax":
        keywords.append(ast.keyword(arg="samesite", value=make_constant(cookie.samesite)))
    call = ast.Call(
        func=ast.Attribute(
            value=make_name("response"),
            attr="set_cookie",
            ctx=ast.Load(),
        ),
        args=[],
        keywords=keywords,
    )
    return ast.Expr(value=call)


def _cache_header_stmts(cache: CacheConfig) -> list[ast.stmt]:
    """Emit ``response.headers[...] = ...`` statements for cache configuration.

    Args:
        cache: Cache configuration for the route.

    Returns:
        Statements that assign ``Cache-Control``, ``ETag``, and ``Vary``
        headers on the injected ``response`` parameter.

    Sig: 2026-05-07 created
    """
    stmts: list[ast.stmt] = []
    if cache.no_store:
        control_value = "no-store"
    else:
        parts: list[str] = []
        if cache.max_age:
            parts.append(f"max-age={cache.max_age}")
        parts.append("private" if cache.private else "public")
        control_value = ", ".join(parts)

    def _header_assign(name: str, value: ast.expr) -> ast.Assign:
        target = ast.Subscript(
            value=ast.Attribute(
                value=make_name("response"),
                attr="headers",
                ctx=ast.Load(),
            ),
            slice=make_constant(name),
            ctx=ast.Store(),
        )
        return ast.Assign(targets=[target], value=value)

    stmts.append(_header_assign("Cache-Control", make_constant(control_value)))

    if cache.etag and not cache.no_store:
        # response.headers["ETag"] = f'W/"{hash(str(result))}"'
        hash_call = ast.Call(
            func=make_name("hash"),
            args=[
                ast.Call(
                    func=make_name("str"),
                    args=[make_name("result")],
                    keywords=[],
                )
            ],
            keywords=[],
        )
        etag_fstring = ast.JoinedStr(
            values=[
                ast.Constant(value='W/"'),
                ast.FormattedValue(value=hash_call, conversion=-1, format_spec=None),
                ast.Constant(value='"'),
            ]
        )
        stmts.append(_header_assign("ETag", etag_fstring))

    if cache.vary and not cache.no_store:
        stmts.append(
            _header_assign("Vary", make_constant(", ".join(cache.vary)))
        )
    return stmts


def _route_decorator(route: RouteNode, response_model_expr: ast.expr | None, status_code: int) -> ast.expr:
    """Build the ``@router.method(...)`` decorator.

    Appends OpenAPI customization kwargs (``operation_id``, ``deprecated``,
    ``description``, ``include_in_schema``) when ``route.openapi_extras`` is set.

    Sig: 2026-05-07 modified
    """
    keywords: list[ast.keyword] = []
    if response_model_expr is not None:
        keywords.append(ast.keyword(arg="response_model", value=response_model_expr))
    keywords.append(ast.keyword(arg="status_code", value=make_constant(status_code)))
    extras = route.openapi_extras
    if extras is not None:
        if extras.operation_id:
            keywords.append(
                ast.keyword(arg="operation_id", value=make_constant(extras.operation_id))
            )
        if extras.deprecated:
            keywords.append(
                ast.keyword(arg="deprecated", value=make_constant(True))
            )
        if extras.description:
            keywords.append(
                ast.keyword(arg="description", value=make_constant(extras.description))
            )
        if not extras.include_in_schema:
            keywords.append(
                ast.keyword(arg="include_in_schema", value=make_constant(False))
            )
    return ast.Call(
        func=ast.Attribute(value=make_name("router"), attr=route.method.value.lower(), ctx=ast.Load()),
        args=[make_constant(route.path)],
        keywords=keywords,
    )


def _response_model_expr(route: RouteNode, action: ActionKind | None) -> ast.expr | None:
    """Determine the response_model annotation expression for a route.

    Sig: 2026-04-14 created
    """
    if action == ActionKind.DB_DELETE:
        return None
    if route.response_model:
        model_expr = make_name(route.response_model)
        if action == ActionKind.DB_LIST:
            return ast.Subscript(value=make_name("list"), slice=model_expr, ctx=ast.Load())
        return model_expr
    if route.model:
        if action == ActionKind.DB_LIST:
            return ast.Subscript(
                value=make_name("list"),
                slice=make_name(route.model),
                ctx=ast.Load(),
            )
        return make_name(route.model)
    return None


def _request_model_name(route: RouteNode) -> str | None:
    """Determine the request body model name.

    Sig: 2026-04-14 created
    """
    if route.request_model:
        return route.request_model
    if route.model and route.action in (ActionKind.DB_CREATE, ActionKind.DB_UPDATE):
        return route.model
    return None


def _collect_referenced_models(routes: list[RouteNode]) -> list[str]:
    """Collect all model names referenced by routes for imports.

    Sig: 2026-04-14 created
    """
    names: set[str] = set()
    for route in routes:
        if route.model:
            names.add(route.model)
        if route.response_model:
            names.add(route.response_model)
        if route.request_model:
            names.add(route.request_model)
        if route.pipeline:
            _collect_pipeline_models(route.pipeline, names)
    return sorted(names)


def _collect_pipeline_models(steps: list, names: set) -> None:
    """Recursively collect ``model`` references from a pipeline.

    Sig: 2026-04-24 created
    """
    from restgen.ir.nodes import (
        ForEachStepNode,
        IfStepNode,
        PipelineStepNode,
    )

    for step in steps:
        if isinstance(step, PipelineStepNode):
            if step.model:
                names.add(step.model)
        elif isinstance(step, IfStepNode):
            _collect_pipeline_models(step.then_steps, names)
            _collect_pipeline_models(step.else_steps, names)
        elif isinstance(step, ForEachStepNode):
            _collect_pipeline_models(step.body, names)


def _collect_handler_imports(routes: list[RouteNode]) -> list[tuple[str, str]]:
    """Collect (module, name) pairs for handler imports.

    Sig: 2026-04-14 created
    """
    imports: list[tuple[str, str]] = []
    seen: set[str] = set()
    for route in routes:
        if route.handler and route.handler not in seen:
            seen.add(route.handler)
            parts = route.handler.rsplit(".", 1)
            if len(parts) == 2:
                imports.append((parts[0], parts[1]))
    return imports


def _collect_new_feature_imports(routes: list[RouteNode]) -> dict[str, set[str]]:
    """Collect import requirements for all new route-level features.

    Returns:
        A dict keyed by type, with values as name sets to import:
          - ``"fastapi"``: names from fastapi (BackgroundTasks, UploadFile, ...).
          - ``"fastapi.responses"``: names from fastapi.responses (FileResponse).
          - ``"starlette.responses"``: names from starlette.responses (StreamingResponse).
          - ``"handlers"``: dotted paths whose final segment should be imported
            from the preceding module (background task handlers + depends).

    Sig: 2026-05-07 created
    """
    fastapi_names: set[str] = set()
    fastapi_response_names: set[str] = set()
    starlette_response_names: set[str] = set()
    handler_paths: set[str] = set()
    needs_optional = False

    for route in routes:
        if route.background_tasks:
            fastapi_names.add("BackgroundTasks")
            for task in route.background_tasks:
                if task.handler:
                    handler_paths.add(task.handler)
        if route.file_params:
            fastapi_names.add("UploadFile")
            fastapi_names.add("File")
        if route.response_headers or route.cookies or route.cache:
            fastapi_names.add("Response")
        if route.rate_limit:
            fastapi_names.add("Request")
        if route.depends:
            for dep in route.depends:
                if dep.handler:
                    handler_paths.add(dep.handler)
        for rp in route.params:
            if rp.source == ParamSource.FORM:
                fastapi_names.add("Form")
            elif rp.source == ParamSource.COOKIE:
                fastapi_names.add("Cookie")
            elif rp.source == ParamSource.HEADER:
                fastapi_names.add("Header")
            elif rp.source == ParamSource.QUERY:
                fastapi_names.add("Query")
            if rp.optional:
                needs_optional = True
        if route.response_type == ResponseKind.FILE:
            fastapi_response_names.add("FileResponse")
        elif route.response_type == ResponseKind.STREAMING:
            starlette_response_names.add("StreamingResponse")
        elif route.response_type == ResponseKind.HTML:
            fastapi_response_names.add("HTMLResponse")
        elif route.response_type == ResponseKind.REDIRECT:
            fastapi_response_names.add("RedirectResponse")
        elif route.response_type == ResponseKind.PLAIN:
            fastapi_response_names.add("PlainTextResponse")

    result = {
        "fastapi": fastapi_names,
        "fastapi.responses": fastapi_response_names,
        "starlette.responses": starlette_response_names,
        "handlers": handler_paths,
    }
    if needs_optional:
        result["typing"] = {"Optional"}
    return result


# ---------------------------------------------------------------------------
# CRUD body builders
# ---------------------------------------------------------------------------

def _build_list_body(route: RouteNode) -> list[ast.stmt]:
    """Build the body statements for a db.list endpoint.

    Sig: 2026-04-14 created
    """
    model = route.model or "Model"
    # Build filters dict
    filter_keys: list[ast.expr] = []
    filter_values: list[ast.expr] = []
    for f in route.filters:
        filter_keys.append(make_constant(f.field))
        filter_values.append(make_name(f.field))

    keywords: list[ast.keyword] = [
        ast.keyword(arg="skip", value=make_name("skip")),
        ast.keyword(arg="limit", value=make_name("limit")),
    ]
    if route.filters:
        keywords.append(
            ast.keyword(
                arg="filters",
                value=ast.Dict(keys=filter_keys, values=filter_values),
            )
        )

    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="list", ctx=ast.Load()),
        args=[make_name(model)],
        keywords=keywords,
    )
    return [make_return(make_await(call))]


def _build_get_body(route: RouteNode) -> list[ast.stmt]:
    """Build the body statements for a db.get endpoint.

    Sig: 2026-04-15 modified
    """
    model = route.model or "Model"
    id_param = route.path_params[0].name if route.path_params else "id"
    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="get", ctx=ast.Load()),
        args=[make_name(model), make_name(id_param)],
        keywords=[],
    )
    stmts: list[ast.stmt] = [
        make_assign("result", make_await(call)),
        _build_none_check("result", route),
        make_return(make_name("result")),
    ]
    return stmts


def _build_create_body(route: RouteNode) -> list[ast.stmt]:
    """Build the body statements for a db.create endpoint.

    Sig: 2026-04-14 created
    """
    model = route.model or "Model"
    dump_call = ast.Call(
        func=ast.Attribute(value=make_name("body"), attr="model_dump", ctx=ast.Load()),
        args=[],
        keywords=[],
    )
    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="create", ctx=ast.Load()),
        args=[make_name(model), dump_call],
        keywords=[],
    )
    return [make_return(make_await(call))]


def _build_update_body(route: RouteNode) -> list[ast.stmt]:
    """Build the body statements for a db.update endpoint.

    Sig: 2026-04-15 modified
    """
    model = route.model or "Model"
    id_param = route.path_params[0].name if route.path_params else "id"
    dump_call = ast.Call(
        func=ast.Attribute(value=make_name("body"), attr="model_dump", ctx=ast.Load()),
        args=[],
        keywords=[ast.keyword(arg="exclude_unset", value=make_constant(True))],
    )
    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="update", ctx=ast.Load()),
        args=[make_name(model), make_name(id_param), dump_call],
        keywords=[],
    )
    stmts: list[ast.stmt] = [
        make_assign("result", make_await(call)),
        _build_none_check("result", route),
        make_return(make_name("result")),
    ]
    return stmts


def _build_delete_body(route: RouteNode) -> list[ast.stmt]:
    """Build the body statements for a db.delete endpoint.

    If the route has a ``not_found`` error ref, checks the delete result
    and raises the appropriate error when the record doesn't exist.

    Sig: 2026-04-15 modified
    """
    model = route.model or "Model"
    id_param = route.path_params[0].name if route.path_params else "id"
    call = ast.Call(
        func=ast.Attribute(value=make_name("repo"), attr="delete", ctx=ast.Load()),
        args=[make_name(model), make_name(id_param)],
        keywords=[],
    )
    stmts: list[ast.stmt] = []
    if _find_error_ref(route, "not_found"):
        # deleted = await repo.delete(...); if not deleted: raise ...
        stmts.append(make_assign("deleted", make_await(call)))
        err = _find_error_ref(route, "not_found")
        if err and err.ref:
            cls = _error_class_name(err.ref)
            stmts.append(
                ast.If(
                    test=ast.UnaryOp(op=ast.Not(), operand=make_name("deleted")),
                    body=[
                        ast.Raise(
                            exc=ast.Call(func=make_name(cls), args=[], keywords=[]),
                            cause=None,
                        )
                    ],
                    orelse=[],
                )
            )
        elif err and err.status:
            detail = err.body.get("message", "Not found") if err.body else "Not found"
            stmts.append(
                ast.If(
                    test=ast.UnaryOp(op=ast.Not(), operand=make_name("deleted")),
                    body=[make_raise_http_exception(err.status, make_constant(detail))],
                    orelse=[],
                )
            )
        stmts.append(make_return(make_constant(None)))
    else:
        stmts.append(ast.Expr(value=make_await(call)))
        stmts.append(make_return(make_constant(None)))
    return stmts


_ACTION_BODY_BUILDERS = {
    ActionKind.DB_LIST: _build_list_body,
    ActionKind.DB_GET: _build_get_body,
    ActionKind.DB_CREATE: _build_create_body,
    ActionKind.DB_UPDATE: _build_update_body,
    ActionKind.DB_DELETE: _build_delete_body,
}


# ---------------------------------------------------------------------------
# Handler ref body builder
# ---------------------------------------------------------------------------

_RESPONSE_WRAPPERS: dict[ResponseKind, tuple[str, str]] = {
    ResponseKind.HTML: ("HTMLResponse", "content"),
    ResponseKind.REDIRECT: ("RedirectResponse", "url"),
    ResponseKind.PLAIN: ("PlainTextResponse", "content"),
}


def _wrap_response_return(result_expr: ast.expr, kind: ResponseKind) -> ast.expr:
    """Wrap a handler result expression in the appropriate response class.

    Args:
        result_expr: The expression whose value should be wrapped (e.g. ``result``).
        kind: The ResponseKind dictating the wrapper class.

    Returns:
        An ast.Call for the response wrapper, or ``result_expr`` unchanged
        when ``kind`` does not require wrapping.

    Sig: 2026-05-07 created
    """
    wrapper = _RESPONSE_WRAPPERS.get(kind)
    if wrapper is None:
        return result_expr
    cls_name, kwarg = wrapper
    return ast.Call(
        func=make_name(cls_name),
        args=[],
        keywords=[ast.keyword(arg=kwarg, value=result_expr)],
    )


def _build_handler_body(route: RouteNode) -> list[ast.stmt]:
    """Build the body for a handler-ref route.

    Passes path params, body, file params, form/header/cookie params,
    custom dependencies, and repo to the handler function. When the route
    declares an HTML/REDIRECT/PLAIN response type, wraps the awaited result
    in the matching ``*Response`` class.

    Sig: 2026-05-07 modified
    """
    handler_name = route.handler.rsplit(".", 1)[-1] if route.handler else "handler"
    keywords: list[ast.keyword] = []
    for pp in route.path_params:
        keywords.append(ast.keyword(arg=pp.name, value=make_name(pp.name)))
    if _request_model_name(route):
        keywords.append(ast.keyword(arg="body", value=make_name("body")))
    for fp in route.file_params:
        keywords.append(ast.keyword(arg=fp.name, value=make_name(fp.name)))
    for rp in route.params:
        if rp.source != ParamSource.PATH:
            keywords.append(ast.keyword(arg=rp.name, value=make_name(rp.name)))
    for dep in route.depends:
        if dep.as_name:
            keywords.append(ast.keyword(arg=dep.as_name, value=make_name(dep.as_name)))
    keywords.append(ast.keyword(arg="repo", value=make_name("repo")))
    call = ast.Call(
        func=make_name(handler_name),
        args=[],
        keywords=keywords,
    )
    awaited = make_await(call)
    if route.response_type in _RESPONSE_WRAPPERS:
        return [make_return(_wrap_response_return(awaited, route.response_type))]
    return [make_return(awaited)]


# ---------------------------------------------------------------------------
# Function parameter building
# ---------------------------------------------------------------------------

def _build_func_params(route: RouteNode) -> list[ast.arg]:
    """Build the function argument list for a route endpoint.

    Returns:
        List of ast.arg nodes with annotations and defaults handled via a
        parallel defaults list (returned separately via _build_func_defaults).

    Sig: 2026-04-14 created
    """
    args: list[ast.arg] = []
    for pp in route.path_params:
        args.append(ast.arg(arg=pp.name, annotation=_path_param_annotation(pp)))
    return args


def _build_func_args_and_defaults(route: RouteNode, action: ActionKind | None) -> ast.arguments:
    """Build the full ast.arguments node for a route function.

    Parameter ordering:
        1. Path params (no defaults).
        2. ``request: Request`` (if route has rate_limit — slowapi requires it).
        3. Request body (if any).
        4. File upload params.
        5. Pagination + filters (list actions only).
        6. Extended params (form/header/cookie/query via RouteParamNode).
        7. ``background_tasks: BackgroundTasks`` (if route has background tasks).
        8. ``response: Response`` (if route has headers, cookies, or cache).
        9. Custom ``Depends(...)`` dependencies.
       10. ``repo=Depends(get_repository)`` — always last.

    Sig: 2026-05-07 modified
    """
    args: list[ast.arg] = []
    defaults: list[ast.expr] = []

    # Path params (no defaults)
    for pp in route.path_params:
        args.append(ast.arg(arg=pp.name, annotation=_path_param_annotation(pp)))

    # Request param (rate-limited routes need this as the first non-path arg)
    if route.rate_limit:
        args.append(ast.arg(arg="request", annotation=make_name("Request")))

    # Body param for create/update/handler
    req_model = _request_model_name(route)
    has_alt_input = bool(route.file_params) or any(
        rp.source == ParamSource.FORM for rp in route.params
    )
    if req_model:
        args.append(ast.arg(arg="body", annotation=make_name(req_model)))
    elif route.handler and route.method in (HttpMethod.POST, HttpMethod.PUT, HttpMethod.PATCH) and not has_alt_input:
        # Handler routes with body methods may accept optional body
        # (skip when file_params or form params provide the input instead)
        args.append(
            ast.arg(
                arg="body",
                annotation=ast.Subscript(
                    value=make_name("Optional"),
                    slice=make_name(req_model or "dict"),
                    ctx=ast.Load(),
                ),
            )
        )
        defaults.append(make_constant(None))

    # File upload params
    for fp in route.file_params:
        arg_node, default = _file_param_arg(fp)
        args.append(arg_node)
        defaults.append(default)

    # Background tasks param (positional, FastAPI auto-injects — placed before defaulted params)
    if route.background_tasks:
        args.append(
            ast.arg(arg="background_tasks", annotation=make_name("BackgroundTasks"))
        )

    # Response param (positional, FastAPI auto-injects — placed before defaulted params)
    if route.response_headers or route.cookies or route.cache:
        args.append(ast.arg(arg="response", annotation=make_name("Response")))

    # Pagination params for list
    if action == ActionKind.DB_LIST:
        pg = route.pagination or PaginationConfig()
        args.append(ast.arg(arg="skip", annotation=make_name("int")))
        defaults.append(_query_default(0))
        args.append(ast.arg(arg="limit", annotation=make_name("int")))
        defaults.append(_query_default(pg.default_limit))

    # Filter params for list
    if action == ActionKind.DB_LIST:
        for f in route.filters:
            args.append(
                ast.arg(
                    arg=f.field,
                    annotation=ast.Subscript(
                        value=make_name("Optional"),
                        slice=make_name("str"),
                        ctx=ast.Load(),
                    ),
                )
            )
            defaults.append(_query_default(None))

    # Extended params (form/header/cookie/query)
    for rp in route.params:
        if rp.source == ParamSource.PATH:
            continue
        arg_node, default = _route_param_arg(rp)
        args.append(arg_node)
        defaults.append(default)

    # Custom dependencies
    for dep in route.depends:
        if not dep.as_name or not dep.handler:
            continue
        args.append(ast.arg(arg=dep.as_name, annotation=None))
        defaults.append(_depends_handler(dep.handler))

    # Repo dependency (always last)
    args.append(ast.arg(arg="repo", annotation=None))
    defaults.append(_depends_repo())

    return ast.arguments(
        posonlyargs=[],
        args=args,
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=defaults,
    )


# ---------------------------------------------------------------------------
# Single route lowering
# ---------------------------------------------------------------------------

def _references_result(route: RouteNode) -> bool:
    """Return True if any feature arg/value references ``$result``.

    Sig: 2026-05-07 modified
    """
    for task in route.background_tasks:
        for v in task.args.values():
            if isinstance(v, str) and v.startswith("$result"):
                return True
    for hdr in route.response_headers:
        if isinstance(hdr.value, str) and hdr.value.startswith("$result"):
            return True
    for cookie in route.cookies:
        if isinstance(cookie.value, str) and cookie.value.startswith("$result"):
            return True
    if route.cache and route.cache.etag and not route.cache.no_store:
        return True
    return False


def _inject_side_effect_stmts(body: list[ast.stmt], route: RouteNode) -> list[ast.stmt]:
    """Insert background task, header, and cookie statements before the return.

    Keeps any statements preceding the final ``return`` intact, inserts the
    feature statements just above it, then re-appends the return. If the body
    has no trailing return, the statements are appended at the end.

    If any feature references ``$result`` and the body's trailing statement is
    a bare ``return <expr>`` (no prior ``result`` binding), rewrite it as
    ``result = <expr>; ...stmts...; return result`` so the refs resolve.

    Sig: 2026-05-07 modified
    """
    if not (
        route.background_tasks
        or route.response_headers
        or route.cookies
        or route.cache
    ):
        return body

    feature_stmts: list[ast.stmt] = []
    for hdr in route.response_headers:
        feature_stmts.append(_response_header_stmt(hdr))
    for cookie in route.cookies:
        feature_stmts.append(_cookie_stmt(cookie))
    if route.cache:
        feature_stmts.extend(_cache_header_stmts(route.cache))
    for task in route.background_tasks:
        feature_stmts.append(_background_task_stmt(task))

    if not body:
        return feature_stmts

    last = body[-1]
    if isinstance(last, ast.Return):
        needs_result = _references_result(route)
        has_result_binding = any(
            isinstance(s, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "result" for t in s.targets)
            for s in body[:-1]
        )
        if needs_result and not has_result_binding and last.value is not None \
                and not (isinstance(last.value, ast.Name) and last.value.id == "result"):
            rebind = make_assign("result", last.value)
            return [*body[:-1], rebind, *feature_stmts, make_return(make_name("result"))]
        return [*body[:-1], *feature_stmts, last]
    return [*body, *feature_stmts]


def _lower_single_route(
    route: RouteNode,
    app: AppNode,
) -> tuple[ast.AsyncFunctionDef, list[tuple[str, str]]]:
    """Lower a single RouteNode to an async function with decorators.

    Returns:
        Tuple of (function_def, extra_imports). ``extra_imports`` includes
        imports for pipeline handlers, the route handler, background task
        handlers, and custom dependency callables.

    Sig: 2026-05-07 modified
    """
    extra_imports: list[tuple[str, str]] = []
    action = route.action

    # Determine status code
    if action:
        status_code = _ACTION_STATUS.get(action, 200)
    elif route.method == HttpMethod.POST:
        status_code = 201
    else:
        status_code = 200

    resp_expr = _response_model_expr(route, action)
    decorator = _route_decorator(route, resp_expr, status_code)

    decorators: list[ast.expr] = [decorator]
    if route.rate_limit:
        decorators.append(
            ast.Call(
                func=ast.Attribute(
                    value=make_name("limiter"),
                    attr="limit",
                    ctx=ast.Load(),
                ),
                args=[make_constant(route.rate_limit.rate)],
                keywords=[],
            )
        )

    # Build function arguments
    func_args = _build_func_args_and_defaults(route, action)

    # Build function body
    if route.pipeline:
        body, pipeline_imports = lower_pipeline(route.pipeline, route, app)
        extra_imports.extend(pipeline_imports)
    elif route.handler:
        body = _build_handler_body(route)
        parts = route.handler.rsplit(".", 1)
        if len(parts) == 2:
            extra_imports.append((parts[0], parts[1]))
    elif action and action in _ACTION_BODY_BUILDERS:
        body = _ACTION_BODY_BUILDERS[action](route)
    else:
        body = [ast.Expr(value=make_constant(...))]

    # Inject new feature side-effects (background tasks, headers, cookies).
    body = _inject_side_effect_stmts(body, route)

    # Collect imports for background task and dependency handlers.
    for task in route.background_tasks:
        if task.handler:
            parts = task.handler.rsplit(".", 1)
            if len(parts) == 2:
                extra_imports.append((parts[0], parts[1]))
    for dep in route.depends:
        if dep.handler:
            parts = dep.handler.rsplit(".", 1)
            if len(parts) == 2:
                extra_imports.append((parts[0], parts[1]))

    func_name = route.name or f"route_{route.method.value.lower()}_{route.path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"

    func = make_async_func(
        name=func_name,
        args=func_args,
        body=body,
        decorators=decorators,
    )
    return func, extra_imports


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lower_routes(app: AppNode) -> ast.Module:
    """Generate routes.py AST with all endpoint functions.

    Used when the application declares no routers (legacy layout).

    Args:
        app: The root IR application node containing all routes and models.

    Returns:
        An ast.Module representing the complete routes.py file.

    Sig: 2026-04-24 modified
    """
    return _lower_routes_module(
        routes=app.routes,
        app=app,
        prefix=app.base_path,
        models_import=".models",
        deps_import=".dependencies",
        errors_import=".errors",
    )


def _deduplicate_route_names(routes: list[RouteNode]) -> None:
    """Disambiguate auto-named routes whose names collide inside a module.

    When two routes share an auto-name (e.g. both ``/`` and ``/{id}`` GET
    producing ``get_root``), suffix the later ones with ``_2``, ``_3``,
    etc. Runs in place.

    Sig: 2026-04-24 created
    """
    seen: dict[str, int] = {}
    for route in routes:
        base = route.name or "handler"
        count = seen.get(base, 0)
        if count:
            new_name = f"{base}_{count + 1}"
            while new_name in seen:
                count += 1
                new_name = f"{base}_{count + 1}"
            route.name = new_name
            seen[new_name] = 1
            seen[base] = count + 1
        else:
            seen[base] = 1


def lower_router_group(group, app: AppNode) -> ast.Module:
    """Generate one ``routers/<name>.py`` module for a RouterGroupNode.

    Each router file imports from the parent package (``..models``,
    ``..dependencies``, ``..errors``) and exposes its own ``router`` as
    ``APIRouter`` without a prefix — the prefix and tags are re-applied
    by ``app.py`` via ``include_router(prefix=..., tags=[...])``.

    Args:
        group: The RouterGroupNode to emit.
        app: Application IR (for resolving model/handler references).

    Returns:
        ast.Module for ``routers/<group.name>.py``.

    Sig: 2026-04-24 created
    """
    # Routes inside a group keep their router-relative paths. Prefix
    # application happens in app_emitter via include_router(prefix=...).
    _deduplicate_route_names(group.routes)
    return _lower_routes_module(
        routes=group.routes,
        app=app,
        prefix="",
        models_import="..models",
        deps_import="..dependencies",
        errors_import="..errors",
    )


def _lower_routes_module(
    *,
    routes: list[RouteNode],
    app: AppNode,
    prefix: str,
    models_import: str,
    deps_import: str,
    errors_import: str,
) -> ast.Module:
    """Generate a routes-bearing module for an arbitrary route list.

    Factored so the flat-layout emitter and the per-router emitter share
    every code path except for (a) import paths (``.models`` vs
    ``..models``) and (b) whether the ``APIRouter`` carries a prefix.

    Sig: 2026-04-24 created
    """
    body: list[ast.stmt] = []
    all_extra_imports: list[tuple[str, str]] = []

    model_names = _collect_referenced_models(routes)

    type_imports: dict[str, set[str]] = {}
    for route in routes:
        for param in route.path_params:
            for mod, name in collect_imports(param.field_type):
                type_imports.setdefault(mod, set()).add(name)

    error_class_names = _collect_error_imports(routes)

    for mod, names in type_imports.items():
        body.append(make_import_from(mod, sorted(names)))

    feature_imports = _collect_new_feature_imports(routes)
    fastapi_names = {"APIRouter", "Depends", "Query", "HTTPException"}
    fastapi_names.update(feature_imports.get("fastapi", set()))
    body.append(make_import_from("fastapi", sorted(fastapi_names)))

    if feature_imports.get("fastapi.responses"):
        body.append(
            make_import_from("fastapi.responses", sorted(feature_imports["fastapi.responses"]))
        )
    if feature_imports.get("starlette.responses"):
        body.append(
            make_import_from("starlette.responses", sorted(feature_imports["starlette.responses"]))
        )

    # Sibling imports: try relative first, fall back to absolute (supports
    # running from inside the generated directory as a standalone module).
    relative_stmts: list[ast.stmt] = []
    absolute_stmts: list[ast.stmt] = []
    if model_names:
        relative_stmts.append(make_import_from(models_import, model_names))
        absolute_stmts.append(make_import_from(models_import.lstrip("."), model_names))
    relative_stmts.append(make_import_from(deps_import, ["get_repository"]))
    absolute_stmts.append(make_import_from(deps_import.lstrip("."), ["get_repository"]))
    if error_class_names:
        relative_stmts.append(make_import_from(errors_import, error_class_names))
        absolute_stmts.append(make_import_from(errors_import.lstrip("."), error_class_names))

    body.append(ast.Try(
        body=relative_stmts,
        handlers=[ast.ExceptHandler(type=make_name("ImportError"), name=None, body=absolute_stmts)],
        orelse=[], finalbody=[],
    ))

    handler_imports = _collect_handler_imports(routes)

    needs_optional = (
        any((r.action == ActionKind.DB_LIST and r.filters) for r in routes)
        or "typing" in feature_imports
    )
    if needs_optional:
        body.insert(0, make_import_from("typing", ["Optional"]))

    router_keywords: list[ast.keyword] = []
    if prefix:
        router_keywords.append(ast.keyword(arg="prefix", value=make_constant(prefix)))
    router_assign = make_assign(
        "router",
        ast.Call(func=make_name("APIRouter"), args=[], keywords=router_keywords),
    )
    body.append(router_assign)

    for route in routes:
        func, extra = _lower_single_route(route, app)
        body.append(func)
        all_extra_imports.extend(extra)

    all_handler_imports = handler_imports + all_extra_imports
    seen: set[tuple[str, str]] = set()
    import_insert_idx = len([s for s in body if isinstance(s, (ast.Import, ast.ImportFrom))])
    for module, name in all_handler_imports:
        key = (module, name)
        if key not in seen:
            seen.add(key)
            body.insert(import_insert_idx, make_import_from(module, [name]))
            import_insert_idx += 1

    return make_module(body)
