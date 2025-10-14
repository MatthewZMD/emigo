import json
import sys
from typing import List, Dict, Any, Optional, Callable, Set
from epc.client import EPCClient
import litellm
from .llm import llm_client

# Global client to communicate back to Emacs.
# Initialized by server.py at startup.
emacs_client: Optional[EPCClient] = None


VALIDATION_PROMPT_TEMPLATE = """You are a meticulous Emacs Lisp code reviewer. Your sole purpose is to validate and, if necessary, correct a given s-expression based on syntax checks, symbol analysis, and the user's original intent.

The user's intent is: {intent}

The proposed code is:
```elisp
{code}
```

Here is the analysis of the code from the Emacs environment:
{analysis}

- Review the analysis. Check for unbound symbols or syntax errors.
- Ensure the code correctly implements the user's intent.
- If the code is valid and correct, respond with a JSON object: {{"status": "approved", "code": "..."}}.
- If the code is invalid or incorrect, you MUST correct it. Respond with a JSON object: {{"status": "rejected", "code": "...", "reason": "..."}}. Provide the corrected code and a brief reason for the change.
- Your output MUST be only the JSON object.
"""


# --- Tool Implementations ---

def apropos_command(query: str) -> List[Dict[str, Any]]:
    """
    Searches for Emacs interactive commands that match an Emacs Lisp-style regular expression.
    This is a good first step for finding functionality related to a topic.
    Be specific with your query. For example, to find commands related to 'file',
    a good query is `\\bfile\\b` to match the whole word.
    NOTE: Emacs regex syntax is different from PCRE. Use `\\(` and `\\)` for grouping, and `\\|` for alternation.
    For example, to find commands related to 'kill' or 'delete' for lines, a good query is `\\(kill\\|delete\\).*line`.
    """
    if not query or not query.strip():
        raise ValueError("The apropos_command query cannot be empty. Please provide a specific regular expression.")
    return emacs_client.call_sync('emigo-apropos-command', [query]) or []

def apropos_function(query: str) -> List[Dict[str, Any]]:
    """
    Searches for Emacs functions (including commands and macros) that match an Emacs Lisp-style regular expression.
    This is broader than `apropos_command` and useful for finding internal functions.
    Be specific with your query. For example, to find functions related to 'window',
    a good query is `\\bwindow\\b` to match the whole word.
    NOTE: Emacs regex syntax is different from PCRE. Use `\\(` and `\\)` for grouping, and `\\|` for alternation.
    For example, to find functions for inserting or deleting from a list, a good query is `list.*\\(insert\\|delete\\)`.
    """
    if not query or not query.strip():
        raise ValueError("The apropos_function query cannot be empty. Please provide a specific regular expression.")
    return emacs_client.call_sync('emigo-apropos-function', [query]) or []

def apropos_variable(query: str) -> List[Dict[str, Any]]:
    """
    Searches for Emacs variables that match an Emacs Lisp-style regular expression.
    Useful for finding configuration options and internal state variables.
    Be specific with your query. For example, to find variables related to 'font',
    a good query is `font-` or `\\bfont\\b`.
    NOTE: Emacs regex syntax is different from PCRE. Use `\\(` and `\\)` for grouping, and `\\|` for alternation.
    For example, to find variables related to 'mode' or 'hook', a good query is `\\(mode\\|hook\\)$`.
    """
    if not query or not query.strip():
        raise ValueError("The apropos_variable query cannot be empty. Please provide a specific regular expression.")
    return emacs_client.call_sync('emigo-apropos-variable', [query]) or []

def read_doc(symbol_name: str) -> Optional[str]:
    """
    Retrieves the full, detailed documentation string for a given symbol name.
    Use this after `apropos` to get a deeper understanding of a specific symbol (e.g., function, variable, or face).
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
    Retrieves the full Emacs Lisp source code for a given function, macro, or variable.
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


def propose_elisp(code: str, intent: str) -> str:
    """
    Validates a string of Emacs Lisp code for syntactic correctness and symbol existence.
    This tool MUST be used to validate any code before it is passed to `eval_elisp`.
    It analyzes the code and uses a dedicated agent to review, approve, and correct it.
    Returns the validated and potentially corrected code.
    """
    max_retries = 3
    current_code = code
    for i in range(max_retries):
        analysis_result = emacs_client.call_sync('emigo-validate-elisp', [current_code])
        analysis_str = json.dumps(analysis_result, indent=2)

        validation_prompt = VALIDATION_PROMPT_TEMPLATE.format(
            intent=intent,
            code=current_code,
            analysis=analysis_str
        )
        messages = [{"role": "system", "content": validation_prompt}]

        try:
            response = litellm.completion(
                model=llm_client.model_name,
                api_key=llm_client.api_key,
                base_url=llm_client.base_url,
                messages=messages,
                response_format={"type": "json_object"}
            )
            response_content = response.choices[0].message.content
            review = json.loads(response_content)

            if review.get('status') == 'approved':
                return review.get('code', current_code)
            elif review.get('status') == 'rejected':
                print(f"Code rejected by validator. Reason: {review.get('reason')}. Retrying ({i+1}/{max_retries})...", file=sys.stderr)
                current_code = review.get('code')
                if not current_code:
                    raise ValueError("Validator rejected code but provided no correction.")
            else:
                raise ValueError(f"Invalid response from validation agent: {response_content}")
        except Exception as e:
            raise ValueError(f"Validation agent failed: {e}")

    raise ValueError(f"Code validation failed after {max_retries} retries. Last code:\n{current_code}")


def eval_elisp(code: str) -> str:
    """
    A powerful and dangerous tool that evaluates a string of Emacs Lisp code and returns the result as a string.
    This can be used for complex introspection or to perform actions.
    **This tool requires user approval before execution.** Use it as a last resort when no other tool
    can achieve your goal. The code must be a single, valid s-expression.
    Before using, you MUST validate the code with `propose_elisp`.
    """
    return emacs_client.call_sync('emigo-eval-elisp', [code])


# Tools available to the LLM planner.
LLM_TOOLS: Dict[str, Callable[..., Any]] = {
    "propose_elisp": propose_elisp,
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
