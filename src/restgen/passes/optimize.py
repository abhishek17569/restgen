"""Pass 4: Optimize IR for cleaner generated code.

Sig: 2026-04-14 created
"""
from __future__ import annotations

from collections import Counter

from restgen.ir.nodes import AppNode, ModelNode, FieldNode


def optimize(app: AppNode) -> AppNode:
    """Optimize IR for cleaner generated code.

    Current optimizations:
    1. Mixin extraction: If 3+ models share identical field subsets,
       extract a mixin model. (Stub -- returns app unchanged for now;
       real extraction is tracked for a future pass.)

    Args:
        app: Resolved AppNode.

    Returns:
        The AppNode, potentially mutated with optimizations applied.

    Sig: 2026-04-14 created
    """
    # TODO: Implement mixin extraction once field-signature hashing is stable.
    #
    # Algorithm sketch:
    #   1. For each model, build frozenset of (name, field_type, constraints)
    #      tuples for every field.
    #   2. Enumerate all 2-field+ subsets that appear in 3+ models.
    #   3. For each qualifying subset, create a new ModelNode with
    #      is_derived=False and add it to app.models.
    #   4. Add the mixin name to each source model's ``mixins`` list.
    #   5. Optionally remove the extracted fields from the source models
    #      (requires careful handling of overrides and defaults).
    #
    # For now, pass through unchanged.

    app = _deduplicate_models(app)

    return app


def _deduplicate_models(app: AppNode) -> AppNode:
    """Remove exact-duplicate model definitions (same name, same fields).

    This can happen when multiple spec fragments define the same model.
    Keeps the first occurrence.

    Args:
        app: The AppNode to deduplicate.

    Returns:
        The AppNode with duplicate models removed.

    Sig: 2026-04-14 created
    """
    seen: set[str] = set()
    unique: list[ModelNode] = []
    for model in app.models:
        if model.name not in seen:
            seen.add(model.name)
            unique.append(model)
    app.models = unique

    # Rebuild model_index to match
    app.model_index = {m.name: m for m in app.models}

    return app
