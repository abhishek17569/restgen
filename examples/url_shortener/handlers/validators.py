"""Custom alias validation: reserved words and profanity filtering.

Blocks aliases that conflict with application routes or contain
offensive language to protect the service's reputation.

Sig: 2026-05-08 created
"""

from __future__ import annotations

from better_profanity import profanity
from fastapi import HTTPException

RESERVED_WORDS: set[str] = {
    "admin", "api", "shorten", "stats", "health", "login", "signup",
    "pricing", "about", "help", "docs", "webhook", "webhooks", "bulk",
    "links", "qr", "preview", "access", "settings", "dashboard",
    "analytics", "static", "assets", "favicon", "robots",
}

profanity.load_censor_words()


def validate_custom_alias(alias: str) -> str:
    """Reject aliases that collide with reserved routes or contain profanity.

    Args:
        alias: Candidate short link identifier supplied by the caller.

    Returns:
        The ``alias`` unchanged when it passes both checks.

    Raises:
        HTTPException: 422 when the alias matches a reserved word
            (case-insensitive) or triggers the profanity filter.

    Sig: 2026-05-08 created
    """
    if alias.lower() in RESERVED_WORDS or profanity.contains_profanity(alias):
        raise HTTPException(
            status_code=422,
            detail="Alias contains reserved or inappropriate words",
        )
    return alias
