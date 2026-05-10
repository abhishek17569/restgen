# Why restgen?

## The Problem

Building production-grade FastAPI applications requires writing the same boilerplate patterns over and over:

- Pydantic models with field validators
- Async route functions with `Depends()` injection
- Repository patterns for database access
- Error handling with custom exception classes
- Middleware registration (CORS, rate limiting, GZip)
- Security schemes (OAuth2, API keys)
- OpenAPI metadata

A single CRUD resource (5 endpoints) requires ~200 lines of correctly wired Python. A real API with 20+ resources, auth, file uploads, WebSockets, and caching easily reaches 3,000–5,000 lines — most of it structural, not logic.

## The Solution

restgen replaces structural code with a 30-line YAML declaration:

```
30 lines YAML  →  compiler  →  500+ lines of production FastAPI Python
```

You keep writing Python for business logic. restgen handles the wiring.

---

## Why this matters for AI-assisted development

### 1. Smaller models can write YAML accurately

Writing correct FastAPI code requires understanding:
- Pydantic v2 model syntax (validators, `model_dump`, `ConfigDict`)
- FastAPI's dependency injection (`Depends`, `Security`, yield deps)
- Async/await patterns with proper error propagation
- Import graphs across 6+ files
- Correct type annotations for every parameter

A smaller, cheaper model (Haiku, GPT-4o-mini, local 7B) frequently gets these wrong — missing imports, incorrect `Depends` wiring, outdated Pydantic v1 syntax, sync where async is needed.

**restgen's YAML schema is 10x simpler.** A small model can reliably produce:
```yaml
- path: /users/{id}
  method: GET
  action: db.get
  model: User
  errors: { not_found: NotFound }
  cache: { max_age: 300 }
```

This compiles to correct, idiomatic FastAPI every time. The model doesn't need to know about `Depends(get_repository)`, `make_assign`, `HTTPException`, or `response_model=list[User]` — the compiler handles that.

### 2. 10–50x fewer tokens per API definition

| Approach | Tokens to define 5 CRUD endpoints |
|----------|-----------------------------------|
| Hand-written FastAPI | ~2,000 tokens (models + routes + deps + errors) |
| restgen YAML | ~150 tokens |
| **Savings** | **~93%** |

For an LLM generating an API, this means:
- Faster generation (fewer output tokens)
- Cheaper per request (especially on pay-per-token APIs)
- Fits in smaller context windows
- Less room for hallucination (constrained output space)

### 3. Compile-time correctness guarantees

When an LLM generates YAML, the restgen compiler catches errors **before runtime**:

| Error | When caught | Without restgen |
|-------|-------------|-----------------|
| Unknown model reference | Compile time (E001) | Runtime `NameError` |
| Missing handler function | Compile time (E004) | Runtime `ImportError` |
| Invalid field type | Compile time (E010) | Runtime Pydantic `ValidationError` |
| File upload on GET route | Compile time (E053) | Silent failure |
| Broken `$ref` in pipeline | Compile time (E062) | Runtime `KeyError` |

30+ validation rules (E001–E083) mean that **if it compiles, it runs.** The LLM's output is validated by a real compiler, not just syntax-checked.

And at runtime, the generated code guarantees no unhandled exceptions leak to clients:
- Every `HTTPException` returns structured JSON
- Pydantic validation failures return 422 with field-level error details
- Uncaught exceptions return 500 with a safe generic message (no stack traces)

### 4. Deterministic, reproducible output

Same YAML → same Python. Always.

- No temperature variance
- No prompt sensitivity
- No model version drift
- No "slightly different each time" imports or patterns

This matters for:
- CI/CD pipelines (compile in CI, deploy generated code)
- Code review (review the YAML diff, not 500 lines of Python diff)
- Rollbacks (revert YAML, recompile)

### 5. The LLM focuses on what matters

Without restgen, an LLM generating an API spends most of its tokens on structural code:
```
10% — actual business logic decisions
90% — boilerplate (imports, type annotations, DI wiring, error classes)
```

With restgen:
```
80% — business logic in handler functions
20% — YAML structure (which the compiler validates)
```

The LLM's reasoning capacity is spent on the hard parts — your domain logic, not FastAPI ceremony.

---

## Why this matters for human developers

### 1. Single source of truth

Your API is defined in one YAML file. Not scattered across models.py, routes.py, errors.py, middleware.py, dependencies.py. When you need to understand "what does this API do?", you read one file.

### 2. Zero-overhead generated code

restgen doesn't use a runtime framework. The generated code is what you'd write by hand:
- Direct `async def` functions
- Native FastAPI decorators
- Plain Pydantic `BaseModel` classes
- No proxy objects, no metaclass magic, no runtime reflection

Performance is identical to hand-written FastAPI. There is no restgen import in the generated code.

### 3. Escape hatch: eject at any time

Don't like restgen anymore? Keep the generated code. It's standalone Python. Delete the YAML and the compiler. Your app still runs.

### 4. Progressive complexity

Start with zero Python:
```yaml
action: db.list
```

Grow into pipelines when needed:
```yaml
pipeline:
  - { action: db.get, model: Order, as: order }
  - { action: validate, handler: validators.can_ship }
  - { action: side_effect, handler: events.emit_shipped }
```

Graduate to full handlers when logic demands it:
```yaml
handler: handlers.orders.complex_fulfillment
```

You never hit a wall. The framework grows with your needs.

### 5. Consistency across teams

restgen enforces a single architecture pattern:
- Repository pattern for all DB access
- Dependency injection for all cross-cutting concerns
- Named errors for all failure modes
- Typed models for all request/response shapes

No debates about "where does this code go?" — the compiler decides.

---

## Comparison

| | Hand-written FastAPI | restgen | Code generators (Swagger Codegen, etc.) |
|---|---|---|---|
| **Input** | Python code | YAML config | OpenAPI spec (JSON/YAML) |
| **Output quality** | Depends on developer | Consistent, optimized | Often verbose/ugly |
| **Runtime overhead** | None | None | Often has runtime deps |
| **AI-friendly** | Hard (large output space) | Easy (small, constrained schema) | Medium (spec is verbose) |
| **Validation** | Runtime only | Compile-time + runtime | Spec-level only |
| **Custom logic** | Inline | Handler functions | Inline or hooks |
| **Escape hatch** | N/A | Keep generated code | Keep generated code |
| **Learning curve** | FastAPI + Pydantic + SQLAlchemy | 10 YAML keys | OpenAPI spec format |

---

## When NOT to use restgen

- **Existing codebase**: If you already have 10K+ lines of FastAPI, restgen won't help. It's for new projects or new services.
- **Non-REST APIs**: GraphQL, gRPC, or pure WebSocket services don't fit the DSL.
- **Extreme customization**: If every endpoint is unique with no patterns, the YAML won't save you much over just writing Python.
- **Learning FastAPI**: If you're learning the framework, write it by hand first. Understand what the generated code does.

---

## The economics

For a typical AI-assisted development workflow:

| Metric | Without restgen | With restgen |
|--------|----------------|--------------|
| Tokens per endpoint (LLM output) | ~400 | ~30 |
| Model required for accuracy | Sonnet/GPT-4 | Haiku/GPT-4o-mini |
| Cost per API (20 endpoints) | ~$0.08 | ~$0.006 |
| Compile-time error detection | 0% | 95%+ |
| Time to working API | 5–15 min | 30 seconds |
| Lines to review | 500–2000 | 50–100 (YAML only) |

The ROI is clearest when:
1. You're generating many APIs (microservices, prototypes, SaaS tenants)
2. You're using AI to generate code (token cost reduction)
3. You need correctness guarantees (compile-time validation)
4. You want consistency across a team (single architecture pattern)
