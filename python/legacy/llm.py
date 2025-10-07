#!/usr/bin/env python

"""
LLM Client Wrapper using LiteLLM.

Provides a simplified interface (`LLMClient`) for interacting with various
Large Language Models (LLMs) supported by the `litellm` library. It handles
API calls, streaming responses, and basic configuration (model name, API keys,
base URLs).

Note: This client is designed to be stateless regarding chat history. The
calling process (e.g., `worker.py`) is responsible for managing and
passing the complete message history for each API call.
"""

import importlib
import json
import os
import sys
import time
import warnings
from typing import Dict, Iterator, List, Optional, Union

# Filter out UserWarning from pydantic used by litellm
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# --- Lazy Loading for litellm ---

# Configure basic litellm settings globally
EMIGO_SITE_URL = "https://github.com/MatthewZMD/emigo" # Example URL, adjust if needed
EMIGO_APP_NAME = "Emigo" # Example App Name
os.environ["OR_SITE_URL"] = os.environ.get("OR_SITE_URL", EMIGO_SITE_URL)
os.environ["OR_APP_NAME"] = os.environ.get("OR_APP_NAME", EMIGO_APP_NAME)
os.environ["LITELLM_MODE"] = os.environ.get("LITELLM_MODE", "PRODUCTION")

VERBOSE_LLM_LOADING = False # Set to True for debugging litellm loading

class LazyLiteLLM:
    """Lazily loads the litellm library upon first access."""
    _lazy_module = None

    def __getattr__(self, name):
        # Avoid infinite recursion during initialization
        if name == "_lazy_module":
            return super().__getattribute__(name)

        self._load_litellm()
        return getattr(self._lazy_module, name)

    def _load_litellm(self):
        """Loads and configures the litellm module."""
        if self._lazy_module is not None:
            return

        if VERBOSE_LLM_LOADING:
            print("Loading litellm...", file=sys.stderr)
        start_time = time.time()

        try:
            self._lazy_module = importlib.import_module("litellm")

            # Basic configuration similar to Aider
            self._lazy_module.suppress_debug_info = True
            self._lazy_module.set_verbose = False
            self._lazy_module.drop_params = True # Drop unsupported params silently
            # Attempt to disable internal debugging/logging if method exists
            if hasattr(self._lazy_module, "_logging") and hasattr(
                self._lazy_module._logging, "_disable_debugging"
            ):
                self._lazy_module._logging._disable_debugging()

        except ImportError as e:
            print(
                f"Error: {e} litellm not found. Please install it: pip install litellm",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as e:
            print(f"Error loading litellm: {e}", file=sys.stderr)
            sys.exit(1)

        if VERBOSE_LLM_LOADING:
            load_time = time.time() - start_time
            print(f"Litellm loaded in {load_time:.2f} seconds.", file=sys.stderr)

# Global instance of the lazy loader
litellm = LazyLiteLLM()

# --- LLM Client Class ---

class LLMClient:
    """Handles interaction with the LLM and manages chat history."""

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        verbose: bool = False,
    ):
        """
        Initializes the LLM client.

        Args:
            model_name: The name of the language model to use (e.g., "gpt-4o").
            api_key: Optional API key for the LLM service.
            base_url: Optional base URL for custom LLM endpoints (like Ollama).
            verbose: If True, enables verbose output.
        """
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.verbose = verbose

    def send(
        self,
        messages: List[Dict],
        stream: bool = True,
        temperature: float = 0.7,
    ) -> Union[Iterator[object], object]: # Return type is iterator of chunks or full response object
        """
        Sends the provided messages list to the LLM and returns the response.

        Args:
            messages: The list of message dictionaries to send.
            stream: Whether to stream the response or wait for the full completion.
            temperature: The sampling temperature for the LLM.

        Returns:
            An iterator yielding response chunks if stream=True, otherwise the
            full response content string.
        """
        # Ensure litellm is loaded before making the call
        litellm._load_litellm()

        completion_kwargs = {
            "model": self.model_name,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
        }

        # Add API key and base URL if they were provided
        if self.api_key:
            completion_kwargs["api_key"] = self.api_key
        if self.base_url:
            completion_kwargs["base_url"] = self.base_url
            # OLLAMA specific adjustment if needed (example)
            if "ollama" in self.model_name or (self.base_url and "ollama" in self.base_url):
                 # LiteLLM might handle this automatically, but explicitly setting can help
                 completion_kwargs["model"] = self.model_name.replace("ollama/", "")

        try:
            # Store the raw response object for potential parsing later (e.g., tool calls)
            self.last_response_object = None # Initialize

            # Initiate the LLM call
            response = litellm.completion(**completion_kwargs)
            self.last_response_object = response # Store the raw response

            # --- Verbose Logging ---
            if self.verbose:
                # Calculate approximate token count using litellm's utility
                token_count_str = ""
                try:
                    # Ensure litellm is loaded before using its utilities
                    litellm._load_litellm()
                    # Use litellm's token counter if available
                    count = litellm.token_counter(model=self.model_name, messages=messages)
                    token_count_str = f" (estimated {count} tokens)"
                except Exception as e:
                     # Fallback or simple message if token counting fails
                     # We can't easily use the agent's tokenizer here, so rely on litellm or skip detailed count
                     token_count_str = f" (token count unavailable: {e})"

                print(f"--- LLM Request Tokens: {token_count_str} ---", file=sys.stderr)
            # --- End Verbose Logging ---

            if stream:
                # Generator to yield the raw litellm chunk objects
                def raw_chunk_stream():
                    # Move the try/except block inside the generator
                    try:
                        # The 'response' variable is accessible due to closure
                        for chunk in response:
                            # print(f"Raw chunk: {chunk}") # DEBUG: Ensure this is commented out
                            yield chunk # Yield the original chunk object
                    except litellm.exceptions.APIConnectionError as e: # Catch specific error
                        # Log the specific error clearly
                        # Add more detail from the exception object if possible
                        error_details = f"Caught APIConnectionError: {e}\n"
                        # Check for attributes that might hold response data (common in httpx/openai errors)
                        if hasattr(e, 'response') and e.response:
                            try:
                                error_details += f"  Response Status: {getattr(e.response, 'status_code', 'N/A')}\n"
                                # Limit printing potentially large response content
                                response_text = getattr(e.response, 'text', '')
                                error_details += f"  Response Content (first 500 chars): {response_text[:500]}{'...' if len(response_text) > 500 else ''}\n"
                            except Exception as detail_err: error_details += f"  (Error getting response details: {detail_err})\n"
                        if hasattr(e, 'request') and e.request:
                             try:
                                error_details += f"  Request URL: {getattr(e.request, 'url', 'N/A')}\n"
                             except Exception as detail_err: error_details += f"  (Error getting request details: {detail_err})\n"
                        print(f"\n[LLMClient Stream Error] {error_details}", file=sys.stderr)
                        print("[LLMClient Stream Error] Stream may be incomplete.", file=sys.stderr)
                        # Yield an error marker instead of just passing
                        yield {"_stream_error": True, "error_message": str(e)}
                    except Exception as e:
                        # Catch other potential errors during streaming
                        # Add similar detailed logging
                        error_details = f"Caught unexpected error: {type(e).__name__} - {e}\n"
                        if hasattr(e, 'response') and e.response:
                            try:
                                error_details += f"  Response Status: {getattr(e.response, 'status_code', 'N/A')}\n"
                                response_text = getattr(e.response, 'text', '')
                                error_details += f"  Response Content (first 500 chars): {response_text[:500]}{'...' if len(response_text) > 500 else ''}\n"
                            except Exception as detail_err: error_details += f"  (Error getting response details: {detail_err})\n"
                        if hasattr(e, 'request') and e.request:
                             try:
                                error_details += f"  Request URL: {getattr(e.request, 'url', 'N/A')}\n"
                             except Exception as detail_err: error_details += f"  (Error getting request details: {detail_err})\n"
                        # Include traceback for unexpected errors
                        import traceback
                        error_details += f"  Traceback:\n{traceback.format_exc()}\n"
                        print(f"\n[LLMClient Stream Error] {error_details}", file=sys.stderr)
                        # Yield an error marker
                        yield {"_stream_error": True, "error_message": str(e)}

                return raw_chunk_stream() # Return the generator yielding full chunks
            else:
                # For non-streaming, return the raw response object
                # The caller (worker) will parse content or tool calls
                return response # Return the whole LiteLLM response object

        # Keep exception handling for non-streaming calls or errors *before* streaming starts
        except litellm.APIConnectionError as e:
             error_message = f"API Connection Error (pre-stream or non-stream): {e}"
             print(f"\n{error_message}", file=sys.stderr)
             # For non-streaming, return the error string
             return f"[LLM Error: {error_message}]"
        except Exception as e:
             error_message = f"General Error (pre-stream or non-stream): {e}"
             print(f"\n{error_message}", file=sys.stderr)
             # For non-streaming, return the error string
             return f"[LLM Error: {error_message}]"
