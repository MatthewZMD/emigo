from typing import Optional
from ..rir import codec
from ..rir.nodes import Node
from ..intent.schema import TargetSelector
from ..bridge import client as bridge_client

def read_sexps(target: dict) -> Optional[Node]:
    """
    Read S-expressions from a target in Emacs and return as an RIR tree.
    `target` is a dict representation of a TargetSelector.
    """
    sexp_string = bridge_client.emacs_client.call('emigo-read-sexps', [target])
    if sexp_string:
        return codec.parse_elisp(sexp_string)
    return None

def select_target(file_path: Optional[str] = None, buffer_name: Optional[str] = None, defun_name: Optional[str] = None) -> TargetSelector:
    """Construct a TargetSelector object."""
    return TargetSelector(file_path=file_path, buffer_name=buffer_name, defun_name=defun_name)
