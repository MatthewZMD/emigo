from typing import List, Dict, Any, Optional, Callable, Set
from epc.client import EPCClient

# Global client to communicate back to Emacs.
# Initialized by server.py at startup.
emacs_client: Optional[EPCClient] = None


# --- Tool Implementations ---

def apropos_command(query: str) -> List[Dict[str, Any]]:
    """
    Searches for Emacs interactive commands that match a regular expression.
    This is a good first step for finding functionality related to a topic.
    """
    return emacs_client.call_sync('emigo-apropos-command', [query]) or []

def apropos_function(query: str) -> List[Dict[str, Any]]:
    """
    Searches for Emacs functions (including commands and macros) that match a regular expression.
    This is broader than `apropos_command` and useful for finding internal functions.
    """
    return emacs_client.call_sync('emigo-apropos-function', [query]) or []

def apropos_variable(query: str) -> List[Dict[str, Any]]:
    """
    Searches for Emacs variables that match a regular expression.
    Useful for finding configuration options and internal state variables.
    """
    return emacs_client.call_sync('emigo-apropos-variable', [query]) or []

def read_doc(symbol_name: str) -> Optional[str]:
    """
    Retrieves the full, detailed documentation string for a given symbol name.
    Use this after `apropos` to get a deeper understanding of a specific function or variable before using it.
    The `symbol_name` must be an exact match.
    """
    return emacs_client.call_sync('emigo-symbol-doc', [symbol_name])

def find_keybindings(symbol_name: str) -> List[str]:
    """
    Finds all keybindings that are bound to a specific command symbol.
    This is useful for discovering how to invoke a command interactively. The `symbol_name` must be a
    valid, interactive command (a function with an `(interactive)` form).
    """
    return emacs_client.call_sync('emigo-keybindings', [symbol_name]) or []

def list_buffers() -> List[str]:
    """
    Returns a list of the names of all currently open buffers in Emacs.
    This is useful for getting an overview of the user's current session.
    """
    return emacs_client.call_sync('emigo-list-buffers', []) or []

def buffer_metadata(buffer_name: str) -> Optional[Dict[str, Any]]:
    """
    Retrieves metadata for a specific buffer by name.
    Returns a dictionary containing the buffer's name, associated file path (if any),
    major mode, size, and whether it has been modified.
    """
    return emacs_client.call_sync('emigo-buffer-metadata', [buffer_name])


def get_symbol_source(symbol_name: str) -> Optional[str]:
    """
    Retrieves the full Emacs Lisp source code for a given function or macro.
    This is an essential tool for understanding how existing functionality is implemented,
    learning idiomatic Elisp, and finding undocumented behavior. Use this after `read_doc`
    for the deepest level of understanding.
    """
    return emacs_client.call_sync('emigo-symbol-source', [symbol_name])


def get_symbol_value(symbol_name: str) -> str:
    """
    Inspects and returns the current runtime value of a given variable as a string.
    This is useful for debugging and understanding the state of the Emacs environment.
    The value is returned in its string representation as if printed by `prin1`.
    """
    return emacs_client.call_sync('emigo-symbol-value', [symbol_name])


def get_visible_buffers_content() -> List[Dict[str, str]]:
    """
    Captures and returns the visible portion of text from all windows currently displayed on the screen,
    excluding the agent's own buffer and the minibuffer. This is your primary tool for understanding
    the user's context. It returns a list of dictionaries, where each dictionary contains the
    'buffer_name' and the visible 'content' as a string.
    """
    return emacs_client.call_sync('emigo-visible-buffers-content', []) or []


def eval_elisp(code: str) -> str:
    """
    A powerful and dangerous tool that evaluates a string of Emacs Lisp code and returns the result as a string.
    This can be used for complex introspection or to perform actions.
    **This tool requires user approval before execution.** Use it as a last resort when no other tool
    can achieve your goal. The code must be a single, valid s-expression.
    """
    return emacs_client.call_sync('emigo-eval-elisp', [code])


# Tools available to the LLM planner.
LLM_TOOLS: Dict[str, Callable[..., Any]] = {
    "apropos_command": apropos_command,
    "apropos_function": apropos_function,
    "apropos_variable": apropos_variable,
    "read_doc": read_doc,
    "get_symbol_source": get_symbol_source,
    "get_symbol_value": get_symbol_value,
    "get_visible_buffers_content": get_visible_buffers_content,
    "find_keybindings": find_keybindings,
    "list_buffers": list_buffers,
    "buffer_metadata": buffer_metadata,
    "eval_elisp": eval_elisp,
}

# A set of tool names that require user approval before execution.
# By default, most tools are safe. Potentially destructive or sensitive
# tools like `eval_elisp` should be added here.
TOOLS_REQUIRING_APPROVAL: Set[str] = {}


def select_target(file_path: Optional[str] = None, buffer_name: Optional[str] = None, defun_name: Optional[str] = None) -> dict:
    """Construct a target selector dictionary."""
    # Filter out None values
    return {k: v for k, v in {
        "file_path": file_path,
        "buffer_name": buffer_name,
        "defun_name": defun_name
    }.items() if v is not None}
