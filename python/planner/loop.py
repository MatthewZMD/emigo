import inspect
import json
import sys
from typing import List, Dict, Any, Optional

from ..bridge import client as bridge_client
from ..bridge.registry import TOOL_REGISTRY
from ..llm import llm_client
from ..intent.validate import IntentValidationError

SYSTEM_PROMPT = """You are Emigo, an expert software developer integrated into Emacs.
You are an Emacs-native agent. Your primary goal is to help users with software development tasks inside Emacs.
You can understand and manipulate Emacs Lisp code through structured "Intents".
You can interact with the user's Emacs environment by calling provided tools.
You can read files, search for symbols, and understand the user's current context within Emacs.

Your workflow is:
1.  **Perceive**: Understand the user's goal and the current state of their Emacs environment.
2.  **Plan**: Formulate a sequence of tool calls to achieve the goal.
3.  **Execute**: The host environment will execute your plan.

You must only use the tools provided to you. Do not ask for confirmation unless the task is ambiguous.
When asked to write or modify code, you should use the `execute_intent` tool with `ReplaceSexp` or other appropriate intents. You do not output code blocks directly.
"""

def get_available_tools_schema() -> List[Dict[str, Any]]:
    """
    Generate an OpenAI-compatible tool schema for all registered tools.
    """
    schemas = []
    for name, func in TOOL_REGISTRY.items():
        if name == 'start_planner':
            continue

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

def run_planner_loop(session_path: str, prompt: str):
    """
    Main entry point for the planner loop.
    Orchestrates Perceive -> Plan -> Validate -> Execute -> Observe.
    
    Args:
        session_path: The path identifying the current session/project
        prompt: The user's request/goal
    """
    if bridge_client.emacs_client is None:
        print(f"ERROR: emacs_client is None in run_planner_loop", file=sys.stderr, flush=True)
        return {"status": "failure", "reason": "emacs_client not initialized"}
    
    bridge_client.emacs_client.call_sync('emigo-message', [f"[Emigo Planner] Received prompt: {prompt}"])

    # 1. Perceive (simplified)
    try:
        context_str = f"Current Emacs context: mode is {bridge_client.emacs_client.call('emigo-current-mode')}, open buffers are {bridge_client.emacs_client.call('emigo-list-buffers')[:5]}."
        bridge_client.emacs_client.call_sync('emigo-message', [f"[Emigo Planner] Context: {context_str}"])
    except Exception as e:
        bridge_client.emacs_client.call_sync('emigo-message', [f"[Emigo Planner] Failed to perceive context: {e}"])
        context_str = "Could not perceive Emacs context."

    # Include session path in context
    context_str = f"Session path: {session_path}\n{context_str}"

    # 2. Plan
    available_tools = get_available_tools_schema()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{prompt}\n\n{context_str}"}
    ]

    plan = llm_client.generate_plan(messages, available_tools)

    if not plan:
        bridge_client.emacs_client.call_sync('emigo-message', ["[Emigo Planner] LLM could not generate a plan."])
        return {"status": "failure", "reason": "No plan generated."}

    # 3. Validate, Execute, Observe (in a loop)
    for i, step in enumerate(plan):
        tool_name = step.get("tool_name")
        tool_params = step.get("tool_params", {})

        bridge_client.emacs_client.call_sync('emigo-message', [f"[Emigo Planner] Step {i+1}: Calling tool '{tool_name}' with params {tool_params}"])

        tool_func = TOOL_REGISTRY.get(tool_name)
        if not tool_func:
            error_msg = f"LLM hallucinated a tool: '{tool_name}'"
            bridge_client.emacs_client.call_sync('emigo-message', [f"[Emigo Planner] Error: {error_msg}"])
            return {"status": "failure", "reason": error_msg}

        try:
            # Execute the tool call
            result = tool_func(**tool_params)

            # Observe the result and log it.
            bridge_client.emacs_client.call_sync('emigo-message', [f"[Emigo Planner] Step {i+1} result: {result}"])

            # Check for failure from Emacs actuator
            if isinstance(result, dict) and result.get('status') == 'error':
                 error_msg = f"Execution failed in Emacs: {result.get('message')}"
                 bridge_client.emacs_client.call_sync('emigo-message', [f"[Emigo Planner] Error: {error_msg}"])
                 return {"status": "failure", "reason": error_msg}

        except (IntentValidationError, TypeError, Exception) as e:
            error_msg = f"Error executing tool '{tool_name}': {e}"
            bridge_client.emacs_client.call_sync('emigo-message', [f"[Emigo Planner] Error: {error_msg}"])
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {"status": "failure", "reason": str(e)}

    bridge_client.emacs_client.call_sync('emigo-message', ["[Emigo Planner] Plan executed successfully."])
    return {"status": "success"}
