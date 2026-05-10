"""Shared pipeline-scope model used by validator and emitter.

A ``Scope`` tracks which ``$ref`` names are visible at a given point in a
pipeline. Both the validator and the emitter consult this single source of
truth so they never disagree about which references resolve (see Risk #4
in the plan).

Lexical rules:

* Built-in prefixes (``$path.``, ``$body``, ``$query.``, ``$header.``,
  ``$request``) are always in scope — they do not participate in frames.
* Each step's ``as_name`` becomes a binding in the current frame **after**
  the step's statements emit.
* ``for_each`` pushes a frame carrying ``{loop_var: "bound"}``; the frame
  is popped after the loop body, so inner ``as_name`` bindings do not
  survive the loop.
* ``if`` branches each evaluate in their own child frame. Names bound in
  **both** branches are promoted to the parent (intersection) after the
  ``if`` closes.

Sig: 2026-04-24 created
"""
from __future__ import annotations


BUILTIN_REF_PREFIXES = ("$path.", "$body", "$query.", "$header.", "$request")


class Scope:
    """Lexical-scope stack for pipeline ``$ref`` resolution.

    Sig: 2026-04-24 created
    """

    def __init__(self) -> None:
        """Sig: 2026-04-24 created"""
        self._frames: list[set[str]] = [set()]

    # ------------------------------------------------------------------ push/pop
    def push(self, initial: set[str] | None = None) -> None:
        """Push a new frame onto the stack.

        Args:
            initial: Names pre-bound in the new frame (e.g. the loop var).

        Sig: 2026-04-24 created
        """
        self._frames.append(set(initial or set()))

    def pop(self) -> set[str]:
        """Pop the top frame and return the names it held.

        Returns:
            The set of names that were bound in the popped frame.

        Raises:
            IndexError: If only the root frame remains (programming error).

        Sig: 2026-04-24 created
        """
        if len(self._frames) == 1:
            raise IndexError("cannot pop the root scope frame")
        return self._frames.pop()

    # ------------------------------------------------------------------ bindings
    def bind(self, name: str) -> None:
        """Bind ``name`` in the current (top) frame.

        Sig: 2026-04-24 created
        """
        if name:
            self._frames[-1].add(name)

    def promote(self, names: set[str]) -> None:
        """Promote ``names`` into the current frame.

        Used when ``if`` branches agree on bindings — the intersection
        of their produced names is visible after the join.

        Sig: 2026-04-24 created
        """
        self._frames[-1].update(names)

    # ------------------------------------------------------------------ lookup
    def has(self, name: str) -> bool:
        """Return ``True`` if ``name`` is bound anywhere on the stack.

        Sig: 2026-04-24 created
        """
        return any(name in frame for frame in self._frames)

    def snapshot(self) -> set[str]:
        """Return the set of all currently-bound names across all frames.

        Sig: 2026-04-24 created
        """
        out: set[str] = set()
        for frame in self._frames:
            out |= frame
        return out

    # ------------------------------------------------------------------ ref check
    @staticmethod
    def is_builtin_ref(ref: str) -> bool:
        """Return True if ``ref`` names a built-in (request-scoped) binding.

        Sig: 2026-04-24 created
        """
        return any(ref.startswith(prefix) for prefix in BUILTIN_REF_PREFIXES)

    def resolves(self, ref: str) -> bool:
        """Return True if ``$ref`` expression resolves in the current scope.

        Accepts dotted-access (``$user.email`` checks ``user``).

        Sig: 2026-04-24 created
        """
        if not ref.startswith("$"):
            return True
        if self.is_builtin_ref(ref):
            return True
        head = ref[1:].split(".", 1)[0]
        return self.has(head)
