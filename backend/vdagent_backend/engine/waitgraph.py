"""Per-user wait-for graph over agents (§4.4).

Edge `a → b` exists while a running invocation of agent `a` has an accepted, unresolved call to
`b`. Parallel calls from one step to the same target are separate acceptances, so edges are
counted; an edge disappears when its last acceptance is resolved.
"""

from __future__ import annotations

from collections import Counter


class WaitGraph:
    def __init__(self) -> None:
        self._edges: Counter[tuple[str, str]] = Counter()

    def add(self, src: str, dst: str) -> None:
        self._edges[(src, dst)] += 1

    def remove(self, src: str, dst: str) -> None:
        key = (src, dst)
        count = self._edges[key]
        if count <= 1:
            self._edges.pop(key, None)
        else:
            self._edges[key] = count - 1

    def has_path(self, src: str, dst: str) -> bool:
        """True if `dst` is reachable from `src` following edges (a node reaches itself)."""
        seen = {src}
        todo = [src]
        while todo:
            node = todo.pop()
            if node == dst:
                return True
            for a, b in self._edges:
                if a == node and b not in seen:
                    seen.add(b)
                    todo.append(b)
        return False

    def __bool__(self) -> bool:
        return bool(self._edges)
