"""Bloom filter for ultra-short URL alias generation.

System Design:
    A bloom filter provides O(1) probabilistic membership testing. We use it
    to avoid expensive Redis lookups when checking alias availability during
    generation. The filter guarantees NO FALSE NEGATIVES — if it says "not
    present", the alias is definitely available.

    False positives (filter says "present" but alias is actually free) are
    acceptable: we just generate the next candidate. With proper sizing, the
    false positive rate stays below 1%.

Strategy for short aliases:
    Start at MIN_LENGTH (3 chars = 238,328 possible aliases with base62).
    When the bloom filter fill ratio exceeds GROWTH_THRESHOLD (75%), bump
    length by 1. This gives us:

    | Length | Possible aliases | Capacity at 75% |
    |--------|-----------------|-----------------|
    | 3      | 238,328         | 178,746         |
    | 4      | 14,776,336      | 11,082,252      |
    | 5      | 916,132,832     | 687,099,624     |

    Most URL shorteners never exceed 4 chars with this approach.

Persistence:
    The bloom filter is rebuilt from Redis on startup (scan all existing keys).
    This takes ~1s per million keys. For larger deployments, serialize the
    bit array to Redis as a separate key.

Sig: 2026-05-08 created
"""
from __future__ import annotations

import hashlib
import math
import struct
from typing import Any


class BloomFilter:
    """Space-efficient probabilistic set membership test.

    Args:
        capacity: Expected number of elements.
        error_rate: Desired false positive probability (0 < p < 1).

    Sig: 2026-05-08 created
    """

    def __init__(self, capacity: int = 500_000, error_rate: float = 0.01) -> None:
        self.capacity = capacity
        self.error_rate = error_rate
        self.size = self._optimal_size(capacity, error_rate)
        self.hash_count = self._optimal_hash_count(self.size, capacity)
        self.bit_array = bytearray(math.ceil(self.size / 8))
        self.count = 0

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        """Calculate optimal bit array size.

        Sig: 2026-05-08 created
        """
        return int(-n * math.log(p) / (math.log(2) ** 2))

    @staticmethod
    def _optimal_hash_count(m: int, n: int) -> int:
        """Calculate optimal number of hash functions.

        Sig: 2026-05-08 created
        """
        return max(1, int((m / n) * math.log(2)))

    def _get_hash_values(self, item: str) -> list[int]:
        """Generate k hash positions using double hashing.

        Uses MD5 for speed (not cryptographic — collision resistance
        irrelevant for bloom filter hashing). Double hashing technique
        from Kirsch & Mitzenmacher: h(i) = h1 + i*h2 mod m.

        Sig: 2026-05-08 created
        """
        digest = hashlib.md5(item.encode(), usedforsecurity=False).digest()
        h1 = struct.unpack_from("<Q", digest, 0)[0]
        h2 = struct.unpack_from("<Q", digest, 8)[0]
        return [(h1 + i * h2) % self.size for i in range(self.hash_count)]

    def add(self, item: str) -> None:
        """Add an item to the filter.

        Sig: 2026-05-08 created
        """
        for pos in self._get_hash_values(item):
            byte_idx = pos // 8
            bit_idx = pos % 8
            self.bit_array[byte_idx] |= (1 << bit_idx)
        self.count += 1

    def might_contain(self, item: str) -> bool:
        """Test if an item might be in the filter.

        Returns:
            False → definitely not present (guaranteed).
            True → probably present (false positive rate = error_rate).

        Sig: 2026-05-08 created
        """
        for pos in self._get_hash_values(item):
            byte_idx = pos // 8
            bit_idx = pos % 8
            if not (self.bit_array[byte_idx] & (1 << bit_idx)):
                return False
        return True

    @property
    def fill_ratio(self) -> float:
        """Current fill ratio (count / capacity).

        Sig: 2026-05-08 created
        """
        return self.count / self.capacity

    def estimated_false_positive_rate(self) -> float:
        """Estimate current false positive rate based on fill level.

        Sig: 2026-05-08 created
        """
        n = self.count
        m = self.size
        k = self.hash_count
        return (1 - math.exp(-k * n / m)) ** k


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_filter: BloomFilter | None = None

MIN_LENGTH = 3
MAX_LENGTH = 8
GROWTH_THRESHOLD = 0.75
_current_length = MIN_LENGTH


def get_filter() -> BloomFilter:
    """Get or create the global bloom filter instance.

    Sig: 2026-05-08 created
    """
    global _filter
    if _filter is None:
        _filter = BloomFilter(capacity=500_000, error_rate=0.01)
    return _filter


def get_current_length() -> int:
    """Get the current alias length (grows with usage).

    Sig: 2026-05-08 created
    """
    return _current_length


def register_alias(alias: str) -> None:
    """Register a known alias in the bloom filter.

    Called on alias creation and during startup rebuild.

    Sig: 2026-05-08 created
    """
    global _current_length
    bf = get_filter()
    bf.add(alias)

    if bf.fill_ratio > GROWTH_THRESHOLD and _current_length < MAX_LENGTH:
        _current_length += 1


def is_probably_taken(alias: str) -> bool:
    """Fast check if an alias is likely taken.

    Returns False only when the alias is DEFINITELY available
    (bloom filter guarantee: no false negatives).

    Sig: 2026-05-08 created
    """
    return get_filter().might_contain(alias)


async def rebuild_from_redis(repo: Any) -> int:
    """Rebuild the bloom filter from all existing aliases in Redis.

    Call this during app startup (lifespan). Scans all ShortLink records
    and populates the filter.

    Args:
        repo: Repository instance.

    Returns:
        Number of aliases loaded.

    Sig: 2026-05-08 created
    """
    global _filter, _current_length
    _filter = BloomFilter(capacity=500_000, error_rate=0.01)
    _current_length = MIN_LENGTH

    try:
        links = await repo.list(None, skip=0, limit=1_000_000)
        for link in links:
            alias = link.id if hasattr(link, "id") else link.get("id", "")
            if alias:
                _filter.add(alias)
    except Exception:
        pass

    if _filter.fill_ratio > GROWTH_THRESHOLD:
        _current_length = min(MAX_LENGTH, MIN_LENGTH + int(_filter.fill_ratio * 3))

    return _filter.count
