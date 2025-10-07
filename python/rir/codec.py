from __future__ import annotations
from typing import Union
from .nodes import Node, Symbol, String, Number, List, Call, Defun, Let, Lambda, Defcustom
from sexpdata import loads, dumps, Symbol as SexpSymbol, Quoted

# Mapping from symbol name to structured RIR node class
STRUCTURED_FORMS = {
    "defun": Defun,
    "defcustom": Defcustom,
    "let": Let,
    "let*": Let,
    "lambda": Lambda,
}

def _from_sexp(sexp) -> Node:
    """Recursively convert data from `sexpdata` to RIR nodes."""
    if isinstance(sexp, SexpSymbol):
        return Symbol(sexp.value())
    if isinstance(sexp, str):
        return String(sexp)
    if isinstance(sexp, int) or isinstance(sexp, float):
        return Number(sexp)
    if isinstance(sexp, list):
        elements = [_from_sexp(e) for e in sexp]
        if elements and isinstance(elements[0], Symbol):
            head_name = elements[0].name
            # Check for special structured forms
            if head_name in STRUCTURED_FORMS:
                return STRUCTURED_FORMS[head_name](elements)
            # Default to Call for other function-like lists
            return Call(elements)
        return List(elements)
    if isinstance(sexp, Quoted):
        return List([Symbol("quote"), _from_sexp(sexp.value())])
    if sexp is None: # Should be handled by nil=... in loads
        return Symbol("nil")
    raise TypeError(f"Unsupported S-expression type: {type(sexp)}")

def parse_elisp(s: str) -> Node:
    """Parse a single Emacs Lisp S-expression string into an RIR node tree."""
    sexp = loads(s, nil=SexpSymbol('nil'), true=SexpSymbol('t'), false=SexpSymbol('nil'))
    return _from_sexp(sexp)

def _to_sexp(node: Node) -> Union[SexpSymbol, str, int, float, list]:
    """Recursively convert an RIR node to `sexpdata` types."""
    if isinstance(node, Symbol):
        return SexpSymbol(node.name)
    if isinstance(node, String):
        return node.value
    if isinstance(node, Number):
        return node.value
    if isinstance(node, List):
        # Handle (quote ...) forms for serialization
        if len(node.elements) == 2 and isinstance(node.elements[0], Symbol) and node.elements[0].name == 'quote':
            val_to_quote = _to_sexp(node.elements[1])
            return Quoted(val_to_quote)
        return [_to_sexp(e) for e in node.elements]
    raise TypeError(f"Unsupported RIR node type: {type(node)}")

def to_elisp(node: Node) -> str:
    """Serialize an RIR node tree into a pretty-printed Emacs Lisp string."""
    sexp = _to_sexp(node)
    return dumps(sexp, pretty_print=True)
