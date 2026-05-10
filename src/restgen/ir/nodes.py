from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from restgen.ir.types import FieldType, ScalarType


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------

class _MissingSentinel:
    """Sentinel object indicating a field has no default value.

    Sig: 2026-04-14 created
    """

    _instance: _MissingSentinel | None = None

    def __new__(cls) -> _MissingSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _MissingSentinel()


# ---------------------------------------------------------------------------
# Field-level nodes
# ---------------------------------------------------------------------------

@dataclass
class FieldConstraints:
    """Validation constraints applied to a model field.

    Args:
        min_length: Minimum string/collection length.
        max_length: Maximum string/collection length.
        ge: Greater than or equal to.
        le: Less than or equal to.
        gt: Strictly greater than.
        lt: Strictly less than.
        regex: Regular expression pattern the value must match.
        multiple_of: Numeric value must be a multiple of this.

    Sig: 2026-04-14 created
    """

    min_length: int | None = None
    max_length: int | None = None
    ge: float | None = None
    le: float | None = None
    gt: float | None = None
    lt: float | None = None
    regex: str | None = None
    multiple_of: float | None = None


@dataclass
class FieldNode:
    """A single field within a model definition.

    Args:
        name: Field identifier.
        field_type: Resolved type information.
        primary: Whether this field is the primary key.
        auto: Whether the value is auto-generated.
        optional: Whether the field is optional.
        unique: Whether the field has a uniqueness constraint.
        default: Default value; MISSING sentinel if none.
        enum: Allowed literal values.
        format: Semantic format hint (e.g. "email", "uri").
        constraints: Validation constraints.
        description: Human-readable description.

    Sig: 2026-04-14 created
    """

    name: str = ""
    field_type: FieldType = field(default_factory=FieldType)
    primary: bool = False
    auto: bool = False
    optional: bool = False
    unique: bool = False
    default: Any = MISSING
    enum: list[Any] | None = None
    format: str | None = None
    constraints: FieldConstraints | None = None
    description: str | None = None


@dataclass
class ComputedFieldNode:
    """A field whose value is derived at runtime via a handler.

    Args:
        name: Field identifier.
        field_type: Resolved type information.
        handler: Dotted path to the handler function.

    Sig: 2026-04-14 created
    """

    name: str = ""
    field_type: FieldType = field(default_factory=FieldType)
    handler: str = ""


# ---------------------------------------------------------------------------
# Model node
# ---------------------------------------------------------------------------

@dataclass
class ModelNode:
    """A data model definition in the IR.

    Args:
        name: Model identifier.
        fields: Direct field definitions.
        computed_fields: Computed/derived fields.
        base: Name of parent model for inheritance.
        include: Whitelist of fields to inherit from base.
        exclude: Blacklist of fields to exclude from base.
        all_optional: Make every field optional (for PATCH schemas).
        overrides: Per-field override dicts keyed by field name.
        description: Human-readable description.
        table_name: Database table name override.
        resolved_fields: Fields after base-class resolution.
        is_derived: Whether this model was derived from another.
        mixins: List of mixin model names to compose in.

    Sig: 2026-04-14 created
    """

    name: str = ""
    fields: list[FieldNode] = field(default_factory=list)
    computed_fields: list[ComputedFieldNode] = field(default_factory=list)
    base: str | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None
    all_optional: bool = False
    overrides: dict[str, dict] | None = None
    description: str | None = None
    table_name: str | None = None
    resolved_fields: list[FieldNode] = field(default_factory=list)
    is_derived: bool = False
    mixins: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Route-level enums and nodes
# ---------------------------------------------------------------------------

class HttpMethod(Enum):
    """HTTP methods supported by route definitions.

    Sig: 2026-04-14 created
    """

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ParamSource(Enum):
    """Source location for route parameters.

    Sig: 2026-05-07 created
    """

    QUERY = "query"
    HEADER = "header"
    COOKIE = "cookie"
    FORM = "form"
    PATH = "path"


class ResponseKind(Enum):
    """Response type for a route endpoint.

    Sig: 2026-05-07 modified
    """

    JSON = "json"
    FILE = "file"
    STREAMING = "streaming"
    HTML = "html"
    REDIRECT = "redirect"
    PLAIN = "plain"


class SecuritySchemeType(Enum):
    """Security scheme types.

    Sig: 2026-05-07 created
    """

    OAUTH2 = "oauth2"
    APIKEY = "apikey"
    BASIC = "basic"


@dataclass
class SecuritySchemeNode:
    """A security scheme definition.

    Args:
        name: Scheme identifier used in route `auth` references.
        scheme_type: The type of security scheme.
        flow: OAuth2 flow type (password, client_credentials).
        token_url: Token endpoint URL for OAuth2.
        verify_handler: Dotted path to the verification function.
        location: Where the API key is sent (header, query, cookie).
        header_name: Name of the header/query/cookie parameter for API keys.
        scopes: Available OAuth2 scopes.

    Sig: 2026-05-07 created
    """

    name: str = ""
    scheme_type: SecuritySchemeType = SecuritySchemeType.OAUTH2
    flow: str = "password"
    token_url: str = "/auth/token"
    verify_handler: str = ""
    location: str = "header"
    header_name: str = "Authorization"
    scopes: dict[str, str] = field(default_factory=dict)


@dataclass
class BackgroundTaskRef:
    """A background task to execute after the route response.

    Args:
        handler: Dotted path to the task function.
        args: Keyword arguments with $ref support for runtime values.

    Sig: 2026-05-07 created
    """

    handler: str = ""
    args: dict[str, str] = field(default_factory=dict)


@dataclass
class FileParamNode:
    """A file upload parameter on a route.

    Args:
        name: Parameter name in the function signature.
        multiple: Whether multiple files are accepted.
        max_size: Maximum file size (e.g., "10mb").
        accept: List of accepted file extensions/MIME types.
        description: Human-readable description.

    Sig: 2026-05-07 created
    """

    name: str = "file"
    multiple: bool = False
    max_size: str | None = None
    accept: list[str] = field(default_factory=list)
    description: str | None = None


@dataclass
class ResponseHeaderNode:
    """A response header to set on the route response.

    Args:
        name: HTTP header name.
        value: Static string or $ref expression.

    Sig: 2026-05-07 created
    """

    name: str = ""
    value: str = ""


@dataclass
class CookieNode:
    """A cookie to set on the route response.

    Args:
        key: Cookie name.
        value: Cookie value (static or $ref).
        max_age: Max age in seconds.
        path: Cookie path.
        domain: Cookie domain.
        secure: Secure flag.
        httponly: HttpOnly flag.
        samesite: SameSite policy (lax, strict, none).

    Sig: 2026-05-07 created
    """

    key: str = ""
    value: str = ""
    max_age: int | None = None
    path: str = "/"
    domain: str | None = None
    secure: bool = False
    httponly: bool = False
    samesite: str = "lax"


@dataclass
class DependencyRef:
    """A custom dependency to inject into a route.

    Args:
        handler: Dotted path to the dependency function.
        as_name: Parameter name in the route function signature.

    Sig: 2026-05-07 created
    """

    handler: str = ""
    as_name: str = ""


# ---------------------------------------------------------------------------
# Tier B + C nodes
# ---------------------------------------------------------------------------


@dataclass
class WebSocketRouteNode:
    """A WebSocket endpoint definition.

    Args:
        path: URL path pattern (e.g. "/ws/{room_id}").
        name: Route identifier.
        handler: Dotted path to the message handler function.
        on_connect: Dotted path to connection handler.
        on_disconnect: Dotted path to disconnect handler.
        auth: Security scheme name for WS auth.
        depends: Custom dependency injections.
        path_params: Parameters extracted from the URL path.

    Sig: 2026-05-07 created
    """

    path: str = ""
    name: str = ""
    handler: str = ""
    on_connect: str | None = None
    on_disconnect: str | None = None
    auth: str | None = None
    depends: list[DependencyRef] = field(default_factory=list)
    path_params: list["FieldNode"] = field(default_factory=list)


@dataclass
class HealthCheckConfig:
    """Health check endpoint configuration.

    Args:
        enabled: Whether to generate health endpoints.
        path: Health check endpoint path.
        ready_path: Readiness probe endpoint path.
        include_db: Whether to check database connectivity.
        custom_checks: Dotted paths to custom check handler functions.

    Sig: 2026-05-07 created
    """

    enabled: bool = True
    path: str = "/health"
    ready_path: str = "/ready"
    include_db: bool = True
    custom_checks: list[str] = field(default_factory=list)


@dataclass
class TestConfig:
    """Test generation configuration.

    Args:
        generate: Whether to generate test files.
        framework: Test framework (pytest).
        async_mode: Async testing library (anyio, asyncio).

    Sig: 2026-05-07 created
    """

    generate: bool = True
    framework: str = "pytest"
    async_mode: str = "anyio"


@dataclass
class CacheConfig:
    """Response caching configuration for a route.

    Args:
        max_age: Cache-Control max-age in seconds.
        private: Whether cache is private (vs public).
        no_store: Disable caching entirely.
        etag: Whether to generate ETag headers.
        vary: Vary header fields.

    Sig: 2026-05-07 created
    """

    max_age: int = 0
    private: bool = True
    no_store: bool = False
    etag: bool = False
    vary: list[str] = field(default_factory=list)


@dataclass
class RateLimitConfig:
    """Per-route rate limiting configuration.

    Args:
        rate: Rate limit string (e.g. "10/minute", "100/hour").
        key_func: Dotted path to custom key extraction function.

    Sig: 2026-05-07 created
    """

    rate: str = "10/minute"
    key_func: str | None = None


@dataclass
class OpenAPIExtras:
    """OpenAPI customization for a route.

    Args:
        operation_id: Custom operation ID.
        deprecated: Whether the endpoint is deprecated.
        description: Extended description.
        examples: Request/response examples.
        include_in_schema: Whether to include in OpenAPI schema.

    Sig: 2026-05-07 created
    """

    operation_id: str | None = None
    deprecated: bool = False
    description: str | None = None
    examples: dict[str, Any] = field(default_factory=dict)
    include_in_schema: bool = True


@dataclass
class MountConfig:
    """Sub-application mount configuration.

    Args:
        path: Mount path prefix.
        app_module: Dotted path to the sub-application (module.attribute).
        name: Optional name for the mounted app.

    Sig: 2026-05-07 created
    """

    path: str = ""
    app_module: str = ""
    name: str | None = None


@dataclass
class StreamingConfig:
    """Streaming response configuration.

    Args:
        media_type: MIME type for the stream (e.g. "text/event-stream").
        chunk_size: Chunk size for streaming responses.

    Sig: 2026-05-07 created
    """

    media_type: str = "text/event-stream"
    chunk_size: int | None = None


@dataclass
class DocsConfig:
    """OpenAPI documentation endpoint configuration.

    Args:
        enabled: Master switch — set False to disable all doc endpoints.
        docs_url: Swagger UI path (None to disable).
        redoc_url: ReDoc path (None to disable).
        openapi_url: OpenAPI JSON schema path (None to disable all docs).

    Sig: 2026-05-08 created
    """

    enabled: bool = True
    docs_url: str | None = "/docs"
    redoc_url: str | None = "/redoc"
    openapi_url: str | None = "/openapi.json"


class ActionKind(Enum):
    """Built-in action types for route pipelines.

    Sig: 2026-04-14 created
    """

    DB_LIST = "db.list"
    DB_GET = "db.get"
    DB_CREATE = "db.create"
    DB_UPDATE = "db.update"
    DB_DELETE = "db.delete"
    VALIDATE = "validate"
    TRANSFORM = "transform"
    SIDE_EFFECT = "side_effect"


@dataclass
class PipelineStepNode:
    """A single step in a route's processing pipeline.

    Args:
        action: The kind of action this step performs.
        handler: Dotted path to a custom handler function.
        args: Keyword arguments passed to the handler.
        as_name: Name to bind the step result to in the pipeline context.
        model: Model name this step operates on.

    Sig: 2026-04-14 created
    """

    action: ActionKind = ActionKind.DB_GET
    handler: str | None = None
    args: dict[str, str] = field(default_factory=dict)
    as_name: str | None = None
    model: str | None = None


@dataclass
class ErrorRef:
    """Reference to an error response from a route.

    Args:
        ref: Name of a globally defined ErrorNode.
        status: HTTP status code override.
        body: Inline response body override.
        condition: Expression describing when this error applies.

    Sig: 2026-04-14 created
    """

    ref: str | None = None
    status: int | None = None
    body: dict[str, Any] | None = None
    condition: str | None = None


@dataclass
class ErrorNode:
    """A reusable error definition.

    Args:
        name: Error identifier.
        status: HTTP status code.
        body: Default response body.
        description: Human-readable description.

    Sig: 2026-04-14 created
    """

    name: str = ""
    status: int = 500
    body: dict[str, Any] = field(default_factory=dict)
    description: str | None = None


@dataclass
class FilterNode:
    """A query filter definition for list endpoints.

    Args:
        field: Name of the field to filter on.
        op: Comparison operator (eq, ne, gt, lt, gte, lte, like, in).

    Sig: 2026-04-14 created
    """

    field: str = ""
    op: str = "eq"


@dataclass
class PaginationConfig:
    """Pagination settings for list endpoints.

    Args:
        enabled: Whether pagination is active.
        default_limit: Default page size.
        max_limit: Maximum allowed page size.

    Sig: 2026-04-14 created
    """

    enabled: bool = True
    default_limit: int = 20
    max_limit: int = 100


@dataclass
class RouteNode:
    """A single API route definition in the IR.

    Args:
        path: URL path pattern (e.g. "/users/{id}").
        method: HTTP method.
        name: Route identifier.
        summary: Short description for OpenAPI.
        tags: Grouping tags for OpenAPI.
        action: Simple single-action shorthand.
        pipeline: Multi-step processing pipeline.
        handler: Dotted path to a custom handler function.
        request_model: Name of the request body model.
        response_model: Name of the response body model.
        path_params: Parameters extracted from the URL path.
        query_params: Parameters extracted from the query string.
        filters: Filter definitions for list endpoints.
        errors: Error responses this route may produce.
        auth: Authentication scheme name.
        model: Primary model this route operates on.
        pagination: Pagination configuration for list endpoints.
        transform: Dotted path to a response transform function.
        background_tasks: Tasks to run after response is sent.
        file_params: File upload parameters.
        response_headers: Headers to set on the response.
        cookies: Cookies to set on the response.
        depends: Custom dependency injections.
        response_type: Response format (json, file, streaming).
        params: Extended parameters with source location.

    Sig: 2026-05-07 modified
    """

    path: str = ""
    method: HttpMethod = HttpMethod.GET
    name: str = ""
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    action: ActionKind | None = None
    pipeline: list[PipelineStepNode] | None = None
    handler: str | None = None
    request_model: str | None = None
    response_model: str | None = None
    path_params: list[FieldNode] = field(default_factory=list)
    query_params: list[FieldNode] = field(default_factory=list)
    filters: list[FilterNode] = field(default_factory=list)
    errors: list[ErrorRef] = field(default_factory=list)
    auth: str | None = None
    model: str | None = None
    pagination: PaginationConfig | None = None
    transform: str | None = None
    pipeline_ref: str | None = None
    background_tasks: list[BackgroundTaskRef] = field(default_factory=list)
    file_params: list[FileParamNode] = field(default_factory=list)
    response_headers: list[ResponseHeaderNode] = field(default_factory=list)
    cookies: list[CookieNode] = field(default_factory=list)
    depends: list[DependencyRef] = field(default_factory=list)
    response_type: ResponseKind = ResponseKind.JSON
    params: list["RouteParamNode"] = field(default_factory=list)
    cache: CacheConfig | None = None
    rate_limit: RateLimitConfig | None = None
    openapi_extras: OpenAPIExtras | None = None
    streaming: StreamingConfig | None = None


# ---------------------------------------------------------------------------
# App-level nodes
# ---------------------------------------------------------------------------

@dataclass
class RouteParamNode:
    """An extended route parameter with explicit source location.

    Args:
        name: Parameter name.
        source: Where the parameter comes from (header, cookie, form, query).
        field_type: Resolved type information.
        optional: Whether the parameter is optional.
        default: Default value; None if required.
        alias: Wire name (e.g., header name differs from param name).
        description: Human-readable description.

    Sig: 2026-05-07 created
    """

    name: str = ""
    source: ParamSource = ParamSource.QUERY
    field_type: FieldType = field(default_factory=FieldType)
    optional: bool = False
    default: Any = None
    alias: str | None = None
    description: str | None = None


@dataclass
class ConditionNode:
    """A boolean expression used by `if` steps and `when:` guards.

    Shapes (exactly one of these applies):
        - Leaf comparison: ``op`` in {eq, ne, gt, ge, lt, le, in, not_in,
          is_null, is_not_null}; ``left`` and ``right`` carry literals or
          ``$ref`` strings (``is_null``/``is_not_null`` ignore ``right``).
        - Combinator: ``op`` in {and, or}; ``children`` is the operand list.
        - Negation: ``op`` is ``not``; ``children`` is a single-element list.
        - Handler escape: ``when_handler`` is a dotted function path;
          ``captures`` names the bindings forwarded as keyword args.

    Args:
        op: Operator discriminator for structured conditions.
        left: Left-hand ref or literal.
        right: Right-hand ref or literal.
        children: Operands for and/or/not combinators.
        when_handler: Dotted path to a Python boolean function (escape hatch).
        captures: ``$ref`` list forwarded to ``when_handler`` as kwargs.

    Sig: 2026-04-24 created
    """

    op: str | None = None
    left: Any = None
    right: Any = None
    children: list["ConditionNode"] = field(default_factory=list)
    when_handler: str | None = None
    captures: list[str] = field(default_factory=list)


@dataclass
class IfStepNode:
    """An ``if/else`` step in a pipeline.

    Args:
        condition: The boolean expression to evaluate.
        then_steps: Steps executed when the condition is truthy.
        else_steps: Steps executed when the condition is falsy.
        as_name: Optional binding promoted to parent scope when both
            branches bind the same name.

    Sig: 2026-04-24 created
    """

    condition: ConditionNode = field(default_factory=ConditionNode)
    then_steps: list[Any] = field(default_factory=list)
    else_steps: list[Any] = field(default_factory=list)
    as_name: str | None = None


@dataclass
class ForEachStepNode:
    """A ``for_each`` iteration step.

    Args:
        iterable_ref: ``$ref`` expression producing the sequence to iterate.
        loop_var: Name bound to each element inside the body.
        body: Sub-pipeline executed per element.
        as_name: Optional accumulator name for the loop result.

    Sig: 2026-04-24 created
    """

    iterable_ref: str = ""
    loop_var: str = "item"
    body: list[Any] = field(default_factory=list)
    as_name: str | None = None


@dataclass
class ReturnStepNode:
    """An early-return step.

    Args:
        value_ref: ``$ref`` or literal to return. ``None`` returns no body
            (used with a ``status`` override for empty responses).
        status: Optional HTTP status override; pairs with a JSONResponse.
        condition: Optional guard; when set, the return is emitted inside
            an ``if`` statement.

    Sig: 2026-04-24 created
    """

    value_ref: str | None = None
    status: int | None = None
    condition: ConditionNode | None = None


@dataclass
class NamedPipelineNode:
    """A reusable pipeline declared at top level and referenced by routes.

    Args:
        name: Fully-qualified pipeline identifier. Local pipelines use the
            plain name (``fulfill_order``); imported pipelines use the
            dotted form (``shared.fulfill_order``).
        steps: Inline steps — empty when ``extends`` is set and the body is
            expressed purely through prepend/override/append.
        extends: Name of a parent pipeline this one inherits from.
        prepend: Steps to insert before the inherited list (after overrides).
        append: Steps to insert after the inherited list.
        override: Step replacements keyed by the target step's ``as_name``.
        resolved_steps: Post-flattening step list, populated by the resolver.

    Sig: 2026-04-24 created
    """

    name: str = ""
    steps: list["PipelineStepNode"] = field(default_factory=list)
    extends: str | None = None
    prepend: list["PipelineStepNode"] = field(default_factory=list)
    append: list["PipelineStepNode"] = field(default_factory=list)
    override: dict[str, "PipelineStepNode"] = field(default_factory=dict)
    resolved_steps: list["PipelineStepNode"] = field(default_factory=list)


@dataclass
class RouterGroupNode:
    """A first-class router: a prefix-scoped collection of routes.

    Routers may be declared inline in the root ``api.yaml`` (``routers:``
    section) or imported from a sibling YAML that exposes a top-level
    ``router:`` key. When imported, ``source_path`` carries the path of
    the source file relative to the project root so the emitter can
    mirror the DSL tree into ``generated/routers/<path>.py``.

    Args:
        name: Router identifier (used as Python module and variable name).
        prefix: URL prefix applied to every contained route.
        tags: OpenAPI tags inherited by every contained route.
        middleware: Per-router middleware (advisory; not yet applied).
        dependencies: Router-level FastAPI dependencies (advisory).
        auth: Optional auth scheme applied to every contained route.
        routes: Routes declared inside this router.
        source_path: Path (relative to project root) of the file this
            router was loaded from, or ``None`` for inline routers.

    Sig: 2026-04-24 created
    """

    name: str = ""
    prefix: str = ""
    tags: list[str] = field(default_factory=list)
    middleware: list["MiddlewareNode"] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    auth: str | None = None
    routes: list["RouteNode"] = field(default_factory=list)
    source_path: Path | None = None


@dataclass
class MiddlewareNode:
    """A middleware configuration entry.

    Args:
        kind: Middleware type identifier (e.g. "cors", "rate_limit").
        config: Middleware-specific configuration.

    Sig: 2026-04-14 created
    """

    kind: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class DatabaseConfig:
    """Database connection configuration.

    Args:
        db_type: Database engine type (e.g. "postgres", "sqlite").
        url: Connection URL.
        options: Additional engine options.

    Sig: 2026-04-14 created
    """

    db_type: str = ""
    url: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthConfig:
    """Authentication provider configuration.

    Args:
        provider: Auth provider name (e.g. "jwt", "oauth2").
        config: Provider-specific configuration.

    Sig: 2026-04-14 created
    """

    provider: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppNode:
    """Root IR node representing the entire API application.

    Args:
        title: Application title for OpenAPI spec.
        version: Application version string.
        description: Application description.
        base_path: URL prefix for all routes.
        database: Database configuration.
        auth: Authentication configuration.
        models: All model definitions.
        routes: All route definitions.
        errors: Reusable error definitions.
        middleware: Middleware stack.
        model_index: Lookup table from model name to ModelNode.
        error_index: Lookup table from error name to ErrorNode.
        security_schemes: Security scheme definitions.
        security_index: Lookup table from scheme name to SecuritySchemeNode.

    Sig: 2026-05-07 modified
    """

    title: str = "API"
    version: str = "0.1.0"
    description: str = ""
    base_path: str = ""
    database: DatabaseConfig | None = None
    auth: AuthConfig | None = None
    models: list[ModelNode] = field(default_factory=list)
    routes: list[RouteNode] = field(default_factory=list)
    errors: list[ErrorNode] = field(default_factory=list)
    middleware: list[MiddlewareNode] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    model_index: dict[str, ModelNode] = field(default_factory=dict)
    error_index: dict[str, ErrorNode] = field(default_factory=dict)
    named_pipelines: list[NamedPipelineNode] = field(default_factory=list)
    named_pipeline_index: dict[str, NamedPipelineNode] = field(default_factory=dict)
    routers: list[RouterGroupNode] = field(default_factory=list)
    security_schemes: list[SecuritySchemeNode] = field(default_factory=list)
    security_index: dict[str, SecuritySchemeNode] = field(default_factory=dict)
    websocket_routes: list[WebSocketRouteNode] = field(default_factory=list)
    health_check: HealthCheckConfig | None = None
    test_config: TestConfig | None = None
    mounts: list[MountConfig] = field(default_factory=list)
    docs: DocsConfig | None = None
