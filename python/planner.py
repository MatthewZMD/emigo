import inspect
import json
import sys
from typing import List, Dict, Any, Optional, Callable

from .llm import llm_client
from .history import _HistoryItem, summarize_history_if_needed, get_total_tokens
from . import tools
from .tools import LLM_TOOLS, TOOLS_REQUIRING_APPROVAL

try:
    import litellm
except ImportError:
    litellm = None


# --- Tool and Method Registries ---

# The registry of all functions callable via RPC from Emacs.
# This will be populated after all functions are defined.
RPC_METHODS: Dict[str, Callable[..., Any]] = {**LLM_TOOLS}
RPC_METHODS['select_target'] = tools.select_target


# --- Planner and Session Management ---

SYSTEM_PROMPT_TEMPLATE = """You are Emigo, an expert software developer and Emacs Lisp programmer, acting as an interactive pair programmer integrated into Emacs.
Your primary goal is to help users by investigating their environment and writing Emacs Lisp code to accomplish their goals.

To be effective, you have a few knowhows for exploring the user's Emacs environment:
- You can inspect what the user is looking at with `get_visible_buffers_content`.
- You can find relevant symbols (commands, functions, variables) using `apropos_command`, `apropos_function`, and `apropos_variable`. These tools take Emacs Lisp regular expressions, which have a different syntax than PCRE (e.g., `\\(` and `\\)` for grouping, `\\|` for alternation). Vague queries will be rejected, so be creative and specific.
- Once you find symbols, you can investigate them with `read_doc`, `get_symbol_source`, and `get_symbol_value` to understand their purpose, implementation, and state.

If the user's request requires writing Emacs Lisp code, you should follow this routine:
1.  **Explore & Gather Information**: Use your exploratory knowhows to understand the user's context and find relevant Emacs Lisp symbols.
2.  **Plan**: Based on your investigation, formulate a plan to write a single piece of Emacs Lisp code that solves the user's problem. Before using one or more tools, you MUST first provide a textual explanation of your reasoning and what you plan to do.
3.  **Execute**: Provide the final code to the user. This is a two-step process:
    a. **Propose**: You MUST first call the `propose_elisp` tool with your generated code. Provide a concise summary of the user's goal for the `intent` parameter. This tool will validate and finalize the code.
    b. **Evaluate**: ONLY after `propose_elisp` succeeds, you may use `eval_elisp` with the validated code returned by `propose_elisp`.

**CRITICAL RULE**: When you provide Emacs Lisp code, it MUST be a single, self-contained, and syntactically valid s-expression. Do not wrap it in markdown code blocks unless it is part of a larger explanation. If you are just providing code, provide only the raw s-expression.
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
        self.history: List[_HistoryItem] = [_HistoryItem(message={"role": "system", "content": SYSTEM_PROMPT_TEMPLATE})]
        self._configure_llm()

    def _configure_llm(self):
        """Fetches LLM configuration from Emacs."""
        try:
            config_vars = tools.emacs_client.call_sync(
                'emigo--get-emacs-vars-func',
                ['emigo-model', 'emigo-base-url', 'emigo-api-key']
            )
            llm_client.model_name = config_vars[0][0]
            llm_client.base_url = config_vars[1][0]
            llm_client.api_key = config_vars[2][0]

            if not all([llm_client.model_name, llm_client.base_url, llm_client.api_key]):
                 raise ValueError("LLM config is incomplete. Check emigo-model, emigo-base-url, and emigo-api-key.")
        except Exception as e:
            tools.emacs_client.call_sync('emigo-message', [f"[Emigo] Error getting LLM config: {e}"])
            raise

    def clear_history(self):
        """Resets the conversation history."""
        self.history = [_HistoryItem(message={"role": "system", "content": SYSTEM_PROMPT_TEMPLATE})]
        print(f"History cleared for session: {self.session_path}", file=sys.stderr)

    def get_history(self) -> List[Dict[str, Any]]:
        """Returns the current conversation history as a list of dicts."""
        return [item.message for item in self.history]

    def run(self, prompt: str):
        """Runs the main ReAct loop for one user turn."""
        if not litellm:
            tools.emacs_client.call_sync('emigo-message', ["[Emigo] Error: litellom is not installed."])
            return

        self.history.append(_HistoryItem(message={"role": "user", "content": prompt}))
        tools.emacs_client.call_sync('emigo--flush-buffer', [self.session_path, "\n\nAssistant:\n", "llm"])

        while True:
            try:
                messages_for_llm = [item.message for item in self.history]
                response_stream = llm_client.generate_plan(messages_for_llm, get_available_tools_schema())
            except litellm.exceptions.RateLimitError as e:
                error_msg = f"LLM Rate Limit Error: {e}"
                tools.emacs_client.call_sync('emigo-message', [f"[Emigo] Error: {error_msg}"])
                break
            except Exception as e:
                error_msg = f"LLM connection error: {e}"
                tools.emacs_client.call_sync('emigo-message', [f"[Emigo] Error: {error_msg}"])
                break

            llm_content = ""
            tool_call_chunks = {}
            stop_reason = "unknown"

            try:
                for chunk in response_stream:
                    choice = chunk.choices[0]
                    delta = choice.delta
                    stop_reason = choice.finish_reason or stop_reason

                    if delta.content:
                        llm_content += delta.content
                        tools.emacs_client.call_sync('emigo--flush-buffer', [self.session_path, delta.content, "llm"])

                    if delta.tool_calls:
                        for tool_call_chunk in delta.tool_calls:
                            index = tool_call_chunk.index
                            if index not in tool_call_chunks:
                                tool_call_chunks[index] = {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}

                            chunk_dict = tool_call_chunks[index]
                            if tool_call_chunk.id: chunk_dict["id"] = tool_call_chunk.id
                            if tool_call_chunk.function.name: chunk_dict["function"]["name"] = tool_call_chunk.function.name
                            if tool_call_chunk.function.arguments: chunk_dict["function"]["arguments"] += tool_call_chunk.function.arguments
            except litellm.exceptions.APIConnectionError as e:
                # Workaround for a litellm/provider bug where a tool call chunk is malformed.
                if "missing 1 required positional argument: 'arguments'" in str(e):
                    print(f"WORKAROUND: Ignoring litellm stream parsing error: {e}", file=sys.stderr)
                    # If we were receiving tool calls, assume that was the intended action.
                    if tool_call_chunks:
                        stop_reason = "tool_calls"
                else:
                    raise

            # Get usage after stream is consumed
            llm_usage = getattr(response_stream, 'usage', None)
            previous_tokens = get_total_tokens(self.history)
            current_total_tokens = previous_tokens + llm_usage.total_tokens if llm_usage else previous_tokens
            usage_dict = {
                "completion_tokens": llm_usage.completion_tokens if llm_usage else 0,
                "prompt_tokens": llm_usage.prompt_tokens if llm_usage else 0,
                "total_tokens": current_total_tokens
            }

            assistant_message = {"role": "assistant", "content": llm_content}
            if tool_call_chunks:
                assistant_message["tool_calls"] = list(tool_call_chunks.values())
                tools.emacs_client.call_sync('emigo--flush-buffer', [self.session_path, "\n", "llm"])

            self.history.append(_HistoryItem(message=assistant_message, usage=usage_dict))

            if stop_reason == "tool_calls":
                proposed_tool_calls = assistant_message['tool_calls']

                # Check if any proposed tool call requires approval
                needs_approval = any(
                    tool_call["function"]["name"] in TOOLS_REQUIRING_APPROVAL
                    for tool_call in proposed_tool_calls
                )

                approval_response = "approve"  # Default to approve
                if needs_approval:
                    approval_response = tools.emacs_client.call_sync('emigo--approve-tool-calls', [json.dumps(proposed_tool_calls)])

                if approval_response == "approve":
                    tool_results = []
                    for tool_call in proposed_tool_calls:
                        tool_name = tool_call["function"]["name"]
                        tool_id = tool_call["id"]
                        try:
                            tool_params = json.loads(tool_call["function"]["arguments"])
                            tool_func = RPC_METHODS.get(tool_name)
                            if not tool_func:
                                raise NameError(f"Tool '{tool_name}' not found.")

                            # Guard against LLM returning a raw value for a single-arg function
                            if not isinstance(tool_params, dict):
                                sig = inspect.signature(tool_func)
                                if len(sig.parameters) == 1:
                                    param_name = next(iter(sig.parameters))
                                    tool_params = {param_name: tool_params}

                            # Guard against LLM nesting JSON in string arguments
                            if isinstance(tool_params, dict):
                                for param_name, param_value in tool_params.items():
                                    if isinstance(param_value, str):
                                        try:
                                            nested_data = json.loads(param_value)
                                            if isinstance(nested_data, dict) and param_name in nested_data:
                                                tool_params[param_name] = nested_data[param_name]
                                        except json.JSONDecodeError:
                                            pass  # Not a JSON string, leave as is.

                            # Validate that all required arguments are present
                            sig = inspect.signature(tool_func)
                            required_params = [p.name for p in sig.parameters.values() if p.default is inspect.Parameter.empty]
                            missing_params = [p for p in required_params if p not in tool_params]
                            if missing_params:
                                raise TypeError(f"missing required arguments: {', '.join(missing_params)}")

                            print(f"Executing tool: {tool_name}({tool_params})", file=sys.stderr)
                            result = tool_func(**tool_params)
                            result_content = json.dumps(result)
                            print(f"--- TOOL RESULT (ID: {tool_id}) ---\n{result_content}", file=sys.stderr)
                            tool_results.append({"tool_call_id": tool_id, "role": "tool", "name": tool_name, "content": result_content})

                        except (Exception, TypeError) as e:
                            print(f"Error executing tool: {tool_name} with args ({tool_call['function']['arguments']}): {e}", file=sys.stderr)
                            error_content = json.dumps({"error": str(e)})
                            print(f"--- TOOL ERROR (ID: {tool_id}) ---\n{error_content}", file=sys.stderr)
                            tool_results.append({"tool_call_id": tool_id, "role": "tool", "name": tool_name, "content": error_content})

                    # Append results as new history items, without usage
                    self.history.extend([_HistoryItem(message=res) for res in tool_results])
                    continue # Continue loop to send tool results back to LLM
                else:
                    # User rejected or provided a new prompt
                    tools.emacs_client.call_sync('emigo--flush-buffer', [self.session_path, f"\n\nUser: {approval_response}", "user"])
                    self.history.append(_HistoryItem(message={"role": "user", "content": approval_response}))
                    break # Exit tool loop and wait for next user turn
            else: # end_turn, stop, etc.
                break # Exit loop and wait for next user prompt

        # After a turn, check if history needs summarization
        self.history = summarize_history_if_needed(self.history, llm_client)
        tools.emacs_client.call_sync('emigo--reinsert-prompt', [self.session_path])


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
        tools.emacs_client.call_sync('emigo--reinsert-prompt', [session_path])
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
