from __future__ import annotations
from typing import Set
from .nodes import Node, Symbol, List as RirList

# Default denylist. This can be configured.
DEFAULT_DENYLIST: Set[str] = {
    "shell-command",
    "start-process",
    "call-process",
    "delete-file",
    "rename-file",
    "write-file",
    "kill-emacs",
}

class ValidationError(Exception):
    """Raised when an RIR tree fails validation."""
    pass

def validate_rir_tree(node: Node, denylist: Set[str] = DEFAULT_DENYLIST):
    """
    Recursively traverses an RIR tree and checks for forbidden symbols.
    Raises ValidationError if a forbidden symbol is found in a call position.
    """
    if isinstance(node, RirList) and node.elements:
        first = node.elements[0]
        if isinstance(first, Symbol) and first.name in denylist:
            raise ValidationError(f"Forbidden symbol found in call position: {first.name}")

        for child in node.elements:
            validate_rir_tree(child, denylist)

# TODO: Add functions for arity checks and other sanity checks as per plan.
