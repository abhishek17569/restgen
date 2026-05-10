---
name: restgen
description: Compile-time REST API framework. Use when the user writes, extends, or compiles a `restgen` YAML/JSON API config into a FastAPI app, or when the user mentions restgen, the `restgen` CLI, or any of its DSL keywords (`action`, `pipeline`, `handler`, `routers`, `models.base`, `response_type`, `cache`, `rate_limit`, `openapi`). Covers authoring the DSL, running the compiler, wiring custom handlers, swapping DB adapters, and debugging validation errors.
---

# restgen

restgen compiles a declarative YAML/JSON config into a fully typed FastAPI application (models, routes, errors, middleware, dependencies, tests). Generated code is plain Python — no runtime DSL interpreter. Edit the config, re-compile, redeploy.

## When to reach for this skill

Trigger when:
- A file named `api.yaml` / `api.yml` / `restgen.yaml` is present, or a config contains top-level keys like `routes:` + `models:` + `database:`.
- The user says "compile my API", "generate the FastAPI code", "update the DSL", "add a route to the config", or references `python -m src.restgen ...`.
- The user asks to swap the persistence adapter (`memory`, `postgres`, `sqlite`, `redis`, `mongo`) without rewriting the app.

Do **not** use this skill for hand-written FastAPI code where there's no config pipeline — use the generic `fastapi-python` skill instead.

## The 6-pass pipeline

```
YAML/JSON → load → parse → validate → resolve → optimize → lower → emit → .py files
```

Each pass is pure. Validation errors surface early, before any file is written.

## CLI

```bash
# Activate venv first (project convention)
source .venv/bin/activate

# Scaffold a starter config
python -m src.restgen init --out api.yaml

# Validate only (no files emitted)
python -m src.restgen validate api.yaml

# Compile to a FastAPI app
python -m src.restgen compile api.yaml --out generated/ -v

# Dry run (parses + validates without writing)
python -m src.restgen compile api.yaml --out generated/ --dry-run

# Run the generated app
uvicorn generated.app:app --reload
```

`compile` exits 1 on any `error`-severity issue and prints `[ERROR] <code>: <message> (at <location>)` per failure.

## How to write a FastAPI app with restgen (start-to-finish)

### Step 1: Define your data models

Start with your domain. Models become Pydantic v2 `BaseModel` classes in the generated code.

```yaml
models:
  User:
    fields:
      id: { type: uuid, primary: true, auto: true }
      email: { type: str, format: email, unique: true }
      name: { type: str, min_length: 1, max_length: 100 }
      role: { type: enum, values: [user, admin], default: user }
      created_at: { type: datetime, auto_now: true }

  # Derive input/output models from the base — no field duplication
  UserCreate:
    base: User
    include: [email, name]          # only these fields in the request body

  UserUpdate:
    base: User
    include: [email, name, role]
    all_optional: true              # PATCH semantics: all fields nullable
```

**Field types**: `str`, `int`, `float`, `bool`, `datetime`, `date`, `uuid`, `bytes`, `any`, `enum`, `ref` (nested model), `list`, `dict`.

**Constraints**: `min_length`, `max_length`, `ge`, `le`, `gt`, `lt`, `regex`, `multiple_of`.

### Step 2: Define your routes

Choose the right tier for each endpoint:

```yaml
routes:
  # Tier 1 — CRUD (zero Python required)
  - path: /users
    method: GET
    action: db.list
    model: User
    response_model: User
    pagination: true
    filters: [name, role]

  - path: /users/{id}
    method: GET
    action: db.get
    model: User
    errors: { not_found: NotFound }

  - path: /users
    method: POST
    action: db.create
    model: User
    request_model: UserCreate
    response_model: User

  # Tier 3 — Custom handler (complex logic stays in Python)
  - path: /users/{id}/deactivate
    method: POST
    handler: handlers.users.deactivate
    auth: jwt
    depends:
      - handlers.auth.require_admin
```

### Step 3: Add security (if needed)

```yaml
security:
  jwt:
    type: oauth2
    flow: password
    token_url: /auth/token
    verify_handler: handlers.auth.verify_token
```

Then reference it on routes with `auth: jwt`. The compiler generates FastAPI security deps — `OAuth2PasswordBearer` + your verify function.

### Step 4: Add operational features

Layer on production concerns declaratively:

```yaml
routes:
  - path: /users
    method: GET
    action: db.list
    model: User
    # Caching
    cache: { max_age: 60, etag: true, vary: [Authorization] }
    # Rate limiting
    rate_limit: "100/minute"
    # OpenAPI docs
    openapi: { operation_id: listUsers, description: "Paginated user list" }
    # Background work
    background_tasks:
      - handler: handlers.analytics.track_list_view
        args: { endpoint: "/users" }
```

### Step 5: Write your handlers (only for Tier 2/3)

Handlers are plain async Python functions. restgen passes kwargs matching the route's params:

```python
# handlers/users.py
from uuid import UUID
from fastapi import HTTPException

async def deactivate(id: UUID, require_admin: None, repo) -> dict:
    """Deactivate a user account. Only admins can call this."""
    user = await repo.get("User", id)
    if user is None:
        raise HTTPException(404, "User not found")
    return await repo.update("User", id, {"is_active": False})
```

**What restgen passes to your handler:**
- Path params by name (`id`, `slug`, etc.)
- `body` — the validated request model instance
- `repo` — the repository (always)
- File params by name (`file`, `documents`)
- Form/header/cookie params by name
- Custom dependency results by `as_name`

### Step 6: Compile and run

```bash
restgen compile api.yaml --out generated/ -v
cd generated && uvicorn app:app --reload
```

Visit `http://localhost:8000/docs` for the auto-generated Swagger UI.

### Step 7: Add WebSockets, health checks, tests (optional)

```yaml
# Real-time endpoints
websockets:
  - path: /ws/notifications
    handler: handlers.ws.on_message
    on_connect: handlers.ws.on_connect
    on_disconnect: handlers.ws.on_disconnect

# Infrastructure probes
health_check:
  path: /health
  ready_path: /ready
  include_db: true

# Auto-generate pytest suite
testing:
  generate: true
```

### Complete minimal example

This is the smallest config that produces a working API:

```yaml
name: todo_api
version: "1.0"

database:
  type: memory

models:
  Todo:
    fields:
      id: { type: uuid, primary: true, auto: true }
      title: { type: str, min_length: 1 }
      done: { type: bool, default: false }

  TodoCreate:
    base: Todo
    include: [title]

errors:
  NotFound: { status: 404, body: { message: "Not found" } }

routes:
  - { path: /todos, method: GET, action: db.list, model: Todo, pagination: true }
  - { path: /todos/{id}, method: GET, action: db.get, model: Todo, errors: { not_found: NotFound } }
  - { path: /todos, method: POST, action: db.create, model: Todo, request_model: TodoCreate }
  - { path: /todos/{id}, method: DELETE, action: db.delete, model: Todo }

middleware:
  - kind: cors
    config: { origins: ["*"], methods: ["*"], headers: ["*"] }
```

Compile → you get a full FastAPI app with Pydantic models, async CRUD endpoints, 404 handling, pagination, CORS, and OpenAPI docs. Zero Python written.

---

## Route tiers — pick the right one

| Tier | Config shape | When to use |
|------|--------------|-------------|
| **CRUD** | `action: db.list \| db.get \| db.create \| db.update \| db.delete` + `model: X` | Straight table operations, no custom logic |
| **Pipeline** | `pipeline: [{action, handler, args, as}]` | Multi-step flow (fetch → transform → emit) expressible as small handler functions |
| **Handler** | `handler: dotted.path.to.func` | Anything with branching, external I/O, or logic that doesn't fit pipeline steps |

All three tiers produce async functions. Handlers are verified via `ast.parse` at compile time — no import side-effects.

## DSL cheat sheet

```yaml
name: my_api
version: "1.0"

database:
  type: memory          # memory | sqlite | postgres | redis | mongo
  url: ...              # required for non-memory

# Documentation endpoints (optional — defaults to /docs, /redoc, /openapi.json)
docs:
  docs_url: /docs       # Swagger UI (null to disable)
  redoc_url: /redoc     # ReDoc (null to disable)
  openapi_url: /openapi.json  # schema endpoint (null disables ALL docs)
# Or simply: docs: false  (hides everything)

# Optional: pull in other config files as namespaces
$import:
  shared: ./shared/pipelines.yaml
  users: ./routers/users.yaml

models:
  Item:
    fields:
      id:    { type: uuid, primary: true, auto: true }
      name:  { type: str,  min_length: 1, max_length: 100 }
      price: { type: float, ge: 0 }
      created_at: { type: datetime, auto_now: true }

  ItemCreate:
    base: Item                # DSL-level field derivation, NOT Python inheritance
    include: [name, price]

  ItemUpdate:
    base: Item
    include: [name, price]
    all_optional: true        # every field becomes Optional[...] with default None

errors:
  NotFound: { status: 404, body: { message: "Not found" } }

routes:
  # CRUD
  - path: /items
    method: GET
    action: db.list
    model: Item
    pagination: { default_limit: 50, max_limit: 200 }
    filters: [name]

  # Handler + Tier C features
  - path: /items/{id}
    method: GET
    handler: handlers.items.get_item
    response_model: Item
    errors:
      not_found: NotFound
    cache:                    # emits Cache-Control / ETag / Vary on Response
      max_age: 300
      etag: true
      vary: [Authorization]
    rate_limit: "100/minute"  # adds @limiter.limit() + request: Request param
    openapi:
      operation_id: getItemById
      deprecated: false
      description: "Fetch a single item"

# Multi-file layout: declare routers at the root instead of inline routes
routers:
  - users                     # resolved via $import aliases

middleware:
  - kind: cors
    config: { origins: ["*"], methods: ["*"], headers: ["*"] }
  - kind: rate_limit
    config: { rate: "60/minute", storage_uri: "memory://" }
  - kind: custom
    config:
      class_path: my_app.middleware.LoggingMiddleware
      log_level: info
  - kind: custom
    config:
      handler: my_app.middleware.my_dispatch   # wrapped in BaseHTTPMiddleware
```

## Response types

`response_type:` on a route controls how the handler's return value is wrapped:

| Value | Wrapper | Notes |
|-------|---------|-------|
| `json` (default) | — | Pydantic serialization via `response_model` |
| `html` | `HTMLResponse(content=result)` | |
| `redirect` | `RedirectResponse(url=result)` | Handler returns the URL string |
| `plain` | `PlainTextResponse(content=result)` | |
| `file` | `FileResponse` | |
| `streaming` | `StreamingResponse` | Requires `streaming: { media_type, chunk_size }` |

## Common patterns

### File upload with background processing

```yaml
routes:
  - path: /documents/upload
    method: POST
    handler: handlers.uploads.process
    auth: jwt
    files:
      - name: file
        multiple: false
        max_size: 50mb
        accept: [".pdf", ".docx"]
    depends:
      - handlers.auth.get_current_user
    background_tasks:
      - handler: handlers.search.index_document
        args: { doc_id: "$result.id" }
```

### Form-based login returning JWT + refresh cookie

```yaml
routes:
  - path: /auth/login
    method: POST
    handler: handlers.auth.login
    params:
      - { name: username, source: form, type: str }
      - { name: password, source: form, type: str }
    cookies:
      refresh_token:
        value: "$result.refresh_token"
        max_age: 604800
        httponly: true
        secure: true
        samesite: strict
```

### Cached list with ETag + rate limiting

```yaml
routes:
  - path: /products
    method: GET
    action: db.list
    model: Product
    pagination: { default_limit: 50 }
    filters: [category, brand]
    cache: { max_age: 300, etag: true, vary: [Accept] }
    rate_limit: "200/minute"
    openapi: { operation_id: listProducts }
```

### WebSocket with room-based routing

```yaml
websockets:
  - path: /ws/chat/{room_id}
    handler: handlers.chat.on_message
    on_connect: handlers.chat.on_join
    on_disconnect: handlers.chat.on_leave
```

Handler receives: `websocket: WebSocket`, path params, dependency results, and `data: str` (the received message).

### Streaming response (SSE / large file)

```yaml
routes:
  - path: /events/stream
    method: GET
    handler: handlers.events.stream_events
    response_type: streaming
    streaming:
      media_type: text/event-stream
```

Handler returns a `StreamingResponse` with an async generator.

### Custom middleware (class or dispatch function)

```yaml
middleware:
  # Class-based: import + add_middleware
  - kind: custom
    config:
      class_path: my_app.middleware.RequestTimingMiddleware

  # Function-based: wrapped in BaseHTTPMiddleware
  - kind: custom
    config:
      handler: my_app.middleware.log_requests
```

### Sub-app mounting

```yaml
mounts:
  - path: /admin
    app: admin_panel.app
  - path: /metrics
    app: prometheus.app
```

## Writing handlers

Handlers are plain async functions in your own project. restgen imports them by dotted path at generation time. Signature must match the kwargs restgen passes in — at minimum `repo`, plus path params, `body`, file params, form/header/cookie params, and any custom `depends` by `as_name`.

```python
# handlers/items.py
from uuid import UUID
from my_models import Item   # import the Pydantic model from generated/

async def get_item(id: UUID, *, repo) -> Item | None:
    return await repo.get(Item, id)
```

## Swapping DB adapters

Change `database.type` and re-compile. The generated `dependencies.py` wires the right adapter; the repository interface (`repo.list/get/create/update/delete`) is identical across adapters. Built-in adapters live in `src/restgen/runtime/adapters/`.

## Error handling

restgen generates a complete exception handling layer. You never get raw stack traces in production — every error returns structured JSON.

### What gets generated in `errors.py`

| Exception type | Handler | Status | Response body |
|---|---|---|---|
| Named errors (`NotFoundError`, etc.) | Per-class handler | As configured (404, 401, etc.) | `{"detail": "Not found"}` |
| Any `HTTPException` | Generic handler | `exc.status_code` | `{"detail": exc.detail}` |
| `RequestValidationError` | Validation handler | 422 | `{"detail": "Validation error", "errors": [...]}` |
| **Any uncaught exception** | Catch-all handler | **500** | `{"detail": "Internal server error"}` |

### Defining errors in YAML

```yaml
errors:
  NotFound:
    status: 404
    body: { message: "Resource not found" }
  Conflict:
    status: 409
    body: { message: "Already exists" }
```

Each becomes an `HTTPException` subclass with a default detail message. Reference them on routes:

```yaml
routes:
  - path: /items/{id}
    method: GET
    action: db.get
    model: Item
    errors:
      not_found: NotFound      # raises NotFoundError when result is None
```

### In handlers

Raise `HTTPException` directly — the generic handler catches it:

```python
from fastapi import HTTPException

async def my_handler(repo, **kwargs):
    if not authorized:
        raise HTTPException(status_code=403, detail="Forbidden")
```

Or import and raise a named error from the generated `errors.py`:

```python
from generated.errors import NotFoundError

async def my_handler(id, repo):
    item = await repo.get("Item", id)
    if item is None:
        raise NotFoundError()  # uses default "Resource not found" detail
```

### Unhandled exceptions

Any `Exception` that isn't an `HTTPException` or `RequestValidationError` is caught by the 500 handler. The client sees `{"detail": "Internal server error"}` — never a stack trace. Logging the actual exception is left to your middleware or observability stack.

## Common failure modes

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `E001: unknown model X` | Route references a model that isn't declared | Add it to `models:` or fix the typo |
| `E004: handler not found` | `handler:` path doesn't resolve to a function | Verify the module is importable from the project root and the function exists |
| `E007: path param X not in fields` | `/{id}` segment with no matching path_param | Add `path_params:` or align names |
| Cache ETag looks wrong | Route references `$result` but no body reached the return | Ensure the handler returns the value you want hashed |
| Rate limit has no effect | `limiter` isn't defined at module scope | Include a `rate_limit` middleware entry — that creates the `limiter` instance |
| 500 on a valid request | Uncaught exception in handler | Check server logs — the 500 handler hides the detail from clients |
| 422 on POST/PUT | Request body doesn't match the model schema | Check `RequestValidationError.errors()` in the response for field-level detail |

## What to edit vs. regenerate

- **Edit the YAML** for any route/model/middleware/error change. Never hand-edit files under `generated/` — they're overwritten on every compile.
- **Edit handler source** (your own `handlers/` package) for business logic. The compiler re-imports them by dotted path.
- **Commit both** `api.yaml` and `generated/` if you deploy the generated tree directly; otherwise gitignore `generated/` and run `compile` as part of the build.

## Extending the compiler itself

Only relevant if you're changing restgen, not consuming it:

- New IR node → add to `src/restgen/ir/nodes.py` (dataclass, default via `field(default_factory=...)`).
- New DSL key → parse in `passes/parse.py`, validate in `passes/validate.py`.
- New codegen feature → add an emitter helper in `codegen/<name>_emitter.py`, wire it into `lower.py`.
- Use `ast.unparse` for output (guaranteed valid Python). Avoid templates.
- Follow the project's Function Signature Protocol (typed params, `Sig: YYYY-MM-DD <created|modified>` footer).

## Examples in this repo

Working configs you can copy from:

- `examples/url_shortener/api.yaml` — Redis adapter, rate limiting, mix of handler + pipeline + CRUD
- `examples/shop/api.yaml` — multi-file DSL with `$import`, top-level `routers:`, shared pipelines
- `examples/full_featured/api.yaml` — every feature (auth, uploads, WebSockets, caching, SSE, custom middleware, sub-app mounts)
- `examples/auth_service/api.yaml`, `examples/file_storage/api.yaml`, `examples/realtime_chat/api.yaml`, `examples/rate_limiter/api.yaml` — focused feature demos
