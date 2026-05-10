"""Parse pass: convert raw config dict into the IR node tree.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import re
from typing import Any

from pathlib import Path

from restgen.ir.nodes import (
    MISSING,
    ActionKind,
    AppNode,
    AuthConfig,
    BackgroundTaskRef,
    CacheConfig,
    ComputedFieldNode,
    ConditionNode,
    CookieNode,
    DatabaseConfig,
    DependencyRef,
    DocsConfig,
    ErrorNode,
    ErrorRef,
    FieldConstraints,
    FieldNode,
    FileParamNode,
    FilterNode,
    ForEachStepNode,
    HealthCheckConfig,
    HttpMethod,
    IfStepNode,
    MiddlewareNode,
    ModelNode,
    MountConfig,
    NamedPipelineNode,
    OpenAPIExtras,
    PaginationConfig,
    ParamSource,
    PipelineStepNode,
    RateLimitConfig,
    ResponseHeaderNode,
    ResponseKind,
    ReturnStepNode,
    RouteNode,
    RouteParamNode,
    RouterGroupNode,
    SecuritySchemeNode,
    SecuritySchemeType,
    StreamingConfig,
    TestConfig,
    WebSocketRouteNode,
)
from restgen.schema.linker import IMPORTS_KEY, SOURCE_KEY
from restgen.ir.types import FieldType, ScalarType


# ---------------------------------------------------------------------------
# Scalar name → ScalarType mapping
# ---------------------------------------------------------------------------

_SCALAR_MAP: dict[str, ScalarType] = {s.value: s for s in ScalarType}


# ---------------------------------------------------------------------------
# ActionKind string → enum mapping
# ---------------------------------------------------------------------------

_ACTION_MAP: dict[str, ActionKind] = {a.value: a for a in ActionKind}


# ---------------------------------------------------------------------------
# HTTP method string → enum mapping
# ---------------------------------------------------------------------------

_METHOD_MAP: dict[str, HttpMethod] = {m.value: m for m in HttpMethod}

# Also accept lowercase
_METHOD_MAP.update({m.value.lower(): m for m in HttpMethod})


# ---------------------------------------------------------------------------
# Method → verb mapping for route name auto-generation
# ---------------------------------------------------------------------------

_METHOD_VERB: dict[HttpMethod, str] = {
    HttpMethod.GET: "list",
    HttpMethod.POST: "create",
    HttpMethod.PUT: "update",
    HttpMethod.PATCH: "update",
    HttpMethod.DELETE: "delete",
}

# Regex to find path parameter placeholders
_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


# =========================================================================
# Type parsing
# =========================================================================


def _parse_field_type(field_def: dict[str, Any]) -> FieldType:
    """Parse a field definition dict into a FieldType.

    Args:
        field_def: Raw field definition with at least a 'type' key.

    Returns:
        Resolved FieldType instance.

    Raises:
        ValueError: If the type specification is unrecognised.

    Sig: 2026-04-14 created
    """
    raw_type = str(field_def.get("type", "any")).lower()
    is_optional = field_def.get("optional", False)

    if raw_type == "ref":
        return FieldType(ref=field_def["model"], optional=is_optional)

    if raw_type == "enum":
        values = [str(v) for v in field_def["values"]]
        return FieldType(enum_values=values, optional=is_optional)

    if raw_type == "list":
        inner = _parse_inner_type(field_def.get("items", "any"))
        return FieldType(list_of=inner, optional=is_optional)

    if raw_type == "dict":
        key_type = _parse_inner_type(field_def.get("keys", "str"))
        val_type = _parse_inner_type(field_def.get("values", "any"))
        return FieldType(dict_of=(key_type, val_type), optional=is_optional)

    # Scalar
    scalar = _SCALAR_MAP.get(raw_type)
    if scalar is None:
        raise ValueError(f"Unknown type: {raw_type!r}")
    return FieldType(scalar=scalar, optional=is_optional)


def _parse_inner_type(type_spec: str | dict[str, Any]) -> FieldType:
    """Parse an inner type spec (used inside list/dict).

    Args:
        type_spec: Either a scalar name string or a nested field def dict.

    Returns:
        Resolved FieldType instance.

    Sig: 2026-04-14 created
    """
    if isinstance(type_spec, dict):
        return _parse_field_type(type_spec)
    name = str(type_spec).lower()
    scalar = _SCALAR_MAP.get(name)
    if scalar is not None:
        return FieldType(scalar=scalar)
    # Treat as ref
    return FieldType(ref=str(type_spec))


# =========================================================================
# Constraint parsing
# =========================================================================

_CONSTRAINT_KEYS = frozenset(
    ("min_length", "max_length", "ge", "le", "gt", "lt", "regex", "multiple_of")
)


def _parse_constraints(field_def: dict[str, Any]) -> FieldConstraints | None:
    """Extract FieldConstraints from a field definition dict.

    Args:
        field_def: Raw field definition.

    Returns:
        FieldConstraints if any constraint keys are present, else None.

    Sig: 2026-04-14 created
    """
    kwargs = {k: field_def[k] for k in _CONSTRAINT_KEYS if k in field_def}
    if not kwargs:
        return None
    return FieldConstraints(**kwargs)


# =========================================================================
# Field parsing
# =========================================================================


def _parse_field(name: str, field_def: dict[str, Any]) -> FieldNode:
    """Parse a single field definition into a FieldNode.

    Args:
        name: Field name.
        field_def: Raw field definition dict.

    Returns:
        Populated FieldNode.

    Sig: 2026-04-14 created
    """
    ft = _parse_field_type(field_def)
    constraints = _parse_constraints(field_def)

    # Handle 'auto_now' as alias for auto on datetime fields
    auto = field_def.get("auto", False) or field_def.get("auto_now", False)

    default = field_def.get("default", MISSING)

    # If type is enum, store enum values on the FieldNode as well
    enum_values = None
    if ft.enum_values is not None:
        enum_values = ft.enum_values

    return FieldNode(
        name=name,
        field_type=ft,
        primary=field_def.get("primary", False),
        auto=auto,
        optional=field_def.get("optional", False),
        unique=field_def.get("unique", False),
        default=default,
        enum=enum_values,
        format=field_def.get("format"),
        constraints=constraints,
        description=field_def.get("description"),
    )


# =========================================================================
# Model parsing
# =========================================================================


def _parse_computed_fields(
    computed: dict[str, dict[str, Any]],
) -> list[ComputedFieldNode]:
    """Parse computed field definitions.

    Args:
        computed: Mapping of field name to computed field config.

    Returns:
        List of ComputedFieldNode instances.

    Sig: 2026-04-14 created
    """
    result: list[ComputedFieldNode] = []
    for name, defn in computed.items():
        ft = _parse_field_type(defn)
        result.append(
            ComputedFieldNode(
                name=name,
                field_type=ft,
                handler=defn.get("handler", ""),
            )
        )
    return result


def _parse_model(name: str, model_def: dict[str, Any]) -> ModelNode:
    """Parse a single model definition into a ModelNode.

    Args:
        name: Model name.
        model_def: Raw model definition dict.

    Returns:
        Populated ModelNode.

    Sig: 2026-04-14 created
    """
    fields: list[FieldNode] = []
    raw_fields = model_def.get("fields", {})
    for fname, fdef in raw_fields.items():
        if isinstance(fdef, dict):
            fields.append(_parse_field(fname, fdef))
        else:
            # Shorthand: field_name: type_string
            fields.append(_parse_field(fname, {"type": fdef}))

    computed = _parse_computed_fields(model_def.get("computed", {}))

    base = model_def.get("base")
    is_derived = base is not None

    include = model_def.get("include")
    exclude = model_def.get("exclude")
    overrides = model_def.get("overrides")
    mixins = model_def.get("mixins", [])

    return ModelNode(
        name=name,
        fields=fields,
        computed_fields=computed,
        base=base,
        include=include,
        exclude=exclude,
        all_optional=model_def.get("all_optional", False),
        overrides=overrides,
        description=model_def.get("description"),
        table_name=model_def.get("table_name"),
        is_derived=is_derived,
        mixins=mixins,
    )


def _parse_models(
    raw: dict[str, dict[str, Any]],
) -> tuple[list[ModelNode], dict[str, ModelNode]]:
    """Parse all model definitions and build an index.

    Args:
        raw: Mapping of model name to model config dict.

    Returns:
        Tuple of (models list, model_index dict).

    Sig: 2026-04-14 created
    """
    models: list[ModelNode] = []
    index: dict[str, ModelNode] = {}
    for name, defn in raw.items():
        node = _parse_model(name, defn)
        models.append(node)
        index[name] = node
    return models, index


# =========================================================================
# Error parsing
# =========================================================================


def _parse_error(name: str, err_def: dict[str, Any]) -> ErrorNode:
    """Parse a global error definition.

    Args:
        name: Error name.
        err_def: Raw error definition dict.

    Returns:
        Populated ErrorNode.

    Sig: 2026-04-14 created
    """
    return ErrorNode(
        name=name,
        status=err_def.get("status", 500),
        body=err_def.get("body", {}),
        description=err_def.get("description"),
    )


def _parse_errors(
    raw: dict[str, dict[str, Any]],
) -> tuple[list[ErrorNode], dict[str, ErrorNode]]:
    """Parse all error definitions and build an index.

    Args:
        raw: Mapping of error name to error config dict.

    Returns:
        Tuple of (errors list, error_index dict).

    Sig: 2026-04-14 created
    """
    errors: list[ErrorNode] = []
    index: dict[str, ErrorNode] = {}
    for name, defn in raw.items():
        node = _parse_error(name, defn)
        errors.append(node)
        index[name] = node
    return errors, index


# =========================================================================
# Security parsing
# =========================================================================


_SECURITY_TYPE_MAP: dict[str, SecuritySchemeType] = {
    "oauth2": SecuritySchemeType.OAUTH2,
    "apikey": SecuritySchemeType.APIKEY,
    "api_key": SecuritySchemeType.APIKEY,
    "basic": SecuritySchemeType.BASIC,
    "http_basic": SecuritySchemeType.BASIC,
}


def _parse_security_scheme(
    name: str, scheme_def: dict[str, Any],
) -> SecuritySchemeNode:
    """Parse a single security scheme definition.

    Args:
        name: Scheme identifier.
        scheme_def: Raw scheme config dict.

    Returns:
        Populated SecuritySchemeNode.

    Sig: 2026-05-07 created
    """
    type_str = str(scheme_def.get("type", "oauth2")).lower()
    scheme_type = _SECURITY_TYPE_MAP.get(type_str, SecuritySchemeType.OAUTH2)

    return SecuritySchemeNode(
        name=name,
        scheme_type=scheme_type,
        flow=str(scheme_def.get("flow", "password")),
        token_url=str(scheme_def.get("token_url", "/auth/token")),
        verify_handler=str(scheme_def.get("verify_handler", "")),
        location=str(scheme_def.get("location", "header")),
        header_name=str(scheme_def.get("name", "Authorization")),
        scopes=scheme_def.get("scopes", {}),
    )


def _parse_security(
    raw: dict[str, dict[str, Any]],
) -> tuple[list[SecuritySchemeNode], dict[str, SecuritySchemeNode]]:
    """Parse all security scheme definitions.

    Args:
        raw: Mapping of scheme name to scheme config.

    Returns:
        Tuple of (schemes list, scheme_index dict).

    Sig: 2026-05-07 created
    """
    schemes: list[SecuritySchemeNode] = []
    index: dict[str, SecuritySchemeNode] = {}
    for name, defn in raw.items():
        node = _parse_security_scheme(name, defn)
        schemes.append(node)
        index[name] = node
    return schemes, index


# =========================================================================
# Background task parsing
# =========================================================================


def _parse_background_tasks(raw: list[dict[str, Any]]) -> list[BackgroundTaskRef]:
    """Parse route-level background task definitions.

    Args:
        raw: List of background task config dicts.

    Returns:
        List of BackgroundTaskRef instances.

    Sig: 2026-05-07 created
    """
    tasks: list[BackgroundTaskRef] = []
    for item in raw:
        if isinstance(item, str):
            tasks.append(BackgroundTaskRef(handler=item))
        elif isinstance(item, dict):
            tasks.append(
                BackgroundTaskRef(
                    handler=str(item.get("handler", "")),
                    args=item.get("args", {}),
                )
            )
    return tasks


# =========================================================================
# File param parsing
# =========================================================================


def _parse_file_params(raw: list[dict[str, Any]]) -> list[FileParamNode]:
    """Parse route-level file upload parameter definitions.

    Args:
        raw: List of file param config dicts.

    Returns:
        List of FileParamNode instances.

    Sig: 2026-05-07 created
    """
    params: list[FileParamNode] = []
    for item in raw:
        if isinstance(item, str):
            params.append(FileParamNode(name=item))
        elif isinstance(item, dict):
            accept = item.get("accept", [])
            if isinstance(accept, str):
                accept = [accept]
            params.append(
                FileParamNode(
                    name=str(item.get("name", "file")),
                    multiple=bool(item.get("multiple", False)),
                    max_size=item.get("max_size"),
                    accept=accept,
                    description=item.get("description"),
                )
            )
    return params


# =========================================================================
# Response headers / cookies parsing
# =========================================================================


def _parse_response_headers(raw: dict[str, str]) -> list[ResponseHeaderNode]:
    """Parse route-level response header definitions.

    Args:
        raw: Mapping of header name to value (static or $ref).

    Returns:
        List of ResponseHeaderNode instances.

    Sig: 2026-05-07 created
    """
    headers: list[ResponseHeaderNode] = []
    for name, value in raw.items():
        headers.append(ResponseHeaderNode(name=name, value=str(value)))
    return headers


def _parse_cookies(raw: dict[str, Any] | list[dict[str, Any]]) -> list[CookieNode]:
    """Parse route-level cookie definitions.

    Args:
        raw: Mapping of cookie key to value, or list of cookie config dicts.

    Returns:
        List of CookieNode instances.

    Sig: 2026-05-07 created
    """
    cookies: list[CookieNode] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                cookies.append(
                    CookieNode(
                        key=key,
                        value=str(value.get("value", "")),
                        max_age=value.get("max_age"),
                        path=str(value.get("path", "/")),
                        domain=value.get("domain"),
                        secure=bool(value.get("secure", False)),
                        httponly=bool(value.get("httponly", False)),
                        samesite=str(value.get("samesite", "lax")),
                    )
                )
            else:
                cookies.append(CookieNode(key=key, value=str(value)))
    elif isinstance(raw, list):
        for item in raw:
            cookies.append(
                CookieNode(
                    key=str(item.get("key", "")),
                    value=str(item.get("value", "")),
                    max_age=item.get("max_age"),
                    path=str(item.get("path", "/")),
                    domain=item.get("domain"),
                    secure=bool(item.get("secure", False)),
                    httponly=bool(item.get("httponly", False)),
                    samesite=str(item.get("samesite", "lax")),
                )
            )
    return cookies


# =========================================================================
# Extended params parsing (form/cookie/header)
# =========================================================================


_PARAM_SOURCE_MAP: dict[str, ParamSource] = {
    "query": ParamSource.QUERY,
    "header": ParamSource.HEADER,
    "cookie": ParamSource.COOKIE,
    "form": ParamSource.FORM,
    "path": ParamSource.PATH,
}


def _parse_route_params(raw: list[dict[str, Any]]) -> list[RouteParamNode]:
    """Parse route-level extended parameter definitions.

    Args:
        raw: List of param config dicts with source field.

    Returns:
        List of RouteParamNode instances.

    Sig: 2026-05-07 created
    """
    params: list[RouteParamNode] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source_str = str(item.get("source", "query")).lower()
        source = _PARAM_SOURCE_MAP.get(source_str, ParamSource.QUERY)
        ft = _parse_field_type(item) if "type" in item else FieldType(scalar=ScalarType.STR)
        params.append(
            RouteParamNode(
                name=str(item.get("name", "")),
                source=source,
                field_type=ft,
                optional=bool(item.get("optional", False)),
                default=item.get("default"),
                alias=item.get("alias"),
                description=item.get("description"),
            )
        )
    return params


# =========================================================================
# Custom dependencies parsing
# =========================================================================


def _parse_depends(raw: list[Any]) -> list[DependencyRef]:
    """Parse route-level custom dependency definitions.

    Args:
        raw: List of dotted handler paths or config dicts.

    Returns:
        List of DependencyRef instances.

    Sig: 2026-05-07 created
    """
    deps: list[DependencyRef] = []
    for item in raw:
        if isinstance(item, str):
            func_name = item.rsplit(".", 1)[-1]
            deps.append(DependencyRef(handler=item, as_name=func_name))
        elif isinstance(item, dict):
            handler = str(item.get("handler", ""))
            as_name = str(item.get("as", handler.rsplit(".", 1)[-1]))
            deps.append(DependencyRef(handler=handler, as_name=as_name))
    return deps


# =========================================================================
# Response type parsing
# =========================================================================


_RESPONSE_KIND_MAP: dict[str, ResponseKind] = {
    "json": ResponseKind.JSON,
    "file": ResponseKind.FILE,
    "streaming": ResponseKind.STREAMING,
    "stream": ResponseKind.STREAMING,
    "html": ResponseKind.HTML,
    "redirect": ResponseKind.REDIRECT,
    "plain": ResponseKind.PLAIN,
    "text": ResponseKind.PLAIN,
}


# =========================================================================
# Tier B+C parsing: WebSocket, Health, Testing, Cache, Rate Limit, OpenAPI, Mounts
# =========================================================================


def _parse_websocket_route(
    ws_def: dict[str, Any],
    model_index: dict[str, "ModelNode"],
) -> WebSocketRouteNode:
    """Parse a single WebSocket route definition.

    Args:
        ws_def: Raw WebSocket route config dict.
        model_index: Model index for path param type inference.

    Returns:
        Populated WebSocketRouteNode.

    Sig: 2026-05-07 created
    """
    path = ws_def.get("path", "/ws")
    path_params = _extract_path_params(path, model_index, None)
    depends: list[DependencyRef] = []
    if "depends" in ws_def:
        depends = _parse_depends(ws_def["depends"])

    return WebSocketRouteNode(
        path=path,
        name=ws_def.get("name", ""),
        handler=str(ws_def.get("handler", "")),
        on_connect=ws_def.get("on_connect"),
        on_disconnect=ws_def.get("on_disconnect"),
        auth=ws_def.get("auth"),
        depends=depends,
        path_params=path_params,
    )


def _parse_health_check(raw: dict[str, Any] | bool) -> HealthCheckConfig:
    """Parse health check configuration.

    Args:
        raw: True for defaults, or a dict with overrides.

    Returns:
        HealthCheckConfig instance.

    Sig: 2026-05-07 created
    """
    if raw is True:
        return HealthCheckConfig()
    if isinstance(raw, dict):
        custom_checks = raw.get("custom_checks", [])
        if isinstance(custom_checks, str):
            custom_checks = [custom_checks]
        return HealthCheckConfig(
            enabled=raw.get("enabled", True),
            path=str(raw.get("path", "/health")),
            ready_path=str(raw.get("ready_path", "/ready")),
            include_db=bool(raw.get("include_db", True)),
            custom_checks=custom_checks,
        )
    return HealthCheckConfig()


def _parse_test_config(raw: dict[str, Any] | bool) -> TestConfig:
    """Parse test generation configuration.

    Args:
        raw: True for defaults, or a dict with overrides.

    Returns:
        TestConfig instance.

    Sig: 2026-05-07 created
    """
    if raw is True:
        return TestConfig()
    if isinstance(raw, dict):
        return TestConfig(
            generate=raw.get("generate", True),
            framework=str(raw.get("framework", "pytest")),
            async_mode=str(raw.get("async_mode", "anyio")),
        )
    return TestConfig()


def _parse_cache_config(raw: dict[str, Any] | int) -> CacheConfig:
    """Parse route-level cache configuration.

    Args:
        raw: Integer (max_age shorthand) or dict with full config.

    Returns:
        CacheConfig instance.

    Sig: 2026-05-07 created
    """
    if isinstance(raw, (int, float)):
        return CacheConfig(max_age=int(raw))
    if isinstance(raw, dict):
        vary = raw.get("vary", [])
        if isinstance(vary, str):
            vary = [vary]
        return CacheConfig(
            max_age=int(raw.get("max_age", 0)),
            private=bool(raw.get("private", True)),
            no_store=bool(raw.get("no_store", False)),
            etag=bool(raw.get("etag", False)),
            vary=vary,
        )
    return CacheConfig()


def _parse_rate_limit(raw: dict[str, Any] | str) -> RateLimitConfig:
    """Parse route-level rate limit configuration.

    Args:
        raw: Rate string shorthand or dict with config.

    Returns:
        RateLimitConfig instance.

    Sig: 2026-05-07 created
    """
    if isinstance(raw, str):
        return RateLimitConfig(rate=raw)
    if isinstance(raw, dict):
        return RateLimitConfig(
            rate=str(raw.get("rate", "10/minute")),
            key_func=raw.get("key_func"),
        )
    return RateLimitConfig()


def _parse_openapi_extras(raw: dict[str, Any]) -> OpenAPIExtras:
    """Parse route-level OpenAPI customization.

    Args:
        raw: Dict with OpenAPI fields.

    Returns:
        OpenAPIExtras instance.

    Sig: 2026-05-07 created
    """
    return OpenAPIExtras(
        operation_id=raw.get("operation_id"),
        deprecated=bool(raw.get("deprecated", False)),
        description=raw.get("description"),
        examples=raw.get("examples", {}),
        include_in_schema=bool(raw.get("include_in_schema", True)),
    )


def _parse_mounts(raw: list[dict[str, Any]]) -> list[MountConfig]:
    """Parse sub-application mount definitions.

    Args:
        raw: List of mount config dicts.

    Returns:
        List of MountConfig instances.

    Sig: 2026-05-07 created
    """
    mounts: list[MountConfig] = []
    for item in raw:
        if isinstance(item, dict):
            mounts.append(
                MountConfig(
                    path=str(item.get("path", "")),
                    app_module=str(item.get("app", item.get("app_module", ""))),
                    name=item.get("name"),
                )
            )
    return mounts


def _parse_streaming_config(raw: dict[str, Any]) -> StreamingConfig:
    """Parse streaming response configuration.

    Args:
        raw: Dict with media_type and chunk_size.

    Returns:
        StreamingConfig instance.

    Sig: 2026-05-07 created
    """
    return StreamingConfig(
        media_type=str(raw.get("media_type", "text/event-stream")),
        chunk_size=raw.get("chunk_size"),
    )


def _parse_docs_config(raw: dict[str, Any] | bool) -> DocsConfig:
    """Parse documentation endpoint configuration.

    Args:
        raw: False to disable all docs, or a dict with url overrides.

    Returns:
        DocsConfig instance.

    Sig: 2026-05-08 created
    """
    if raw is False:
        return DocsConfig(enabled=False, docs_url=None, redoc_url=None, openapi_url=None)
    if raw is True:
        return DocsConfig()
    if isinstance(raw, dict):
        enabled = raw.get("enabled", True)
        if not enabled:
            return DocsConfig(enabled=False, docs_url=None, redoc_url=None, openapi_url=None)
        return DocsConfig(
            enabled=True,
            docs_url=raw.get("docs_url", "/docs"),
            redoc_url=raw.get("redoc_url", "/redoc"),
            openapi_url=raw.get("openapi_url", "/openapi.json"),
        )
    return DocsConfig()


# =========================================================================
# Route parsing helpers
# =========================================================================


def _parse_route_errors(raw_errors: dict[str, Any]) -> list[ErrorRef]:
    """Parse route-level error references.

    Args:
        raw_errors: Mapping of condition name to error ref string or inline dict.

    Returns:
        List of ErrorRef instances.

    Sig: 2026-04-14 created
    """
    refs: list[ErrorRef] = []
    for condition, defn in raw_errors.items():
        if isinstance(defn, str):
            refs.append(ErrorRef(ref=defn, condition=condition))
        elif isinstance(defn, dict):
            refs.append(
                ErrorRef(
                    status=defn.get("status"),
                    body=defn.get("body"),
                    condition=condition,
                )
            )
        else:
            refs.append(ErrorRef(ref=str(defn), condition=condition))
    return refs


def _parse_filters(raw_filters: list[Any]) -> list[FilterNode]:
    """Parse route filter definitions.

    Args:
        raw_filters: List of filter specs (strings or dicts).

    Returns:
        List of FilterNode instances.

    Sig: 2026-04-14 created
    """
    result: list[FilterNode] = []
    for item in raw_filters:
        if isinstance(item, str):
            result.append(FilterNode(field=item, op="eq"))
        elif isinstance(item, dict):
            result.append(
                FilterNode(field=item["field"], op=item.get("op", "eq"))
            )
    return result


def _parse_pagination(raw: bool | dict[str, Any]) -> PaginationConfig:
    """Parse pagination config.

    Args:
        raw: True for defaults, or a dict with overrides.

    Returns:
        PaginationConfig instance.

    Sig: 2026-04-14 created
    """
    if raw is True:
        return PaginationConfig()
    if isinstance(raw, dict):
        return PaginationConfig(
            enabled=raw.get("enabled", True),
            default_limit=raw.get("default_limit", 20),
            max_limit=raw.get("max_limit", 100),
        )
    return PaginationConfig()


def _parse_condition(raw: dict[str, Any]) -> ConditionNode:
    """Parse a condition dict into a ConditionNode tree.

    Args:
        raw: Raw condition dict (see ConditionNode docstring for shape).

    Returns:
        Populated ConditionNode.

    Sig: 2026-04-24 created
    """
    if not isinstance(raw, dict):
        return ConditionNode()

    if "when_handler" in raw:
        captures = raw.get("captures", []) or []
        if isinstance(captures, str):
            captures = [captures]
        return ConditionNode(
            when_handler=str(raw["when_handler"]),
            captures=[str(c) for c in captures],
        )

    op = raw.get("op")
    children_raw = raw.get("children", [])
    children = [_parse_condition(c) for c in children_raw] if children_raw else []
    return ConditionNode(
        op=str(op) if op is not None else None,
        left=raw.get("left"),
        right=raw.get("right"),
        children=children,
    )


def _parse_pipeline(
    raw_steps: list[Any],
) -> list[Any]:
    """Parse pipeline step definitions (including control flow steps).

    Dispatches on keys:
        - ``if``       -> IfStepNode
        - ``for_each`` -> ForEachStepNode
        - ``return``   -> ReturnStepNode
        - default      -> PipelineStepNode (action/handler step)

    Args:
        raw_steps: List of pipeline step dicts.

    Returns:
        Heterogeneous list of step nodes.

    Sig: 2026-04-24 modified
    """
    steps: list[Any] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        if "if" in step:
            steps.append(_parse_if_step(step["if"]))
        elif "for_each" in step:
            steps.append(_parse_for_each_step(step["for_each"]))
        elif "return" in step:
            steps.append(_parse_return_step(step["return"]))
        else:
            action_str = step.get("action", "")
            action = _ACTION_MAP.get(action_str, ActionKind.SIDE_EFFECT)
            steps.append(
                PipelineStepNode(
                    action=action,
                    handler=step.get("handler"),
                    args=step.get("args", {}),
                    as_name=step.get("as"),
                    model=step.get("model"),
                )
            )
    return steps


def _parse_if_step(raw: dict[str, Any]) -> IfStepNode:
    """Parse an ``if:`` step definition.

    Args:
        raw: Dict with ``when`` (condition), ``then`` (steps), ``else`` (steps).

    Sig: 2026-04-24 created
    """
    condition = _parse_condition(raw.get("when", {}))
    then_steps = _parse_pipeline(raw.get("then", []))
    else_steps = _parse_pipeline(raw.get("else", []))
    return IfStepNode(
        condition=condition,
        then_steps=then_steps,
        else_steps=else_steps,
        as_name=raw.get("as"),
    )


def _parse_for_each_step(raw: dict[str, Any]) -> ForEachStepNode:
    """Parse a ``for_each:`` step definition.

    Args:
        raw: Dict with ``in`` (iterable ref), ``as`` (loop var), ``body``.

    Sig: 2026-04-24 created
    """
    return ForEachStepNode(
        iterable_ref=str(raw.get("in", "")),
        loop_var=str(raw.get("as", "item")),
        body=_parse_pipeline(raw.get("body", [])),
        as_name=raw.get("collect"),
    )


def _parse_return_step(raw: dict[str, Any] | str | None) -> ReturnStepNode:
    """Parse a ``return:`` step definition.

    Accepts a shorthand string (``return: $order``) or a dict with
    ``value`` / ``status`` / ``when``.

    Sig: 2026-04-24 created
    """
    if raw is None:
        return ReturnStepNode()
    if isinstance(raw, str):
        return ReturnStepNode(value_ref=raw)
    condition = None
    if "when" in raw:
        condition = _parse_condition(raw["when"])
    value = raw.get("value")
    return ReturnStepNode(
        value_ref=str(value) if value is not None else None,
        status=raw.get("status"),
        condition=condition,
    )


def _extract_path_params(
    path: str,
    model_index: dict[str, ModelNode],
    route_model: str | None,
) -> list[FieldNode]:
    """Extract path parameters from a URL path pattern.

    Args:
        path: URL path pattern containing {param} placeholders.
        model_index: Model index for looking up primary key types.
        route_model: Name of the route's primary model, if any.

    Returns:
        List of FieldNode instances for each path parameter.

    Sig: 2026-04-14 created
    """
    params: list[FieldNode] = []
    for match in _PATH_PARAM_RE.finditer(path):
        param_name = match.group(1)
        # Try to infer type from model's primary key
        field_type = FieldType(scalar=ScalarType.STR)
        if route_model and route_model in model_index:
            model = model_index[route_model]
            for f in model.fields:
                if f.primary and f.name == param_name:
                    field_type = f.field_type
                    break
        params.append(FieldNode(name=param_name, field_type=field_type))
    return params


def _singularize(word: str) -> str:
    """Naive singularization: strip trailing 's'.

    Args:
        word: Word to singularize.

    Returns:
        Singularized word.

    Sig: 2026-04-14 created
    """
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("ses"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _auto_route_name(path: str, method: HttpMethod) -> str:
    """Generate a route name from the path and HTTP method.

    Args:
        path: URL path pattern.
        method: HTTP method.

    Returns:
        Generated route name string.

    Sig: 2026-04-14 created
    """
    # Split path into segments, ignoring params
    segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    if not segments:
        return f"{method.value.lower()}_root"

    last_segment = segments[-1]
    has_trailing_param = path.rstrip("/").endswith("}")

    verb = _METHOD_VERB.get(method, method.value.lower())

    if method == HttpMethod.GET:
        if has_trailing_param:
            # GET /users/{id} → get_user
            return f"get_{_singularize(last_segment)}"
        else:
            # GET /users → list_users
            return f"list_{last_segment}"

    if method in (HttpMethod.PUT, HttpMethod.PATCH, HttpMethod.DELETE):
        return f"{verb}_{_singularize(last_segment)}"

    if method == HttpMethod.POST:
        if has_trailing_param:
            # POST /users/{id}/deactivate → handled below as custom
            pass
        else:
            # Check if last segment looks like an action (not the resource)
            if len(segments) >= 2:
                # /users/{id}/deactivate → deactivate_user
                prev_segments = [
                    s for s in segments[:-1] if not s.startswith("{")
                ]
                if prev_segments:
                    resource = _singularize(prev_segments[-1])
                    return f"{last_segment}_{resource}"
            return f"create_{_singularize(last_segment)}"

    return f"{verb}_{last_segment}"


# =========================================================================
# Route parsing
# =========================================================================


def _parse_route(
    route_def: dict[str, Any],
    model_index: dict[str, ModelNode],
) -> RouteNode:
    """Parse a single route definition into a RouteNode.

    Args:
        route_def: Raw route definition dict.
        model_index: Model index for path param type inference.

    Returns:
        Populated RouteNode.

    Sig: 2026-04-14 created
    """
    method_str = route_def.get("method", "GET").upper()
    method = _METHOD_MAP.get(method_str, HttpMethod.GET)
    path = route_def.get("path", "/")
    route_model = route_def.get("model")

    # Name: explicit or auto-generated
    name = route_def.get("name") or _auto_route_name(path, method)

    # Action tier detection
    action: ActionKind | None = None
    pipeline: list[PipelineStepNode] | None = None
    handler: str | None = route_def.get("handler")

    pipeline_ref: str | None = None
    if "action" in route_def:
        action_str = route_def["action"]
        action = _ACTION_MAP.get(action_str)
    elif "pipeline" in route_def:
        raw_pipeline = route_def["pipeline"]
        if isinstance(raw_pipeline, str):
            pipeline_ref = raw_pipeline
        else:
            pipeline = _parse_pipeline(raw_pipeline)

    # Path params
    path_params = _extract_path_params(path, model_index, route_model)

    # Filters
    filters: list[FilterNode] = []
    if "filters" in route_def:
        filters = _parse_filters(route_def["filters"])

    # Pagination
    pagination: PaginationConfig | None = None
    if "pagination" in route_def:
        pagination = _parse_pagination(route_def["pagination"])

    # Errors
    errors: list[ErrorRef] = []
    if "errors" in route_def:
        errors = _parse_route_errors(route_def["errors"])

    # Tags
    tags = route_def.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    # Background tasks
    background_tasks: list[BackgroundTaskRef] = []
    if "background_tasks" in route_def:
        background_tasks = _parse_background_tasks(route_def["background_tasks"])

    # File params
    file_params: list[FileParamNode] = []
    if "files" in route_def:
        file_params = _parse_file_params(route_def["files"])

    # Response headers
    response_headers: list[ResponseHeaderNode] = []
    if "response_headers" in route_def:
        response_headers = _parse_response_headers(route_def["response_headers"])

    # Cookies
    cookies: list[CookieNode] = []
    if "cookies" in route_def:
        cookies = _parse_cookies(route_def["cookies"])

    # Custom dependencies
    depends: list[DependencyRef] = []
    if "depends" in route_def:
        depends = _parse_depends(route_def["depends"])

    # Extended params (form/cookie/header)
    params: list[RouteParamNode] = []
    if "params" in route_def:
        params = _parse_route_params(route_def["params"])

    # Response type
    response_type = ResponseKind.JSON
    if "response_type" in route_def:
        response_type = _RESPONSE_KIND_MAP.get(
            str(route_def["response_type"]).lower(), ResponseKind.JSON
        )

    # Cache config
    cache: CacheConfig | None = None
    if "cache" in route_def:
        cache = _parse_cache_config(route_def["cache"])

    # Rate limit config
    rate_limit: RateLimitConfig | None = None
    if "rate_limit" in route_def:
        rate_limit = _parse_rate_limit(route_def["rate_limit"])

    # OpenAPI extras
    openapi_extras: OpenAPIExtras | None = None
    if "openapi" in route_def:
        openapi_extras = _parse_openapi_extras(route_def["openapi"])

    # Streaming config
    streaming: StreamingConfig | None = None
    if "streaming" in route_def:
        streaming = _parse_streaming_config(route_def["streaming"])

    return RouteNode(
        path=path,
        method=method,
        name=name,
        summary=route_def.get("summary"),
        tags=tags,
        action=action,
        pipeline=pipeline,
        handler=handler,
        request_model=route_def.get("request_model"),
        response_model=route_def.get("response_model"),
        path_params=path_params,
        query_params=[],
        filters=filters,
        errors=errors,
        auth=route_def.get("auth"),
        model=route_model,
        pagination=pagination,
        transform=route_def.get("transform"),
        pipeline_ref=pipeline_ref,
        background_tasks=background_tasks,
        file_params=file_params,
        response_headers=response_headers,
        cookies=cookies,
        depends=depends,
        response_type=response_type,
        params=params,
        cache=cache,
        rate_limit=rate_limit,
        openapi_extras=openapi_extras,
        streaming=streaming,
    )


# =========================================================================
# Named pipeline parsing
# =========================================================================


def _parse_named_pipeline(
    name: str,
    pipeline_def: dict[str, Any] | list[Any],
) -> NamedPipelineNode:
    """Parse a single named-pipeline definition.

    Args:
        name: Fully-qualified pipeline name (may include an import-alias
            prefix like ``shared.fulfill_order``).
        pipeline_def: Either a list of steps (shorthand for ``{steps: [...]}``)
            or a dict with ``steps``/``extends``/``prepend``/``override``/``append``.

    Returns:
        Populated NamedPipelineNode.

    Sig: 2026-04-24 created
    """
    if isinstance(pipeline_def, list):
        return NamedPipelineNode(name=name, steps=_parse_pipeline(pipeline_def))

    steps_raw = pipeline_def.get("steps", [])
    prepend_raw = pipeline_def.get("prepend", [])
    append_raw = pipeline_def.get("append", [])
    override_raw = pipeline_def.get("override", {})

    override_nodes: dict[str, PipelineStepNode] = {}
    for key, step_def in override_raw.items():
        parsed = _parse_pipeline([step_def])
        if parsed:
            override_nodes[key] = parsed[0]

    return NamedPipelineNode(
        name=name,
        steps=_parse_pipeline(steps_raw),
        extends=pipeline_def.get("extends"),
        prepend=_parse_pipeline(prepend_raw),
        append=_parse_pipeline(append_raw),
        override=override_nodes,
    )


def _collect_named_pipelines(
    raw: dict[str, Any],
    prefix: str = "",
    sibling_bare: dict[str, str] | None = None,
) -> list[NamedPipelineNode]:
    """Recursively collect named pipelines from a config tree.

    Walks both the current level's ``pipelines`` section and any
    ``__imports__`` subtrees produced by the link pass, prepending
    the import alias as a dotted namespace.

    Within one file, an ``extends:`` or ``pipeline:`` reference that
    names a sibling defined in the *same* file is rewritten to its
    fully-qualified form. This preserves local-only references without
    requiring the author to repeat the alias prefix. Cross-file
    references still use the ``alias.name`` dotted form.

    Args:
        raw: Config dict (possibly with ``__imports__`` from the linker).
        prefix: Accumulated import-alias prefix for dotted naming.
        sibling_bare: Reserved for recursion; maps bare names defined in
            the current file to their fully-qualified form so that
            ``extends: fetch_order`` is rewritten to
            ``extends: shared.fetch_order`` while processing
            ``shared/pipelines.yaml``.

    Returns:
        Flat list of NamedPipelineNode across all imported files.

    Sig: 2026-04-25 modified
    """
    result: list[NamedPipelineNode] = []
    pipelines_raw = raw.get("pipelines")

    # Build a local bare → full map for refs within this file.
    local_bare: dict[str, str] = {}
    if isinstance(pipelines_raw, dict):
        for local_name in pipelines_raw.keys():
            full_name = f"{prefix}{local_name}" if prefix else local_name
            local_bare[local_name] = full_name

    if isinstance(pipelines_raw, dict):
        for local_name, defn in pipelines_raw.items():
            full_name = local_bare[local_name]
            node = _parse_named_pipeline(full_name, defn)
            if node.extends and node.extends in local_bare:
                node.extends = local_bare[node.extends]
            result.append(node)

    imports = raw.get(IMPORTS_KEY, {})
    if isinstance(imports, dict):
        for alias, subtree in imports.items():
            if isinstance(subtree, dict):
                result.extend(
                    _collect_named_pipelines(subtree, prefix=f"{prefix}{alias}.")
                )

    # Also collect pipelines declared inside a router file (imported as a
    # router alias). When users declare pipelines next to their router in
    # the same YAML, `_collect_named_pipelines` has already picked them
    # up via the generic ``pipelines:`` section above.
    return result


# =========================================================================
# Router group parsing
# =========================================================================


def _parse_router_group(
    router_def: dict[str, Any],
    model_index: dict[str, ModelNode],
    source_path: Path | None = None,
) -> RouterGroupNode:
    """Parse a single router-group definition into a RouterGroupNode.

    Args:
        router_def: Raw router config dict containing ``name``, ``prefix``,
            and a list of ``routes``. Per-router ``middleware`` and
            ``tags`` are preserved on the node and applied at emission
            time.
        model_index: Model index for path-param type inference.
        source_path: Relative path the router was imported from, or
            ``None`` for inline routers declared in the root file.

    Returns:
        Populated RouterGroupNode.

    Sig: 2026-04-24 created
    """
    tags = router_def.get("tags", []) or []
    if isinstance(tags, str):
        tags = [tags]

    middleware: list[MiddlewareNode] = []
    if "middleware" in router_def:
        middleware = _parse_middleware(router_def["middleware"])

    routes: list[RouteNode] = []
    for rdef in router_def.get("routes", []) or []:
        routes.append(_parse_route(rdef, model_index))

    return RouterGroupNode(
        name=str(router_def.get("name", "")),
        prefix=str(router_def.get("prefix", "")),
        tags=[str(t) for t in tags],
        middleware=middleware,
        dependencies=[
            str(d) for d in router_def.get("dependencies", []) or []
        ],
        auth=router_def.get("auth"),
        routes=routes,
        source_path=source_path,
    )


def _collect_routers(
    raw: dict[str, Any],
    model_index: dict[str, ModelNode],
) -> list[RouterGroupNode]:
    """Collect RouterGroupNodes from the root config.

    Top-level ``routers:`` is a list; entries are either:
        - an inline dict: ``{ name, prefix, routes, ... }``
        - an import-alias string: ``users`` → looks up
          ``raw["__imports__"]["users"]`` for a file with a top-level
          ``router:`` key.

    Imported files whose top level has ``router:`` but that are NOT
    explicitly listed are NOT included — this keeps ``$import`` usable
    for pipeline-only modules without auto-registering their routers.

    Args:
        raw: Linked root config dict (may contain ``__imports__``).
        model_index: Model index for route parsing.

    Returns:
        Ordered list of RouterGroupNode.

    Sig: 2026-04-24 created
    """
    routers: list[RouterGroupNode] = []
    raw_routers = raw.get("routers")
    if not raw_routers:
        return routers

    imports = raw.get(IMPORTS_KEY, {}) or {}

    for entry in raw_routers:
        if isinstance(entry, str):
            sub = imports.get(entry)
            if not isinstance(sub, dict) or "router" not in sub:
                # Validator produces the user-facing error (E041/E021 style);
                # skip silently here to keep parse resilient.
                continue
            router_def = sub["router"]
            if not isinstance(router_def, dict):
                continue
            router_def = dict(router_def)
            if not router_def.get("name"):
                router_def["name"] = entry
            source = sub.get(SOURCE_KEY)
            source_path = Path(source) if source is not None else None
            rg = _parse_router_group(router_def, model_index, source_path)

            # Rewrite bare pipeline_refs on contained routes to the
            # fully-qualified form. A route in ``routers/orders.yaml``
            # that declares ``pipeline: detailed_order_view`` refers to
            # a pipeline that the parent registered as
            # ``orders.detailed_order_view``.
            file_pipelines = sub.get("pipelines") or {}
            if isinstance(file_pipelines, dict):
                local_names = set(file_pipelines.keys())
                for r in rg.routes:
                    if r.pipeline_ref and r.pipeline_ref in local_names:
                        r.pipeline_ref = f"{entry}.{r.pipeline_ref}"

            routers.append(rg)
        elif isinstance(entry, dict):
            routers.append(_parse_router_group(entry, model_index, None))

    # Also pick up a top-level ``router:`` block in the root file (rare,
    # mostly for single-router apps that don't need the indirection).
    if "router" in raw and isinstance(raw["router"], dict):
        routers.append(_parse_router_group(raw["router"], model_index, None))

    return routers


# =========================================================================
# Middleware parsing
# =========================================================================


def _parse_middleware(raw: list[dict[str, Any]]) -> list[MiddlewareNode]:
    """Parse middleware definitions.

    Args:
        raw: List of middleware config dicts.

    Returns:
        List of MiddlewareNode instances.

    Sig: 2026-04-14 created
    """
    result: list[MiddlewareNode] = []
    for item in raw:
        result.append(
            MiddlewareNode(
                kind=item.get("kind", ""),
                config=item.get("config", {}),
            )
        )
    return result


# =========================================================================
# Database / auth parsing
# =========================================================================


def _parse_database(raw: dict[str, Any]) -> DatabaseConfig:
    """Parse database configuration.

    Args:
        raw: Raw database config dict.

    Returns:
        DatabaseConfig instance.

    Sig: 2026-04-14 created
    """
    return DatabaseConfig(
        db_type=raw.get("type", raw.get("db_type", "")),
        url=raw.get("url"),
        options=raw.get("options", {}),
    )


def _parse_auth(raw: dict[str, Any]) -> AuthConfig:
    """Parse authentication configuration.

    Args:
        raw: Raw auth config dict.

    Returns:
        AuthConfig instance.

    Sig: 2026-04-14 created
    """
    return AuthConfig(
        provider=raw.get("provider", ""),
        config=raw.get("config", {}),
    )


# =========================================================================
# Main parse entry point
# =========================================================================


def parse(raw: dict[str, Any]) -> AppNode:
    """Convert validated config dict into IR node tree.

    Args:
        raw: Raw config dict loaded from YAML/JSON.

    Returns:
        Fully populated AppNode representing the API application.

    Sig: 2026-04-14 created
    """
    # Top-level metadata
    title = raw.get("title", raw.get("name", "API"))
    version = raw.get("version", "0.1.0")
    description = raw.get("description", "")
    base_path = raw.get("base_path", "")

    # Database
    database: DatabaseConfig | None = None
    if "database" in raw:
        database = _parse_database(raw["database"])

    # Auth
    auth: AuthConfig | None = None
    if "auth" in raw:
        auth = _parse_auth(raw["auth"])

    # Security schemes
    security_schemes: list[SecuritySchemeNode] = []
    security_index: dict[str, SecuritySchemeNode] = {}
    if "security" in raw:
        security_schemes, security_index = _parse_security(raw["security"])

    # Models
    models: list[ModelNode] = []
    model_index: dict[str, ModelNode] = {}
    if "models" in raw:
        models, model_index = _parse_models(raw["models"])

    # Errors
    errors: list[ErrorNode] = []
    error_index: dict[str, ErrorNode] = {}
    if "errors" in raw:
        errors, error_index = _parse_errors(raw["errors"])

    # Routes
    routes: list[RouteNode] = []
    if "routes" in raw:
        for rdef in raw["routes"]:
            routes.append(_parse_route(rdef, model_index))

    # Middleware
    middleware: list[MiddlewareNode] = []
    if "middleware" in raw:
        middleware = _parse_middleware(raw["middleware"])

    # Extra dependencies (user-specified pip packages)
    dependencies: list[str] = []
    if "dependencies" in raw:
        dependencies = [str(d) for d in raw["dependencies"]]

    # Named pipelines (root + recursively from $imports)
    named_pipelines = _collect_named_pipelines(raw)
    named_pipeline_index = {p.name: p for p in named_pipelines}

    # Routers (inline or imported alias). Each router's routes keep their
    # declared paths (relative to the router prefix); the emitter applies
    # the prefix via `include_router(prefix=...)`. For the flat AppNode.routes
    # list (used by legacy emit path + indexing), we append prefix-composed
    # copies so downstream passes that walk AppNode.routes see full URLs.
    from copy import deepcopy as _deepcopy

    routers = _collect_routers(raw, model_index)
    for rg in routers:
        for r in rg.routes:
            flat = _deepcopy(r)
            if rg.prefix:
                joined = rg.prefix.rstrip("/") + "/" + r.path.lstrip("/")
                flat.path = joined if joined else "/"
            if rg.tags:
                flat.tags = list({*r.tags, *rg.tags})
            # Regenerate the route name against the prefixed path so
            # multiple routers don't collide on ``get_root`` / etc.
            flat.name = _auto_route_name(flat.path, flat.method)
            routes.append(flat)

    # WebSocket routes
    websocket_routes: list[WebSocketRouteNode] = []
    if "websockets" in raw:
        for ws_def in raw["websockets"]:
            websocket_routes.append(_parse_websocket_route(ws_def, model_index))

    # Health check
    health_check: HealthCheckConfig | None = None
    if "health_check" in raw:
        health_check = _parse_health_check(raw["health_check"])

    # Test config
    test_config: TestConfig | None = None
    if "testing" in raw:
        test_config = _parse_test_config(raw["testing"])

    # Mounts
    mounts: list[MountConfig] = []
    if "mounts" in raw:
        mounts = _parse_mounts(raw["mounts"])

    # Docs configuration
    docs: DocsConfig | None = None
    if "docs" in raw:
        docs = _parse_docs_config(raw["docs"])

    return AppNode(
        title=title,
        version=version,
        description=description,
        base_path=base_path,
        database=database,
        auth=auth,
        models=models,
        routes=routes,
        errors=errors,
        middleware=middleware,
        dependencies=dependencies,
        model_index=model_index,
        error_index=error_index,
        named_pipelines=named_pipelines,
        named_pipeline_index=named_pipeline_index,
        routers=routers,
        security_schemes=security_schemes,
        security_index=security_index,
        websocket_routes=websocket_routes,
        health_check=health_check,
        test_config=test_config,
        mounts=mounts,
        docs=docs,
    )
