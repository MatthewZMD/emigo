import inspect
import json
import sys
from typing import List, Dict, Any, Optional, Callable

from epc.client import EPCClient
from .llm import llm_client

try:
    import litellm
except ImportError:
    litellm = None

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
    return emacs_client.call('emacs_symbol_doc', [symbol_name])

def find_keybindings(symbol_name: str) -> List[str]:
    """Find keybindings for a given command symbol in Emacs."""
    return emacs_client.call('emacs_keybindings', [symbol_name])

def list_buffers() -> List[str]:
    """Return a list of all buffer names in Emacs."""
    return emacs_client.call('emigo-list-buffers', [])

def buffer_metadata(buffer_name: str) -> Optional[Dict[str, Any]]:
    """Return metadata for a specific buffer."""
    return emacs_client.call('emigo-buffer-metadata', [buffer_name])

def current_mode() -> Optional[str]:
    """Return the major-mode of the current buffer in Emacs."""
    return emacs_client.call('emigo-current-mode', [])

def select_target(file_path: Optional[str] = None, buffer_name: Optional[str] = None, defun_name: Optional[str] = None) -> dict:
    """Construct a target selector dictionary."""
    # Filter out None values
    return {k: v for k, v in {
        "file_path": file_path,
        "buffer_name": buffer_name,
        "defun_name": defun_name
    }.items() if v is not None}


# --- Tool and Method Registries ---

# Tools available to the LLM planner.
LLM_TOOLS: Dict[str, Callable[..., Any]] = {
    "apropos": apropos,
    "read_doc": read_doc,
    "find_keybindings": find_keybindings,
    "list_buffers": list_buffers,
    "buffer_metadata": buffer_metadata,
    "current_mode": current_mode,
}

# The registry of all functions callable via RPC from Emacs.
# This will be populated after all functions are defined.
RPC_METHODS: Dict[str, Callable[..., Any]] = {**LLM_TOOLS}
RPC_METHODS['select_target'] = select_target


# --- Planner and Session Management ---

SYSTEM_PROMPT = """You are Emigo, an expert software developer integrated into Emacs.
You are an Emacs-native agent. Your primary goal is to help users with software development tasks inside Emacs.
You can interact with the user's Emacs environment by calling provided tools.
You can search for symbols using apropos, read documentation, and understand the user's current context within Emacs.

Your workflow is:
1.  **Perceive**: Understand the user's goal and the current state of their Emacs environment.
2.  **Plan**: Formulate a sequence of tool calls to achieve the goal. You can also ask clarifying questions or provide code snippets for the user to execute.
3.  **Execute**: The host environment will execute your tool calls. You will observe the results and continue planning until the goal is met.

You must only use the tools provided to you. Do not ask for confirmation unless the task is ambiguous.
If you need to write or modify code, provide the complete code snippet in a markdown block for the user to copy and paste.
"""

def get_available_tools_schema() -> List[Dict[str, Any]]:
    """
    Generate an OpenAI-compatible tool schema for all registered tools.
    """
    schemas = []
    for name, func in LLM_TOOLS.items():
        try:
            sig = inspect.signature(func)
            doc = inspect.getdoc(func) or "No description."
            description = doc.strip().split('\n')[0]

            parameters = {
                "type": "object",
                "properties": {},
                "required": [],
            }

            for param_name, param in sig.parameters.items():
                param_type = "string"  # Default
                if param.annotation in [int, float]:
                    param_type = "number"
                elif param.annotation == bool:
                    param_type = "boolean"
                elif param.annotation in [list, List]:
                    param_type = "array"
                elif param.annotation in [dict, Dict, Any]:
                    param_type = "object"

                parameters["properties"][param_name] = {
                    "type": param_type,
                    "description": f"Parameter '{param_name}'",
                }
                if param.default is inspect.Parameter.empty:
                    parameters["required"].append(param_name)

            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                }
            })
        except (ValueError, TypeError) as e:
            print(f"Could not generate schema for tool '{name}': {e}", file=sys.stderr)

    return schemas


class PlannerAgent:
    """Manages the state and execution loop for a single Emigo session."""
    def __init__(self, session_path: str):
        self.session_path = session_path
        self.history: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._configure_llm()

    def _configure_llm(self):
        """Fetches LLM configuration from Emacs."""
        try:
            config_vars = emacs_client.call_sync(
                'emigo--get-emacs-vars-func',
                ['emigo-model', 'emigo-base-url', 'emigo-api-key']
            )
            llm_client.model_name = config_vars[0][0]
            llm_client.base_url = config_vars[1][0]
            llm_client.api_key = config_vars[2][0]

            if not all([llm_client.model_name, llm_client.base_url, llm_client.api_key]):
                 raise ValueError("LLM config is incomplete. Check emigo-model, emigo-base-url, and emigo-api-key.")
        except Exception as e:
            emacs_client.call_sync('emigo-message', [f"[Emigo] Error getting LLM config: {e}"])
            raise

    def clear_history(self):
        """Resets the conversation history."""
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        print(f"History cleared for session: {self.session_path}", file=sys.stderr)

    def get_history(self) -> List[Dict[str, Any]]:
        """Returns the current conversation history."""
        return self.history

    def run(self, prompt: str):
        """Runs the main ReAct loop for one user turn."""
        if not litellm:
            emacs_client.call_sync('emigo-message', ["[Emigo] Error: litellom is not installed."])
            return

        self.history.append({"role": "user", "content": prompt})
        emacs_client.call_sync('emigo--flush-buffer', [self.session_path, "\n\nAssistant:\n", "llm"])

        while True:
            try:
                response_stream = llm_client.generate_plan(self.history, get_available_tools_schema())
            except litellm.exceptions.RateLimitError as e:
                error_msg = f"LLM Rate Limit Error: {e}"
                emacs_client.call_sync('emigo-message', [f"[Emigo] Error: {error_msg}"])
                break
            except Exception as e:
                error_msg = f"LLM connection error: {e}"
                emacs_client.call_sync('emigo-message', [f"[Emigo] Error: {error_msg}"])
                break

            llm_content = ""
            tool_call_chunks = {}
            stop_reason = "unknown"

            for chunk in response_stream:
                choice = chunk.choices[0]
                delta = choice.delta
                stop_reason = choice.finish_reason or stop_reason

                if delta.content:
                    llm_content += delta.content
                    emacs_client.call_sync('emigo--flush-buffer', [self.session_path, delta.content, "llm"])

                if delta.tool_calls:
                    for tool_call_chunk in delta.tool_calls:
                        index = tool_call_chunk.index
                        if index not in tool_call_chunks:
                            tool_call_chunks[index] = {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}

                        chunk_dict = tool_call_chunks[index]
                        if tool_call_chunk.id: chunk_dict["id"] = tool_call_chunk.id
                        if tool_call_chunk.function.name: chunk_dict["function"]["name"] = tool_call_chunk.function.name
                        if tool_call_chunk.function.arguments: chunk_dict["function"]["arguments"] += tool_call_chunk.function.arguments

            assistant_message = {"role": "assistant", "content": llm_content}
            if tool_call_chunks:
                assistant_message["tool_calls"] = list(tool_call_chunks.values())
                emacs_client.call_sync('emigo--flush-buffer', [self.session_path, "\n", "llm"])
            self.history.append(assistant_message)

            if stop_reason == "tool_calls":
                tool_results = []
                print(f"\n--- PROPOSED TOOL CALLS ---\n{json.dumps(assistant_message['tool_calls'], indent=2)}", file=sys.stderr)
                for tool_call in assistant_message["tool_calls"]:
                    tool_name = tool_call["function"]["name"]
                    tool_id = tool_call["id"]
                    try:
                        tool_params = json.loads(tool_call["function"]["arguments"])
                        tool_func = RPC_METHODS.get(tool_name)
                        if not tool_func:
                            raise NameError(f"Tool '{tool_name}' not found.")

                        print(f"Executing tool: {tool_name}({tool_params})", file=sys.stderr)
                        result = tool_func(**tool_params)
                        result_content = json.dumps(result)
                        print(f"--- TOOL RESULT (ID: {tool_id}) ---\n{result_content}", file=sys.stderr)
                        tool_results.append({"tool_call_id": tool_id, "role": "tool", "name": tool_name, "content": result_content})

                    except (Exception, TypeError) as e:
                        print(f"Error executing tool '{tool_name}': {e}", file=sys.stderr)
                        error_content = json.dumps({"error": str(e)})
                        print(f"--- TOOL ERROR (ID: {tool_id}) ---\n{error_content}", file=sys.stderr)
                        tool_results.append({"tool_call_id": tool_id, "role": "tool", "name": tool_name, "content": error_content})

                self.history.extend(tool_results)
                continue # Continue loop to send tool results back to LLM
            else: # end_turn, stop, etc.
                break # Exit loop and wait for next user prompt

        emacs_client.call_sync('emigo--reinsert-prompt', [self.session_path])


# --- Session Management ---

PLANNER_SESSIONS: Dict[str, PlannerAgent] = {}

def get_or_create_agent(session_path: str) -> PlannerAgent:
    """Gets the existing agent for a session or creates a new one."""
    if session_path not in PLANNER_SESSIONS:
        print(f"Creating new planner agent for session: {session_path}", file=sys.stderr)
        PLANNER_SESSIONS[session_path] = PlannerAgent(session_path)
    return PLANNER_SESSIONS[session_path]

def start_planner(session_path: str, prompt: str):
    """Entry point to start or continue a planning session."""
    try:
        agent = get_or_create_agent(session_path)
        agent.run(prompt)
        return {"status": "success"}
    except Exception as e:
        print(f"Error in planner for session {session_path}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        emacs_client.call_sync('emigo--reinsert-prompt', [session_path])
        return {"status": "failure", "reason": str(e)}

def clear_history(session_path: str):
    """Clears the history for a given session."""
    if session_path in PLANNER_SESSIONS:
        agent = PLANNER_SESSIONS[session_path]
        agent.clear_history()
        return {"status": "success"}
    return {"status": "not_found", "reason": "Session not found."}

def get_history(session_path: str) -> List[Dict[str, Any]]:
    """Retrieves the history for a given session."""
    if session_path in PLANNER_SESSIONS:
        return PLANNER_SESSIONS[session_path].get_history()
    return []


# Add session management functions to the RPC method registry
RPC_METHODS["start_planner"] = start_planner
RPC_METHODS["clear_history"] = clear_history
RPC_METHODS["get_history"] = get_history
