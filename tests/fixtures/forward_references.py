# rety fixture: forward_references.py
#
# Forward references and self-referential types using `from __future__ import annotations`.
# Checkers differ on how they resolve forward references, especially for:
#   - Recursive type definitions (linked list, tree nodes)
#   - Optional attributes that reference the containing class
#   - Accessing attributes on Optional types without guards
#
# Expected disagreement pattern:
#   Line 27: n.next.value — n.next is Optional[Node]; calling .value without
#            a None guard should be flagged by all checkers.
#            Line blame varies: some blame the attribute access, some blame
#            the expression.
#   Line 33: deep_value(None) — passing None where Node expected (if not Optional).

from __future__ import annotations

from typing import Optional


class Node:
    def __init__(self, value: int, next: Optional[Node] = None) -> None:
        self.value = value
        self.next = next


def first_value(n: Node) -> int:
    # Accessing .value on Optional[Node] without a guard — should error
    return n.next.value   # n.next might be None


def deep_value(n: Optional[Node]) -> Optional[int]:
    if n is None:
        return None
    return n.next.value   # n.next is still Optional[Node] — should error


def safe_traverse(n: Node) -> list[int]:
    values = []
    current: Optional[Node] = n
    while current is not None:
        values.append(current.value)  # safe: current is narrowed to Node
        current = current.next
    return values


class Tree:
    """Binary tree — doubly self-referential."""
    def __init__(
        self,
        value: int,
        left: Optional[Tree] = None,
        right: Optional[Tree] = None,
    ) -> None:
        self.value = value
        self.left = left
        self.right = right

    def sum(self) -> int:
        total = self.value
        if self.left:
            total += self.left.sum()
        if self.right:
            total += self.right.sum()
        return total


def tree_depth(t: Optional[Tree]) -> int:
    if t is None:
        return 0
    return 1 + max(tree_depth(t.left), tree_depth(t.right))
