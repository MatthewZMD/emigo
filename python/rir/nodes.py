from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Union

@dataclass
class Node:
    """Base class for all RIR (Runtime Intermediate Representation) nodes."""
    pass

# --- Atomic Types ---

@dataclass
class Symbol(Node):
    """Represents an Elisp symbol."""
    name: str

@dataclass
class String(Node):
    """Represents an Elisp string."""
    value: str

@dataclass
class Number(Node):
    """Represents an Elisp number (integer or float)."""
    value: Union[int, float]

# --- Composite Type ---

@dataclass
class List(Node):
    """Represents a generic Elisp list (sexp)."""
    elements: List[Node] = field(default_factory=list)

# --- Structured Forms (subclasses of List) ---

@dataclass
class Call(List):
    """Represents a function call: (function-name ...args)."""
    pass

@dataclass
class Defun(List):
    """Represents a (defun ...)."""
    pass

@dataclass
class Let(List):
    """Represents a (let ...)."""
    pass

@dataclass
class Lambda(List):
    """Represents a (lambda ...)."""
    pass

@dataclass
class Defcustom(List):
    """Represents a (defcustom ...)."""
    pass
