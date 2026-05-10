# URL Shortener — System Design Example

A Redis-backed URL shortener with **bloom-filter-optimized alias generation** that produces the shortest possible URLs.

## Key Design: Bloom Filter for Short Aliases

### The Problem

Most URL shorteners generate fixed-length aliases (6-8 chars). This wastes namespace — with base62 encoding, 3 chars gives 238K possibilities, 4 chars gives 14.7M. A typical shortener with <100K links doesn't need 6 chars.

### The Solution

```
┌────────────────────────────────────────────────────────────────┐
│  Alias Generation Pipeline                                      │
│                                                                  │
│  1. Generate candidate (3 chars initially)                       │
│  2. Check bloom filter → "definitely available" (99% of cases)  │
│  3. Confirm with Redis (only on bloom "maybe taken" — 1%)       │
│  4. Register in bloom filter + write to Redis                    │
│  5. When filter 75% full → grow to 4 chars                      │
└────────────────────────────────────────────────────────────────┘
```

### Why Bloom Filter?

| Operation | Without bloom | With bloom |
|-----------|--------------|------------|
| Check alias available | Redis GET (network I/O) | Bit array lookup (nanoseconds) |
| Generate 1 alias | 1-4 Redis calls (collisions) | 0-1 Redis calls (bloom screens) |
| Generate 1000 aliases/sec | Redis bottleneck | CPU-bound (trivial) |

The bloom filter guarantees **no false negatives**: if it says "available", it IS available. False positives (says "taken" when actually free) just cause us to generate the next candidate — harmless.

### Alias Length Growth

| Current aliases | Alias length | URL example |
|----------------|-------------|-------------|
| 0 – 178K | 3 chars | `site.co/a7B` |
| 178K – 11M | 4 chars | `site.co/kX9p` |
| 11M – 687M | 5 chars | `site.co/mR4qZ` |

Growth is triggered when the bloom filter fill ratio exceeds 75%.

## Architecture

```
┌──────────┐    ┌──────────────────────────────────┐    ┌─────────┐
│  Client  │───▶│  FastAPI (generated)             │───▶│  Redis  │
│          │◀───│                                  │◀───│ (store) │
└──────────┘    │  ┌────────────┐  ┌────────────┐ │    └─────────┘
                │  │ Bloom      │  │ Shortener  │ │
                │  │ Filter     │  │ Handler    │ │
                │  │ (in-memory)│  │            │ │
                │  └────────────┘  └────────────┘ │
                └──────────────────────────────────┘
```

## Bloom Filter Internals

```python
# handlers/bloom.py

BloomFilter(capacity=500_000, error_rate=0.01)
# → bit array: ~4.8 MB
# → hash functions: 7 (double-hashing via MD5)
# → false positive rate at 75% fill: ~3%
```

- **Hash function**: Double hashing (Kirsch-Mitzenmacher) — `h(i) = h1 + i*h2 mod m`
- **Rebuild on startup**: Scans Redis for all existing aliases (~1s per 1M keys)
- **Memory**: ~5 MB for 500K capacity — negligible vs Redis connection overhead

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/shorten` | Create short link (auto or custom alias) |
| GET | `/{alias}` | Redirect to original URL (307) |
| GET | `/{alias}/stats` | View click count and metadata |
| GET | `/admin/links` | List all links (paginated) |
| DELETE | `/admin/links/{id}` | Delete a link |

## Run

```bash
# Start Redis
docker run -d -p 6379:6379 redis:alpine

# Compile
restgen compile api.yaml --out generated/

# Run
cd examples/url_shortener && uvicorn generated.app:app --reload
```

## Test

```bash
# Create a short link (gets 3-char alias)
curl -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/very/long/path"}'
# → {"short_url": "/a7B", "original_url": "...", "alias": "a7B"}

# Custom alias
curl -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "custom_alias": "mylink"}'

# Redirect (follow with -L, or check headers)
curl -I http://localhost:8000/a7B
# → 307 Location: https://example.com/very/long/path

# Stats
curl http://localhost:8000/a7B/stats
# → {"alias": "a7B", "original_url": "...", "clicks": 1, "created_at": "..."}
```

## Design Decisions

1. **Base62 encoding** (a-z, A-Z, 0-9) — URL-safe, case-sensitive, maximizes density per character.

2. **Bloom filter over Redis SET** — Checking membership in a Redis Set would work but adds a network round-trip per candidate. The bloom filter runs in-process at nanosecond latency.

3. **Growth threshold at 75%** — The bloom filter's false positive rate increases with fill ratio. At 75% fill, we accept ~3% false positives and bump the alias length to keep generation fast.

4. **Rebuild on startup** — The bloom filter is ephemeral (in-memory). On restart, we scan Redis to rebuild. For >1M keys, consider persisting the bit array as a Redis key (`SETRANGE`/`GETRANGE` on a binary string).

5. **No distributed bloom** — Each app instance has its own filter. In multi-instance deployments, a newly created alias might not be in another instance's filter — the Redis confirmation step handles this (aliases are still globally unique via Redis).

6. **307 redirect** (not 301) — 301 is cached by browsers. 307 forces the browser to always hit our server, so we can track clicks accurately.

## Files

```
url_shortener/
├── api.yaml                    # restgen config
├── handlers/
│   ├── __init__.py
│   ├── shortener.py            # create/redirect/stats handlers
│   └── bloom.py                # BloomFilter implementation + singleton
├── generated/                  # compiled FastAPI output
└── README.md
```
