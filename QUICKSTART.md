# restgen Quick Start

This guide walks through building APIs with restgen, from a basic CRUD service to complex multi-step pipelines.

---

## Setup

```bash
# From the project root
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml jsonschema fastapi uvicorn pydantic
```

---

## Example 1: Basic CRUD (Item catalog)

The simplest use case -- a full CRUD API from one config file.

### Config

```yaml
# examples/01_basic_crud.yaml
name: item_catalog
version: "1.0"

database:
  type: memory

models:
  Item:
    fields:
      id:          { type: uuid, primary: true, auto: true }
      name:        { type: str, min_length: 1, max_length: 100 }
      description: { type: str, optional: true }
      price:       { type: float, ge: 0 }
      created_at:  { type: datetime, auto_now: true }

  ItemCreate:
    base: Item
    include: [name, description, price]

  ItemUpdate:
    base: Item
    include: [name, description, price]
    all_optional: true

routes:
  - path: /items
    method: GET
    action: db.list
    model: Item
    pagination: true

  - path: /items/{id}
    method: GET
    action: db.get
    model: Item

  - path: /items
    method: POST
    action: db.create
    model: Item
    request_model: ItemCreate

  - path: /items/{id}
    method: PUT
    action: db.update
    model: Item
    request_model: ItemUpdate

  - path: /items/{id}
    method: DELETE
    action: db.delete
    model: Item
```

### Compile and run

```bash
python -m src.restgen compile examples/01_basic_crud.yaml --out examples/01_generated
cd examples && uvicorn 01_generated.app:app --port 8000
```

### Test

```bash
# Create
curl -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Widget", "price": 9.99}'

# List all
curl http://localhost:8000/items

# Get by ID
curl http://localhost:8000/items/<uuid>

# Update
curl -X PUT http://localhost:8000/items/<uuid> \
  -H "Content-Type: application/json" \
  -d '{"name": "Super Widget"}'

# Delete
curl -X DELETE http://localhost:8000/items/<uuid>
```

### What gets generated

**models.py** -- clean Pydantic v2 classes:

```python
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

class Item(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    price: float = Field(ge=0)
    created_at: datetime

class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    price: float = Field(ge=0)

class ItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    price: float | None = Field(default=None, ge=0)
```

**routes.py** -- async FastAPI endpoints:

```python
from fastapi import APIRouter, Depends, Query, HTTPException
from .models import Item, ItemCreate, ItemUpdate
from .dependencies import get_repository

router = APIRouter()

@router.get('/items', response_model=list[Item], status_code=200)
async def list_items(skip: int = Query(default=0), limit: int = Query(default=20),
                     repo=Depends(get_repository)):
    return await repo.list(Item, skip=skip, limit=limit)

@router.post('/items', response_model=Item, status_code=201)
async def create_item(body: ItemCreate, repo=Depends(get_repository)):
    return await repo.create(Item, body.model_dump())
```

---

## Example 2: Filtering, pagination, and errors

Add query filters, custom pagination, and structured error responses.

```yaml
# examples/02_filters_errors.yaml
name: product_api
version: "1.0"

database:
  type: memory

models:
  Product:
    fields:
      id:          { type: uuid, primary: true, auto: true }
      name:        { type: str, min_length: 1, max_length: 200 }
      category:    { type: str }
      price:       { type: float, ge: 0 }
      in_stock:    { type: bool, default: true }
      created_at:  { type: datetime, auto_now: true }

  ProductCreate:
    base: Product
    include: [name, category, price, in_stock]

errors:
  NotFound:
    status: 404
    body: { message: "Product not found" }

  Conflict:
    status: 409
    body: { message: "Product already exists" }

routes:
  # List with filtering and custom pagination
  - path: /products
    method: GET
    action: db.list
    model: Product
    pagination: { default_limit: 50, max_limit: 200 }
    filters:
      - name                           # simple string equality
      - category                       # simple string equality
      - { field: price, op: gte }      # advanced: price >= value
      - { field: price, op: lte }      # advanced: price <= value

  # Get with named error
  - path: /products/{id}
    method: GET
    action: db.get
    model: Product
    errors:
      not_found: NotFound

  # Create with inline error
  - path: /products
    method: POST
    action: db.create
    model: Product
    request_model: ProductCreate
    errors:
      duplicate:
        status: 409
        body: { message: "A product with this name already exists" }

  - path: /products/{id}
    method: DELETE
    action: db.delete
    model: Product

middleware:
  - kind: cors
    config:
      origins: ["http://localhost:3000"]
      methods: ["GET", "POST", "PUT", "DELETE"]
      headers: ["*"]
```

### What this gives you

- `GET /products?name=Widget&category=electronics&skip=0&limit=50` -- filtered, paginated list
- `GET /products/{id}` -- returns `404` with `{"detail": "Product not found"}` if missing
- `POST /products` -- creates product, returns `409` on duplicate
- Automatic CORS middleware for your frontend

---

## Example 3: All field types

A model showcasing every supported type.

```yaml
models:
  # Every field type restgen supports
  FullExample:
    fields:
      # Scalars
      id:           { type: uuid, primary: true, auto: true }
      name:         { type: str, min_length: 1, max_length: 255 }
      count:        { type: int, ge: 0 }
      price:        { type: float, gt: 0, lt: 99999.99 }
      is_active:    { type: bool, default: true }
      published_on: { type: date }
      created_at:   { type: datetime, auto_now: true }
      raw_data:     { type: bytes }
      extra:        { type: any }

      # Email (generates Pydantic EmailStr)
      email:        { type: str, format: email, unique: true }

      # Enum
      status:       { type: enum, values: [draft, published, archived], default: draft }

      # Nested model reference
      author:       { type: ref, model: Author }

      # Collections
      tags:         { type: list, items: str }
      scores:       { type: list, items: int }
      related:      { type: list, items: FullExample }    # list of model refs
      settings:     { type: dict, keys: str, values: any }

      # Optional
      nickname:     { type: str, optional: true }

      # Regex constraint
      slug:         { type: str, regex: "^[a-z0-9-]+$" }

      # Numeric precision
      rating:       { type: float, ge: 0, le: 5, multiple_of: 0.5 }

  Author:
    fields:
      id:   { type: uuid, primary: true, auto: true }
      name: { type: str }
```

---

## Example 4: Model inheritance patterns

restgen supports deriving models from a base to keep configs DRY.

```yaml
models:
  # Base model -- the "source of truth" for all User fields
  User:
    fields:
      id:         { type: uuid, primary: true, auto: true }
      username:   { type: str, min_length: 3, max_length: 50, unique: true }
      email:      { type: str, format: email, unique: true }
      role:       { type: enum, values: [admin, editor, viewer], default: viewer }
      bio:        { type: str, max_length: 500, optional: true }
      is_active:  { type: bool, default: true }
      login_count:{ type: int, default: 0 }
      created_at: { type: datetime, auto_now: true }

  # --- include: pick specific fields ---
  UserCreate:
    base: User
    include: [username, email, role, bio]
    # Result: username (required), email (required), role (default: viewer), bio (optional)

  # --- all_optional: every field becomes X | None = None ---
  UserUpdate:
    base: User
    include: [username, email, role, bio, is_active]
    all_optional: true
    # Result: all fields optional, perfect for PATCH

  # --- exclude: hide internal fields from responses ---
  UserResponse:
    base: User
    exclude: [login_count]
    # Result: everything except login_count

  # --- overrides: change defaults for a context ---
  AdminCreate:
    base: User
    include: [username, email, bio]
    overrides:
      role: { default: admin }
    # Result: same as UserCreate but role defaults to "admin"

  # --- computed: add derived fields ---
  UserProfile:
    base: User
    exclude: [login_count, is_active]
    computed:
      display_name: { type: str, handler: utils.format_display_name }
    # Result: User fields + computed display_name property
```

**Key concepts:**

- `base` is for **field derivation** (DRY config), not Python class inheritance. All generated models extend `BaseModel` directly.
- `include` and `exclude` are mutually exclusive -- use one or the other.
- `all_optional: true` makes every inherited field `X | None = None` -- ideal for update/PATCH models.
- `overrides` lets you change field defaults per derived model.
- `computed` fields run a Python function at response time.

---

## Example 5: Custom handlers (Tier 3)

For business logic that can't be expressed in config, point to a function.

### Config

```yaml
# examples/05_handlers.yaml
name: user_service
version: "1.0"

database:
  type: memory

models:
  User:
    fields:
      id:        { type: uuid, primary: true, auto: true }
      email:     { type: str, format: email }
      is_active: { type: bool, default: true }

  UserResponse:
    base: User

  DeactivateRequest:
    fields:
      reason: { type: str, max_length: 500 }

errors:
  NotFound:
    status: 404
    body: { message: "User not found" }

  AlreadyInactive:
    status: 409
    body: { message: "User is already inactive" }

routes:
  # Standard CRUD
  - path: /users
    method: GET
    action: db.list
    model: User
    response_model: UserResponse

  - path: /users
    method: POST
    action: db.create
    model: User
    request_model: User

  # Custom handler -- complex deactivation logic
  - path: /users/{id}/deactivate
    method: POST
    handler: handlers.users.deactivate_user
    response_model: UserResponse
    errors:
      not_found: NotFound
      already_inactive: AlreadyInactive
```

### Handler code

```python
# handlers/users.py
from fastapi import HTTPException


async def deactivate_user(id, body, repo):
    """Deactivate a user with business logic.

    The generated route will call this function with:
    - id: path parameter
    - body: request body (if request_model is set)
    - repo: the repository instance

    Sig: 2026-04-15 created
    """
    user = await repo.get(User, id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=409, detail="User is already inactive")

    result = await repo.update(User, id, {"is_active": False})

    # Side effects: send notification, audit log, etc.
    await send_deactivation_email(user.email, body.reason)
    await audit_log("user.deactivated", user_id=str(id))

    return result
```

The compiler verifies at compile time (via `ast.parse`) that `handlers.users.deactivate_user` exists on disk. No imports happen -- just a syntax check.

---

## Example 6: Pipelines (Tier 2)

For multi-step workflows where each step is simple but the orchestration matters.

```yaml
# examples/06_pipeline.yaml
name: order_service
version: "1.0"

database:
  type: memory

models:
  Order:
    fields:
      id:        { type: uuid, primary: true, auto: true }
      user_id:   { type: uuid }
      status:    { type: enum, values: [pending, fulfilled, cancelled], default: pending }
      total:     { type: float, ge: 0 }
      created_at:{ type: datetime, auto_now: true }

  FulfillRequest:
    fields:
      tracking_number: { type: str }
      carrier:         { type: str }

  OrderResponse:
    base: Order

routes:
  # Standard CRUD for orders
  - path: /orders
    method: GET
    action: db.list
    model: Order
    response_model: OrderResponse
    pagination: true

  - path: /orders
    method: POST
    action: db.create
    model: Order
    response_model: OrderResponse

  # Pipeline: multi-step fulfillment workflow
  - path: /orders/{id}/fulfill
    method: POST
    request_model: FulfillRequest
    response_model: OrderResponse
    pipeline:
      # Step 1: Fetch the order
      - action: db.get
        model: Order
        args: { id: "$path.id" }
        as: order

      # Step 2: Validate it can be fulfilled
      - action: validate
        handler: validators.orders.can_fulfill
        args: { order: "$order" }

      # Step 3: Transform -- compute fulfillment data
      - action: transform
        handler: transforms.orders.build_fulfillment
        args: { order: "$order", body: "$body" }
        as: fulfillment_data

      # Step 4: Update the order
      - action: db.update
        model: Order
        args: { id: "$path.id", data: "$fulfillment_data" }
        as: updated_order

      # Step 5: Fire-and-forget side effect
      - action: side_effect
        handler: events.orders.emit_fulfilled
        args: { order: "$updated_order" }
```

### Pipeline step handlers

```python
# validators/orders.py
async def can_fulfill(order):
    """Raise if order can't be fulfilled."""
    if order.status != "pending":
        raise HTTPException(status_code=409, detail=f"Order is {order.status}, not pending")
    return True


# transforms/orders.py
async def build_fulfillment(order, body):
    """Compute the data to update the order with."""
    return {
        "status": "fulfilled",
        "tracking_number": body.tracking_number,
        "carrier": body.carrier,
    }


# events/orders.py
async def emit_fulfilled(order):
    """Send notification, update analytics, etc."""
    print(f"Order {order.id} fulfilled!")
```

### How pipelines compile

The pipeline above compiles to this flat async function (no runtime pipeline executor):

```python
@router.post('/orders/{id}/fulfill', response_model=OrderResponse, status_code=200)
async def fulfill_order(id: UUID, body: FulfillRequest, repo=Depends(get_repository)):
    order = await repo.get(Order, id)
    if order is None:
        raise HTTPException(status_code=404, detail='Not found')
    await can_fulfill(order=order)
    fulfillment_data = await build_fulfillment(order=order, body=body)
    updated_order = await repo.update(Order, id, fulfillment_data)
    await emit_fulfilled(order=updated_order)
    return updated_order
```

Zero runtime overhead. Fully debuggable. Each step is just a line of code.

### Pipeline reference

| Key | Required | Description |
|-----|----------|-------------|
| `action` | yes | `db.get`, `db.create`, `db.update`, `db.delete`, `db.list`, `validate`, `transform`, `side_effect` |
| `handler` | for non-db | Dotted function path (`module.func`) |
| `model` | for db actions | Model name for repository calls |
| `args` | no | Named arguments with `$references` |
| `as` | no | Name to store the step's result |

**`$reference` syntax:**

| Reference | Resolves to |
|-----------|-------------|
| `$path.X` | Path parameter `X` |
| `$body` | Request body |
| `$query.X` | Query parameter `X` |
| `$step_name` | Result of a prior step (matched by `as` name) |

---

## Example 7: Full application

A complete multi-resource API with all features.

```yaml
# examples/07_full_app.yaml
name: blog_api
version: "2.0"
description: "Blog platform API"
base_path: /api/v2

database:
  type: memory

models:
  # ---- Authors ----
  Author:
    fields:
      id:       { type: uuid, primary: true, auto: true }
      name:     { type: str, min_length: 1, max_length: 100 }
      email:    { type: str, format: email, unique: true }
      bio:      { type: str, max_length: 1000, optional: true }

  AuthorCreate:
    base: Author
    include: [name, email, bio]

  # ---- Posts ----
  Post:
    fields:
      id:         { type: uuid, primary: true, auto: true }
      title:      { type: str, min_length: 1, max_length: 200 }
      slug:       { type: str, regex: "^[a-z0-9-]+$", unique: true }
      body:       { type: str }
      status:     { type: enum, values: [draft, published, archived], default: draft }
      author_id:  { type: uuid }
      tags:       { type: list, items: str }
      view_count: { type: int, default: 0 }
      created_at: { type: datetime, auto_now: true }

  PostCreate:
    base: Post
    include: [title, slug, body, status, author_id, tags]

  PostUpdate:
    base: Post
    include: [title, slug, body, status, tags]
    all_optional: true

  PostResponse:
    base: Post
    exclude: [view_count]

  # ---- Comments ----
  Comment:
    fields:
      id:         { type: uuid, primary: true, auto: true }
      post_id:    { type: uuid }
      author_name:{ type: str, max_length: 100 }
      body:       { type: str, min_length: 1, max_length: 2000 }
      created_at: { type: datetime, auto_now: true }

  CommentCreate:
    base: Comment
    include: [post_id, author_name, body]

errors:
  NotFound:
    status: 404
    body: { message: "Resource not found" }

  Conflict:
    status: 409
    body: { message: "Resource already exists" }

  Forbidden:
    status: 403
    body: { message: "Action not allowed" }

routes:
  # ---- Author CRUD ----
  - path: /authors
    method: GET
    action: db.list
    model: Author
    pagination: true
    filters: [name]
    tags: [authors]

  - path: /authors/{id}
    method: GET
    action: db.get
    model: Author
    errors: { not_found: NotFound }
    tags: [authors]

  - path: /authors
    method: POST
    action: db.create
    model: Author
    request_model: AuthorCreate
    tags: [authors]

  # ---- Post CRUD ----
  - path: /posts
    method: GET
    action: db.list
    model: Post
    response_model: PostResponse
    pagination: { default_limit: 20, max_limit: 100 }
    filters: [status, author_id]
    tags: [posts]

  - path: /posts/{id}
    method: GET
    action: db.get
    model: Post
    response_model: PostResponse
    errors: { not_found: NotFound }
    tags: [posts]

  - path: /posts
    method: POST
    action: db.create
    model: Post
    request_model: PostCreate
    response_model: PostResponse
    tags: [posts]

  - path: /posts/{id}
    method: PATCH
    action: db.update
    model: Post
    request_model: PostUpdate
    response_model: PostResponse
    errors: { not_found: NotFound }
    tags: [posts]

  - path: /posts/{id}
    method: DELETE
    action: db.delete
    model: Post
    errors: { not_found: NotFound }
    tags: [posts]

  # ---- Post actions (custom handlers) ----
  - path: /posts/{id}/publish
    method: POST
    handler: handlers.posts.publish_post
    response_model: PostResponse
    errors:
      not_found: NotFound
      already_published: { status: 409, body: { message: "Post is already published" } }
    tags: [posts]

  # ---- Comment CRUD ----
  - path: /comments
    method: GET
    action: db.list
    model: Comment
    pagination: true
    filters: [post_id]
    tags: [comments]

  - path: /comments
    method: POST
    action: db.create
    model: Comment
    request_model: CommentCreate
    tags: [comments]

  - path: /comments/{id}
    method: DELETE
    action: db.delete
    model: Comment
    tags: [comments]

middleware:
  - kind: cors
    config:
      origins: ["http://localhost:3000", "https://blog.example.com"]
      methods: ["*"]
      headers: ["*"]
  - kind: gzip
    config:
      minimum_size: 500
```

### Compile

```bash
python -m src.restgen compile examples/07_full_app.yaml --out examples/07_generated -v
```

This generates a complete blog API with:
- 3 resources (authors, posts, comments)
- 11 endpoints
- Filtering and pagination
- Custom publish handler
- CORS + gzip middleware
- OpenAPI docs at `/api/v2/docs`

---

## Example 8: Middleware options

```yaml
# CORS -- Cross-Origin Resource Sharing
middleware:
  - kind: cors
    config:
      origins: ["http://localhost:3000"]     # allowed origins
      methods: ["GET", "POST", "PUT", "DELETE"]
      headers: ["Authorization", "Content-Type"]

  # Trusted hosts
  - kind: trustedhost
    config:
      hosts: ["api.example.com", "*.example.com"]

  # Gzip compression
  - kind: gzip
    config:
      minimum_size: 1000    # compress responses > 1KB
```

---

## Example 9: Error handling patterns

### Named errors (reusable)

```yaml
errors:
  NotFound:
    status: 404
    body: { message: "Resource not found" }

  Unauthorized:
    status: 401
    body: { message: "Authentication required" }

  Forbidden:
    status: 403
    body: { message: "Insufficient permissions" }

  Conflict:
    status: 409
    body: { message: "Resource conflict" }

  RateLimited:
    status: 429
    body: { message: "Too many requests" }
```

### Using errors on routes

```yaml
routes:
  - path: /items/{id}
    method: GET
    action: db.get
    model: Item
    errors:
      # Reference a named error
      not_found: NotFound

  - path: /items
    method: POST
    action: db.create
    model: Item
    request_model: ItemCreate
    errors:
      # Inline error for this route only
      duplicate:
        status: 409
        body: { message: "An item with this name already exists" }
      # Mix named and inline
      unauthorized: Unauthorized
```

Each named error generates an exception class:

```python
class NotFoundError(HTTPException):
    def __init__(self, detail: str = 'Resource not found', **kwargs):
        super().__init__(status_code=404, detail=detail, **kwargs)
```

---

## Example 10: URL shortener with Redis and rate limiting

A production-style URL shortener using Redis for storage and rate limiting.

### Config

```yaml
# examples/10_url_shortener.yaml
name: url_shortener
version: "1.0"
description: "URL shortener with Redis storage and rate limiting"

database:
  type: redis
  url: redis://localhost:6379/0

models:
  ShortLink:
    fields:
      id:           { type: str, primary: true }
      original_url: { type: str, min_length: 1, max_length: 2048 }
      clicks:       { type: int, default: 0 }
      created_at:   { type: datetime, auto_now: true }

  CreateLinkRequest:
    fields:
      url:          { type: str, min_length: 1, max_length: 2048 }
      custom_alias: { type: str, optional: true, min_length: 3, max_length: 20 }

  CreateLinkResponse:
    fields:
      short_url:    { type: str }
      original_url: { type: str }
      alias:        { type: str }

  LinkStats:
    fields:
      alias:        { type: str }
      original_url: { type: str }
      clicks:       { type: int }
      created_at:   { type: datetime }

errors:
  NotFound:
    status: 404
    body: { message: "Short link not found" }

  AliasConflict:
    status: 409
    body: { message: "Alias already taken" }

  InvalidUrl:
    status: 422
    body: { message: "Invalid URL format" }

routes:
  # Create short link -- custom handler for alias generation
  - path: /shorten
    method: POST
    handler: handlers.shortener.create_short_link
    request_model: CreateLinkRequest
    response_model: CreateLinkResponse
    errors:
      alias_taken: AliasConflict
      invalid_url: InvalidUrl
    tags: [links]

  # Redirect -- custom handler for click tracking + redirect
  - path: /{alias}
    method: GET
    handler: handlers.shortener.redirect_to_url
    errors:
      not_found: NotFound
    tags: [redirect]

  # Stats -- pipeline: fetch link then format response
  - path: /{alias}/stats
    method: GET
    response_model: LinkStats
    errors:
      not_found: NotFound
    pipeline:
      - action: db.get
        model: ShortLink
        args: { id: "$path.alias" }
        as: link
      - action: transform
        handler: handlers.shortener.format_stats
        args: { link: "$link" }
        as: stats
    tags: [stats]

  # Admin: list all links
  - path: /admin/links
    method: GET
    action: db.list
    model: ShortLink
    pagination: { default_limit: 50, max_limit: 200 }
    tags: [admin]

  # Admin: delete a link
  - path: /admin/links/{alias}
    method: DELETE
    action: db.delete
    model: ShortLink
    errors:
      not_found: NotFound
    tags: [admin]

middleware:
  - kind: cors
    config:
      origins: ["*"]
      methods: ["*"]
      headers: ["*"]

  - kind: rate_limit
    config:
      rate: "30/minute"
      storage_uri: "redis://localhost:6379/1"
```

### Handler code

```python
# handlers/shortener.py
import string
import random
from urllib.parse import urlparse

from fastapi import HTTPException
from fastapi.responses import RedirectResponse


def _generate_alias(length: int = 6) -> str:
    """Generate a random short alias. Sig: 2026-04-15 created"""
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


def _validate_url(url: str) -> str:
    """Validate and normalize a URL. Sig: 2026-04-15 created"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")
    if not parsed.netloc:
        raise HTTPException(status_code=422, detail="Invalid URL format")
    return url


async def create_short_link(body, repo):
    """Create a shortened URL with optional custom alias.

    Args:
        body: CreateLinkRequest with url and optional custom_alias.
        repo: Repository instance (Redis-backed).

    Returns:
        CreateLinkResponse with the short URL and alias.

    Sig: 2026-04-15 created
    """
    original_url = _validate_url(body.url)

    # Use custom alias or generate one
    alias = body.custom_alias if body.custom_alias else _generate_alias()

    # Check for alias collision
    existing = await repo.get(ShortLink, alias)
    if existing is not None:
        if body.custom_alias:
            raise HTTPException(status_code=409, detail="Alias already taken")
        # Retry with new random alias (up to 3 times)
        for _ in range(3):
            alias = _generate_alias()
            if await repo.get(ShortLink, alias) is None:
                break
        else:
            raise HTTPException(status_code=500, detail="Failed to generate unique alias")

    # Store in Redis
    await repo.create(ShortLink, {
        "id": alias,
        "original_url": original_url,
        "clicks": 0,
    })

    return {
        "short_url": f"/{alias}",
        "original_url": original_url,
        "alias": alias,
    }


async def redirect_to_url(alias, repo):
    """Redirect to the original URL and increment click count.

    Args:
        alias: The short link alias from the path.
        repo: Repository instance.

    Returns:
        RedirectResponse to the original URL.

    Sig: 2026-04-15 created
    """
    link = await repo.get(ShortLink, alias)
    if link is None:
        raise HTTPException(status_code=404, detail="Short link not found")

    # Increment click counter
    await repo.update(ShortLink, alias, {"clicks": link.clicks + 1})

    return RedirectResponse(url=link.original_url, status_code=307)


async def format_stats(link):
    """Format link data into stats response.

    Args:
        link: The ShortLink model instance.

    Returns:
        Dict with stats fields.

    Sig: 2026-04-15 created
    """
    return {
        "alias": link.id,
        "original_url": link.original_url,
        "clicks": link.clicks,
        "created_at": link.created_at,
    }
```

### Compile and run

```bash
# Install Redis dependencies
pip install redis slowapi

# Start Redis (if not running)
redis-server &

# Compile
python -m src.restgen compile examples/10_url_shortener.yaml --out examples/10_generated -v

# Run
cd examples && uvicorn 10_generated.app:app --port 8000
```

### Test

```bash
# Shorten a URL
curl -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/very/long/path/to/something"}'
# => {"short_url": "/abc123", "original_url": "...", "alias": "abc123"}

# Shorten with custom alias
curl -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "custom_alias": "mylink"}'

# Redirect (follow with -L)
curl -L http://localhost:8000/abc123

# View stats
curl http://localhost:8000/abc123/stats
# => {"alias": "abc123", "original_url": "...", "clicks": 5, "created_at": "..."}

# Admin: list all links
curl http://localhost:8000/admin/links

# Admin: delete a link
curl -X DELETE http://localhost:8000/admin/links/abc123

# Rate limiting kicks in after 30 requests/minute:
# => 429 {"error": "Rate limit exceeded: 30 per 1 minute"}
```

### What makes this different

- **Redis adapter**: All data stored in Redis with O(1) lookups and sorted-set indexing
- **Rate limiting**: Built-in via `slowapi` backed by the same Redis instance (different DB)
- **Mixed tiers**: Custom handlers for shorten/redirect, pipeline for stats, CRUD for admin
- **Click tracking**: Atomic counter increment on every redirect
- **No ORM overhead**: Direct Redis JSON storage, fast reads/writes

---

## Tips

### Auto-generated route names

If you don't set `name` on a route, restgen generates one from the path and method:

| Method | Path | Generated name |
|--------|------|---------------|
| GET | /users | `list_users` |
| GET | /users/{id} | `get_user` |
| POST | /users | `create_user` |
| PUT | /users/{id} | `update_user` |
| DELETE | /users/{id} | `delete_user` |
| POST | /users/{id}/deactivate | `deactivate_user` |

### Path parameter types

Path params are auto-typed from the model's primary key. If your model has `id: { type: uuid, primary: true }`, then `{id}` in the path becomes `id: UUID` in the function signature.

### Validation

```bash
# Check config without generating code
python -m src.restgen validate api.yaml

# Dry run -- full compile check, no file output
python -m src.restgen compile api.yaml --dry-run
```

The validator catches:
- Duplicate model/route/error names
- Missing model references
- Circular model inheritance
- Routes without action/pipeline/handler
- CRUD routes without a model
- Invalid pipeline `$references`
- Unknown error references

### Generated code is standalone

The generated code has no dependency on restgen. You can:
- Copy it to another project
- Edit it by hand
- Deploy it directly
- Use it as a starting point and iterate

### Database switching

Change one line to switch databases:

```yaml
# Development
database:
  type: memory

# Production
database:
  type: postgres
  url: ${DATABASE_URL}
```

The generated code uses a repository abstraction. All routes call `repo.get()`, `repo.list()`, etc. -- the adapter handles the actual database calls.

---

## Example 11: Authenticated API with File Uploads

A real-world pattern: JWT-protected endpoints with file uploads and background processing.

### Config

```yaml
# examples/file_uploads.yaml
name: Document API
version: "1.0"

database:
  type: memory

security:
  jwt:
    type: oauth2
    flow: password
    token_url: /auth/token
    verify_handler: auth.verify_token

models:
  Document:
    fields:
      id: { type: uuid, primary: true, auto: true }
      title: { type: str }
      file_path: { type: str }
      content_type: { type: str }
      size_bytes: { type: int }
      owner_id: { type: uuid }
      uploaded_at: { type: datetime, auto_now: true }

routes:
  - path: /documents/upload
    method: POST
    handler: documents.upload_single
    auth: jwt
    files:
      - name: file
        multiple: false
        max_size: 50mb
        accept: [".pdf", ".docx"]
    depends:
      - auth.get_current_user
    background_tasks:
      - handler: documents.index_document
        args: { doc_id: "$result.id" }

  - path: /documents/{id}/download
    method: GET
    handler: documents.download
    auth: jwt
    response_type: file
```

### Handler

```python
# documents.py
from fastapi import UploadFile
from fastapi.responses import FileResponse
import aiofiles
from pathlib import Path

UPLOAD_DIR = Path("uploads")

async def upload_single(file: UploadFile, current_user, repo):
    """Save uploaded file and create DB record."""
    dest = UPLOAD_DIR / f"{current_user.id}/{file.filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(await file.read())
    return await repo.create(Document, {
        "title": file.filename,
        "file_path": str(dest),
        "content_type": file.content_type,
        "size_bytes": file.size,
        "owner_id": current_user.id,
    })

async def download(id, repo):
    """Return the file for download."""
    doc = await repo.get(Document, id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return FileResponse(path=doc.file_path, media_type=doc.content_type)

async def index_document(doc_id):
    """Background task: index document for search."""
    # ... your indexing logic
    pass
```

### What gets generated

```python
# routes.py (excerpt)
from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from fastapi.responses import FileResponse
from .security import get_current_user_jwt
from documents import upload_single, download
from documents import index_document

@router.post('/documents/upload', status_code=201)
async def upload_document(file: UploadFile = File(...), background_tasks: BackgroundTasks,
                          current_user=Depends(get_current_user), repo=Depends(get_repository)):
    result = await upload_single(file=file, current_user=current_user, repo=repo)
    background_tasks.add_task(index_document, doc_id=result.id)
    return result

@router.get('/documents/{id}/download', status_code=200)
async def download_document(id: UUID, repo=Depends(get_repository)):
    return await download(id=id, repo=repo)
```

---

## Example 12: WebSocket Chat with Health Checks

Real-time chat with lifecycle handlers and infrastructure probes.

### Config

```yaml
# examples/websocket_chat.yaml
name: Chat API
version: "1.0"

database:
  type: memory

health_check:
  path: /health
  ready_path: /ready
  include_db: true

websockets:
  - path: /ws/chat/{room_id}
    name: ws_chat
    handler: chat.on_message
    on_connect: chat.on_connect
    on_disconnect: chat.on_disconnect
```

### Handler

```python
# chat.py
from fastapi import WebSocket

connected = {}  # room_id -> set of websockets

async def on_connect(websocket: WebSocket, room_id: str):
    connected.setdefault(room_id, set()).add(websocket)

async def on_message(websocket: WebSocket, room_id: str, data: str):
    for ws in connected.get(room_id, set()):
        await ws.send_text(f"{data}")

async def on_disconnect(websocket: WebSocket, room_id: str):
    connected.get(room_id, set()).discard(websocket)
```

### What gets generated

```python
# websockets.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from chat import on_message, on_connect, on_disconnect

router = APIRouter()

@router.websocket('/ws/chat/{room_id}')
async def ws_chat(websocket: WebSocket, room_id: str):
    await websocket.accept()
    await on_connect(websocket=websocket, room_id=room_id)
    try:
        while True:
            data = await websocket.receive_text()
            await on_message(websocket=websocket, room_id=room_id, data=data)
    except WebSocketDisconnect:
        await on_disconnect(websocket=websocket, room_id=room_id)
```

```python
# health.py
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from .dependencies import get_repository

health_router = APIRouter(tags=["health"])

@health_router.get("/health", status_code=200)
async def health_check():
    return {"status": "healthy"}

@health_router.get("/ready", status_code=200)
async def readiness_check(repo=Depends(get_repository)):
    try:
        await repo.health_check()
        return {"status": "ready", "database": "connected"}
    except Exception:
        return JSONResponse(status_code=503,
            content={"status": "not_ready", "database": "disconnected"})
```
