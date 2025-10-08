import os
import sys
import json
from typing import List, Dict, Any, Optional, Tuple, Iterator

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
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url

    def generate_plan(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Iterator[Any]:
        """
        Given a series of messages and available tools, returns a stream of response chunks.
        """
        try:
            return litellm.completion(
                model=self.model_name,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                api_key=self.api_key,
                base_url=self.base_url,
                stream=True,
            )
        except litellm.exceptions.RateLimitError as e:
            # Re-raise to be handled by the planner loop
            raise e
        except Exception as e:
            print(f"Error calling LLM for plan generation: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            # Return an empty iterator on error
            return iter([])

# Singleton instance
llm_client = LLMClient()
