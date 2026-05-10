# restgen

A compile-time REST API framework. Write YAML config, get production-ready FastAPI code.

```
YAML Config  -->  6-Pass Compiler  -->  Clean FastAPI Python
```

restgen reads a declarative API specification and compiles it into real, debuggable Python files using the `ast` module. No runtime interpretation, no magic, no overhead. The generated code is what you'd write by hand -- but generated in milliseconds.

## Why restgen?

**For AI**: The config schema is small and strict. An LLM can reliably produce valid YAML far more easily than writing correct FastAPI with all the Pydantic/DI boilerplate.

**For humans**: Define your API once, get models, routes, validation, error handling, and DB wiring for free. Complex logic stays in plain Python functions.

**For performance**: Compile-time codegen means zero runtime dispatch overhead. The generated code runs at native FastAPI speed.

## Install

```bash
pip install restgen
```

Or from source:
```bash
git clone https://github.com/restgen/restgen && cd restgen
pip install -e ".[dev]"
```

## Quick start

```bash
# Generate a starter config
restgen init --out api.yaml

# Compile to FastAPI
restgen compile api.yaml --out generated/

# Run
cd generated && uvicorn app:app --reload
```

See [QUICKSTART.md](QUICKSTART.md) for a full walkthrough with examples.

## How it works

```
api.yaml                           generated/
--------                           ----------
models:                    -->     models.py       (Pydantic v2 classes)
routes:                            routes.py       (async endpoints)
security:                          security.py     (OAuth2/APIKey/Basic deps)
websockets:                        websockets.py   (WebSocket lifecycle)
health_check:                      health.py       (liveness + readiness)
testing:                           tests/          (pytest suite)
middleware:                        middleware.py   (CORS, GZip, custom)
database:                          dependencies.py (repository DI)
                                   app.py          (wires everything)
                                   runtime/        (repository ABC + adapter)
```

The compiler runs 6 passes:

| Pass | What it does |
|------|-------------|
| **Parse** | YAML/JSON dict --> IR node tree (dataclasses) |
| **Validate** | Type checks, missing refs, circular deps, structural errors |
| **Resolve** | Model inheritance, handler verification via `ast.parse` |
| **Optimize** | Mixin extraction, deduplication |
| **Lower** | IR --> Python `ast.Module` nodes |
| **Emit** | `ast.unparse()` --> formatted `.py` files |

## Three tiers of complexity

| You need... | Config pattern | Write Python? |
|------------|---------------|---------------|
| CRUD endpoint | `action: db.list` | No |
| Multi-step workflow | `pipeline: [steps...]` | Per-step functions |
| Custom logic | `handler: module.func` | Full function |

Simple things stay simple. Complex things are possible.

## Config reference

### Top-level

```yaml
name: my_api                    # App title (OpenAPI)
version: "1.0"                  # App version
description: "My API"           # Optional description
base_path: /api/v1              # URL prefix for all routes

database:
  type: memory                  # memory | postgres | sqlite | mongo
  url: ${DATABASE_URL}          # Connection URL (env var expansion)

auth:
  provider: jwt                 # Auth provider
  config: { ... }               # Provider-specific config
```

### Models

```yaml
models:
  # Base model with all field options
  User:
    fields:
      id:          { type: uuid, primary: true, auto: true }
      name:        { type: str, min_length: 1, max_length: 100 }
      email:       { type: str, format: email, unique: true }
      age:         { type: int, ge: 0, le: 150 }
      role:        { type: enum, values: [admin, user, viewer], default: user }
      bio:         { type: str, optional: true }
      score:       { type: float, gt: 0, lt: 100, multiple_of: 0.5 }
      is_active:   { type: bool, default: true }
      profile:     { type: ref, model: UserProfile }    # nested model
      tags:        { type: list, items: str }            # list of scalars
      metadata:    { type: dict, keys: str, values: any } # free-form dict
      created_at:  { type: datetime, auto_now: true }

  # Derived model -- pick fields from base (DRY)
  UserCreate:
    base: User
    include: [name, email, age, role]

  # Derived model -- all fields optional (PATCH semantics)
  UserUpdate:
    base: User
    include: [name, email, age, role, bio]
    all_optional: true

  # Derived model -- exclude fields
  UserResponse:
    base: User
    exclude: [metadata]

  # Derived model -- override defaults
  AdminCreate:
    base: User
    include: [name, email]
    overrides:
      role: { default: admin }

  # Computed fields -- derived at response time
  UserWithDisplay:
    base: User
    exclude: [metadata]
    computed:
      display_name: { type: str, handler: utils.format_display_name }
```

**Supported scalar types**: `str`, `int`, `float`, `bool`, `datetime`, `date`, `uuid`, `bytes`, `any`

**Field options**:

| Option | Type | Description |
|--------|------|-------------|
| `type` | string | Field type (required) |
| `primary` | bool | Primary key |
| `auto` | bool | Auto-generated value |
| `auto_now` | bool | Auto-set to current time (datetime fields) |
| `optional` | bool | Nullable field (generates `X \| None = None`) |
| `unique` | bool | Uniqueness constraint |
| `default` | any | Default value |
| `format` | string | Semantic format (`email` generates `EmailStr`) |
| `description` | string | Field description |

**Validation constraints**:

| Constraint | Applies to | Description |
|-----------|-----------|-------------|
| `min_length` | str, list | Minimum length |
| `max_length` | str, list | Maximum length |
| `ge` | int, float | Greater than or equal |
| `le` | int, float | Less than or equal |
| `gt` | int, float | Strictly greater than |
| `lt` | int, float | Strictly less than |
| `regex` | str | Pattern match |
| `multiple_of` | int, float | Must be divisible by |

### Routes

```yaml
routes:
  # ---- Tier 1: Simple CRUD ----
  - path: /users
    method: GET
    action: db.list
    model: User
    response_model: UserResponse
    pagination: true                     # adds skip/limit params
    filters: [name, email]              # adds optional query params

  - path: /users/{id}
    method: GET
    action: db.get
    model: User
    response_model: UserResponse
    errors:
      not_found: NotFound

  - path: /users
    method: POST
    action: db.create
    model: User
    request_model: UserCreate
    response_model: UserResponse

  - path: /users/{id}
    method: PUT
    action: db.update
    model: User
    request_model: UserUpdate
    response_model: UserResponse

  - path: /users/{id}
    method: DELETE
    action: db.delete
    model: User

  # ---- Tier 2: Pipeline ----
  - path: /orders/{id}/fulfill
    method: POST
    request_model: FulfillRequest
    response_model: OrderResponse
    pipeline:
      - action: db.get
        model: Order
        args: { id: "$path.id" }
        as: order
      - action: validate
        handler: validators.can_fulfill
        args: { order: "$order", body: "$body" }
        as: validated
      - action: db.update
        model: Order
        args: { id: "$order.id", data: "$validated" }
        as: updated
      - action: side_effect
        handler: events.emit_order_fulfilled
        args: { order: "$updated" }

  # ---- Tier 3: Custom handler ----
  - path: /users/{id}/deactivate
    method: POST
    handler: handlers.users.deactivate_user
    response_model: UserResponse
    errors:
      not_found: NotFound
```

**CRUD actions**: `db.list`, `db.get`, `db.create`, `db.update`, `db.delete`

**Pipeline actions**: `db.get`, `db.create`, `db.update`, `db.delete`, `db.list`, `validate`, `transform`, `side_effect`

**Pipeline `$references`**:
- `$path.X` -- path parameter
- `$body` -- request body
- `$query.X` -- query parameter
- `$step_name` -- result of a prior pipeline step (by `as` name)

**Route options**:

| Option | Type | Description |
|--------|------|-------------|
| `path` | string | URL pattern with `{param}` placeholders |
| `method` | string | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |
| `action` | string | CRUD shorthand (Tier 1) |
| `pipeline` | list | Multi-step workflow (Tier 2) |
| `handler` | string | Custom function reference (Tier 3) |
| `model` | string | Target DB model for CRUD |
| `request_model` | string | Request body model name |
| `response_model` | string | Response body model name |
| `pagination` | bool/dict | Enable pagination (`true` or `{default_limit: 50, max_limit: 200}`) |
| `filters` | list | Query param filters (`[name]` or `[{field: name, op: like}]`) |
| `errors` | dict | Error mapping (condition name to error ref or inline) |
| `tags` | list | OpenAPI grouping tags |
| `summary` | string | OpenAPI summary |
| `name` | string | Override auto-generated function name |
| `auth` | string | Auth scheme name |
| `transform` | string | Response transform function reference |

### Security

```yaml
security:
  # OAuth2 with password flow (JWT)
  jwt:
    type: oauth2
    flow: password
    token_url: /auth/token
    verify_handler: auth.verify_token     # your async function

  # API Key in header
  api_key:
    type: apikey
    location: header                      # header | query | cookie
    name: X-API-Key
    verify_handler: auth.verify_api_key

  # HTTP Basic
  basic:
    type: basic
    verify_handler: auth.verify_basic
```

Reference in routes with `auth: jwt` or `auth: api_key`.

Generated code uses FastAPI's native `OAuth2PasswordBearer`, `APIKeyHeader`, `HTTPBasic` -- zero overhead.

### Background Tasks

```yaml
routes:
  - path: /orders
    method: POST
    action: db.create
    model: Order
    request_model: OrderCreate
    background_tasks:
      - handler: notifications.send_order_email
        args: { order_id: "$result.id", email: "$result.email" }
      - handler: analytics.track_order
        args: { order_id: "$result.id" }
```

Generates `background_tasks: BackgroundTasks` parameter with `add_task()` calls after the response.

### File Upload & Download

```yaml
routes:
  # Single file upload
  - path: /documents/upload
    method: POST
    handler: uploads.process_document
    files:
      - name: file
        multiple: false
        max_size: 50mb
        accept: [".pdf", ".docx"]

  # Multiple files
  - path: /documents/batch
    method: POST
    handler: uploads.process_batch
    files:
      - name: documents
        multiple: true
        max_size: 10mb

  # File download
  - path: /documents/{id}/download
    method: GET
    handler: documents.download
    response_type: file

  # Streaming response (large files, SSE)
  - path: /documents/{id}/stream
    method: GET
    handler: documents.stream
    response_type: streaming
    streaming:
      media_type: application/octet-stream
      chunk_size: 65536
```

### Form, Header & Cookie Parameters

```yaml
routes:
  - path: /auth/login
    method: POST
    handler: auth.login
    params:
      - name: username
        source: form            # form | header | cookie | query
        type: str
      - name: password
        source: form
        type: str
      - name: user_agent
        source: header
        type: str
        alias: User-Agent
        optional: true
      - name: session_id
        source: cookie
        type: str
        optional: true
```

### Custom Dependencies

```yaml
routes:
  - path: /items
    method: POST
    action: db.create
    model: Item
    depends:
      - auth.get_current_user        # injected as current_user param
      - permissions.require_admin    # injected as require_admin param
```

### Response Headers & Cookies

```yaml
routes:
  - path: /items/{id}
    method: GET
    action: db.get
    model: Item
    response_headers:
      X-Request-Id: "$path.id"
      X-Custom: "static-value"
    cookies:
      last_viewed:
        value: "$path.id"
        max_age: 86400
        httponly: true
        samesite: lax
```

### WebSocket Endpoints

```yaml
websockets:
  - path: /ws/chat/{room_id}
    name: ws_chat
    handler: chat.on_message
    on_connect: chat.on_connect
    on_disconnect: chat.on_disconnect
    depends:
      - handler: auth.get_ws_user
        as: current_user
```

Generates a complete WebSocket lifecycle: accept, connect handler, receive loop, disconnect handler.

### Health Checks

```yaml
health_check:
  path: /health
  ready_path: /ready
  include_db: true
  custom_checks:
    - checks.check_redis
    - checks.check_queue
```

Generates `/health` (liveness) and `/ready` (readiness with DB + custom checks, returns 503 on failure).

### Caching

```yaml
routes:
  - path: /items
    method: GET
    action: db.list
    model: Item
    cache:
      max_age: 3600        # Cache-Control: max-age=3600
      private: true        # private vs public
      etag: true           # generates ETag header
      vary: [Accept, Authorization]
```

### Per-Route Rate Limiting

```yaml
routes:
  - path: /items/{id}
    method: GET
    action: db.get
    model: Item
    rate_limit: "100/minute"   # or: { rate: "100/minute", key_func: auth.get_user_id }
```

### OpenAPI Customization

```yaml
routes:
  - path: /items
    method: GET
    action: db.list
    model: Item
    openapi:
      operation_id: listItems
      deprecated: false
      description: "List all items with filtering and pagination"
      include_in_schema: true
```

### Custom Middleware

```yaml
middleware:
  # Class-based (import and add_middleware)
  - kind: custom
    config:
      class_path: my_middleware.RequestLogger
      log_level: info

  # Dispatch-based (BaseHTTPMiddleware + function)
  - kind: custom
    config:
      handler: my_middleware.timing_dispatch
```

### Documentation Endpoints

```yaml
# Default: /docs (Swagger), /redoc, /openapi.json — all enabled
# No config needed for defaults.

# Disable all docs (production deployments)
docs: false

# Custom URLs
docs:
  docs_url: /api/docs
  redoc_url: /api/redoc
  openapi_url: /api/schema.json

# Selectively disable
docs:
  docs_url: /swagger          # move Swagger UI
  redoc_url: null             # disable ReDoc
  openapi_url: /openapi.json  # keep schema
```

When `docs: false` or `enabled: false`, the generated `FastAPI(...)` call passes `docs_url=None, redoc_url=None, openapi_url=None` — no documentation endpoints are exposed.

### Sub-Application Mounting

```yaml
mounts:
  - path: /admin
    app: admin_panel.app
    name: admin
  - path: /metrics
    app: monitoring.prometheus_app
```

### Test Generation

```yaml
testing:
  generate: true
  framework: pytest
  async_mode: anyio
```

Generates `tests/conftest.py` (AsyncClient fixture) and `tests/test_api.py` (CRUD tests for every route).

### Errors

```yaml
errors:
  # Named, reusable error definitions
  NotFound:
    status: 404
    body: { message: "Resource not found" }

  Unauthorized:
    status: 401
    body: { message: "Authentication required" }

  Conflict:
    status: 409
    body: { message: "Resource already exists" }

  ValidationFailed:
    status: 422
    body: { message: "Validation failed" }
```

Reference in routes:

```yaml
routes:
  - path: /users/{id}
    method: GET
    action: db.get
    model: User
    errors:
      not_found: NotFound           # reference to named error
      unauthorized: Unauthorized

  - path: /users
    method: POST
    action: db.create
    model: User
    errors:
      duplicate:                     # inline error definition
        status: 409
        body: { message: "Email already taken" }
```

**Built-in exception handling** — the generated `errors.py` always includes:

| Exception | Status | Response |
|-----------|--------|----------|
| Named errors (your `errors:` section) | As configured | `{"detail": "..."}` |
| Any `HTTPException` | `exc.status_code` | `{"detail": exc.detail}` |
| `RequestValidationError` (bad request body) | 422 | `{"detail": "Validation error", "errors": [...]}` |
| Uncaught exceptions | 500 | `{"detail": "Internal server error"}` |

No stack traces leak to clients. Every response is structured JSON.

### Middleware

```yaml
middleware:
  - kind: cors
    config:
      origins: ["*"]
      methods: ["*"]
      headers: ["*"]

  - kind: trustedhost
    config:
      hosts: ["example.com", "*.example.com"]

  - kind: gzip
    config:
      minimum_size: 1000

  - kind: rate_limit
    config:
      rate: "30/minute"                     # default rate limit
      storage_uri: "redis://localhost:6379/1" # slowapi backend
```

## CLI

```bash
# Compile config to FastAPI code
restgen compile api.yaml --out generated/

# Compile with verbose output
restgen compile api.yaml --out generated/ -v

# Validate config without generating code
restgen validate api.yaml

# Dry run (validate + check, no file output)
restgen compile api.yaml --dry-run

# Skip code formatting
restgen compile api.yaml --no-format

# Generate starter config
restgen init --out api.yaml
```

Or via module: `python -m src.restgen compile api.yaml --out generated/`

## Generated output

After compilation, the output directory contains:

```
generated/
    __init__.py          # sys.path setup (only when handlers are used)
    app.py               # FastAPI app, middleware, error handlers, router mounts
    models.py            # Pydantic v2 BaseModel classes
    routes.py            # APIRouter with async endpoint functions
    errors.py            # Exception classes + handlers (named + 422 + 500 catch-all)
    middleware.py        # Middleware registration (CORS, GZip, custom, rate limit)
    dependencies.py      # Repository setup + dependency injection
    security.py          # OAuth2/APIKey/Basic security deps (when configured)
    health.py            # Health check endpoints (when configured)
    websockets.py        # WebSocket endpoints (when configured)
    tests/
        conftest.py      # pytest fixtures (when testing enabled)
        test_api.py      # Generated API tests
    runtime/
        repository.py    # Abstract Repository ABC
        exceptions.py    # Base exceptions
        adapters/
            memory.py    # In-memory adapter (default for development)
```

Run with: `uvicorn generated.app:app --reload`

The generated code is fully standalone -- it does not import from `restgen`. You can copy it, modify it, or deploy it directly.

## Database adapters

Set `database.type` in config to select the adapter:

| Type | Adapter | Connection |
|------|---------|-----------|
| `memory` | `MemoryRepository` | In-memory dicts (default, for dev/test) |
| `postgres` | `PostgresRepository` | SQLAlchemy async |
| `sqlite` | `SqliteRepository` | aiosqlite |
| `mongo` | `MongoRepository` | Motor |
| `redis` | `RedisRepository` | redis-py async (JSON + sorted sets) |

```yaml
database:
  type: postgres
  url: ${DATABASE_URL}    # env var expansion
```

## Writing custom handlers (Tier 3)

When CRUD isn't enough, point to a Python function:

```yaml
# api.yaml
routes:
  - path: /users/{id}/deactivate
    method: POST
    handler: handlers.users.deactivate_user
    response_model: UserResponse
```

```python
# handlers/users.py
async def deactivate_user(id, repo):
    """Custom business logic in plain Python."""
    user = await repo.get(User, id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    result = await repo.update(User, id, {"is_active": False})
    await send_notification(user.email)
    return result
```

The compiler verifies that `handlers.users.deactivate_user` exists at compile time (via `ast.parse`, no imports).

## Architecture

restgen is a real compiler with a clean separation of concerns:

```
schema/loader.py      -- Load YAML/JSON, basic format validation
schema/linker.py      -- Resolve $import directives across files
passes/parse.py       -- Raw dict -> IR (intermediate representation)
passes/validate.py    -- 30+ structural checks on the IR (E001-E083)
passes/resolve.py     -- Model inheritance, handler verification, pipeline flattening
passes/optimize.py    -- Mixin extraction, deduplication
passes/lower.py       -- IR -> Python ast.Module (delegates to codegen/)
passes/emit.py        -- ast.unparse() -> formatted .py files on disk

codegen/ast_builder.py        -- AST construction helpers
codegen/model_emitter.py      -- ModelNode -> Pydantic ClassDef
codegen/route_emitter.py      -- RouteNode -> async endpoint (with all Tier A-C features)
codegen/pipeline_emitter.py   -- PipelineStepNode[] -> orchestration code
codegen/error_emitter.py      -- ErrorNode -> exception class
codegen/middleware_emitter.py  -- MiddlewareNode -> registration (built-in + custom)
codegen/security_emitter.py   -- SecuritySchemeNode -> OAuth2/APIKey/Basic deps
codegen/websocket_emitter.py  -- WebSocketRouteNode -> WS lifecycle endpoint
codegen/health_emitter.py     -- HealthCheckConfig -> /health + /ready endpoints
codegen/test_emitter.py       -- TestConfig -> pytest test suite
codegen/app_emitter.py        -- Top-level app wiring + mount + includes
codegen/repo_emitter.py       -- Repository DI setup + lifespan
```

The IR uses plain Python dataclasses (no Pydantic in the compiler). Pydantic is only a dependency of the generated code.

## Requirements

**restgen itself** (the compiler):
- Python 3.12+
- pyyaml

**Generated code** (your app's runtime dependencies):
- fastapi
- uvicorn
- pydantic >= 2.0
- httpx (for tests)
- slowapi (if using rate limiting)

## Feature matrix

| Feature | YAML Key | Generated Code |
|---------|----------|----------------|
| CRUD endpoints | `action: db.*` | Async functions with repo pattern |
| Pipelines | `pipeline: [...]` | Multi-step orchestration |
| Custom handlers | `handler: module.func` | Direct function delegation |
| Security (OAuth2/APIKey/Basic) | `security:` | FastAPI security dependencies |
| Background tasks | `background_tasks:` | `BackgroundTasks.add_task()` |
| File upload | `files:` | `UploadFile` / `File(...)` |
| File download | `response_type: file` | `FileResponse` |
| Streaming/SSE | `response_type: streaming` | `StreamingResponse` |
| Form params | `params: [{source: form}]` | `Form(...)` |
| Header/Cookie params | `params: [{source: header}]` | `Header(...)` / `Cookie(...)` |
| Response headers | `response_headers:` | `response.headers[...]` |
| Cookies | `cookies:` | `response.set_cookie(...)` |
| Custom dependencies | `depends:` | `Depends(user_func)` |
| WebSockets | `websockets:` | Full lifecycle endpoint |
| Health checks | `health_check:` | `/health` + `/ready` |
| Test generation | `testing:` | pytest + httpx suite |
| Caching | `cache:` | Cache-Control + ETag headers |
| Rate limiting | `rate_limit:` | `@limiter.limit()` decorator |
| OpenAPI extras | `openapi:` | operation_id, deprecated, description |
| Custom middleware | `kind: custom` | class or dispatch import |
| Sub-app mounting | `mounts:` | `app.mount()` |
| Multiple response types | `response_type:` | HTML, Redirect, Plain, File, Streaming |
| Documentation control | `docs:` | Custom URLs, disable Swagger/ReDoc/OpenAPI |

## Examples

See the `examples/` directory. Each example is self-contained with config, handlers, and README:

| Example | System Design Problem | Features |
|---------|----------------------|----------|
| `rate_limiter/` | **Rate Limiting** — token bucket, tiered access, Redis counters | Per-route limits, custom key_func, middleware headers, health checks |
| `auth_service/` | **Authentication** — JWT access/refresh tokens, RBAC | OAuth2, form login, cookies, background tasks, role-based deps |
| `realtime_chat/` | **Real-time Messaging** — room-based pub/sub | WebSockets, presence tracking, message persistence, caching |
| `file_storage/` | **File Storage** — upload/download pipeline | Multi-file upload, streaming download, background indexing |
| `shop/` | **E-commerce** — multi-module, pipelines | Routers, multi-file YAML, pipeline composition, named pipelines |
| `url_shortener/` | **URL Shortener** — Redis-backed | Redis adapter, custom handlers, redirect responses |
| `full_featured/` | **Kitchen Sink** — all features combined | Every restgen feature in one config |

## License

MIT
