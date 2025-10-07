import os
import sys
import json
from typing import List, Dict, Any, Optional

try:
    import litellm
except ImportError:
    print("Error: litellm not found. Please install it: pip install litellm", file=sys.stderr)
    sys.exit(1)


# Configure litellm
litellm.suppress_debug_info = True
litellm.set_verbose = False
litellm.drop_params = True

class LLMClient:
    def __init__(self,
                 model_name: Optional[str] = None,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None):
        self.model_name = model_name or os.environ.get("EMIGO_MODEL") or "gpt-5"
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_API_BASE")

    def generate_plan(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """
        Given a series of messages and available tools, generate a sequence of tool calls.
        """
        try:
            response = litellm.completion(
                model=self.model_name,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                api_key=self.api_key,
                base_url=self.base_url,
            )

            # Check for tool calls in the response
            choice = response.choices[0]
            if choice.finish_reason != 'tool_calls' or not choice.message.tool_calls:
                print(f"LLM did not return tool calls. Response: {choice.message.content}", file=sys.stderr)
                return None

            plan = []
            for tool_call in choice.message.tool_calls:
                plan.append({
                    "tool_name": tool_call.function.name,
                    "tool_params": json.loads(tool_call.function.arguments),
                })
            return plan

        except Exception as e:
            print(f"Error calling LLM for plan generation: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None

# Singleton instance
llm_client = LLMClient()
