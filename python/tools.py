from typing import List, Dict, Any, Optional
from epc.client import EPCClient

# Global client to communicate back to Emacs.
# Initialized by server.py at startup.
emacs_client: Optional[EPCClient] = None


# --- Tool Implementations ---

def apropos(query: str) -> List[Dict[str, Any]]:
    """
    Search for symbols in Emacs matching a query (apropos).
    """
    return emacs_client.call('emigo-apropos', [query])

def read_doc(symbol_name: str) -> Optional[str]:
    """Read the full docstring for a symbol from Emacs."""
    return emacs_client.call('emigo-symbol-doc', [symbol_name])

def find_keybindings(symbol_name: str) -> List[str]:
    """Find keybindings for a given command symbol in Emacs."""
    return emacs_client.call('emigo-keybindings', [symbol_name])

def list_buffers() -> List[str]:
    """Return a list of all buffer names in Emacs."""
    return emacs_client.call('emigo-list-buffers', [])

def buffer_metadata(buffer_name: str) -> Optional[Dict[str, Any]]:
    """Return metadata for a specific buffer."""
    return emacs_client.call('emigo-buffer-metadata', [buffer_name])

def current_mode() -> Optional[str]:
    """Return the major-mode of the current buffer in Emacs."""
    return emacs_client.call('emigo-current-mode', [])

def eval_elisp(code: str) -> str:
    """
    Evaluate an Emacs Lisp expression and return the result as a string.
    This is a powerful tool for introspection and dynamic modification of the Emacs environment.
    The code is evaluated in the context of the top-level.
    """
    return emacs_client.call_sync('emigo-eval-elisp', [code])


def select_target(file_path: Optional[str] = None, buffer_name: Optional[str] = None, defun_name: Optional[str] = None) -> dict:
    """Construct a target selector dictionary."""
    # Filter out None values
    return {k: v for k, v in {
        "file_path": file_path,
        "buffer_name": buffer_name,
        "defun_name": defun_name
    }.items() if v is not None}
