#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""LLM Interaction Worker Process."""

import sys
import json
import time
import traceback
import os

# Import necessary components
from utils import _filter_context, get_os_name
from llm import LLMClient # Import the LLMClient

# Add project root to sys.path if not already present
project_root = os.path.dirname(os.path.abspath(__file__))

# --- System Prompt Template ---
# Load from system_prompt.py or define here if simpler
# Assuming it's defined here for simplicity based on previous context
MAIN_SYSTEM_PROMPT = """You are Emigo, an expert software developer integrated into Emacs.
You have extensive knowledge in many programming languages, frameworks, design patterns, and everything in between.
Always use best practices when coding. Respect and use existing conventions, libraries, etc that are already present in the code base.

**Language Instruction**: You MUST detect the language of my question and respond in the same language. For example, if I ask a question in Chinese, you MUST reply in Chinese; if I ask in English, you MUST reply in English. This rule takes precedence over any other instructions. If you are unsure of the language, default to the language of the user's input.

====

CAPABILITIES

- You can write code, make edits or improvements to existing files, and understand the current state of a project.
- When the user initially gives you a task, information about the project structure and files currently in the chat context will be included in `<context>`. Use this context to inform your actions.
- You can use tools to interact with the system, read/write files, search, etc.

====

RULES

- Your session directory is: {session_dir}
- You cannot `cd` into a different directory. You operate from '{session_dir}'.
- Do not use the ~ character or $HOME to refer to the home directory.
- When making changes to code, always consider the context in which the code is being used. Ensure that your changes are compatible with the existing codebase and that they follow the project's coding standards and best practices.
- The user may provide a file's contents directly in their message.
- Your goal is to try to accomplish the user's task, NOT engage in a back and forth conversation.
- You are STRICTLY FORBIDDEN from starting your messages with "Great", "Certainly", "Okay", "Sure". You should NOT be conversational in your responses, but rather direct and to the point. For example you should NOT say "Great, I've updated the CSS" but instead something like "I've updated the CSS". It is important you be clear and technical in your messages.
- When presented with images, utilize your vision capabilities to thoroughly examine them and extract meaningful information. Incorporate these insights into your thought process as you accomplish the user's task.
- At the end of each user message, you will automatically receive `<context>`. This information is not written by the user themselves, but is auto-generated to provide *passive context* about the project structure and the content of files currently added to the chat. Do not treat it as a direct part of the user's request unless they explicitly refer to it. Use this context to inform your actions. Explain your use of `<context>` clearly.
- **Language Rule**: You MUST respond to my question in the same language I use to ask it. This is a strict requirement. For example, if I ask in Chinese, your response MUST be in Chinese. If you fail to detect the language, match the language of my input as closely as possible. This rule overrides any default language preferences.

====

SYSTEM INFORMATION

Operating System: {os_name}
Default Shell: {shell}
Home Directory: {homedir}
Session Directory: {session_dir}

====

OBJECTIVE

You accomplish a given task by:
1. Understanding the user's request and reviewing the `<context>` for context (file structure, cached file content).
2. Generating code, explanations, or modifications based on the request and context.
3. Providing the complete response directly. Avoid conversational filler.
"""


# --- Build System Prompt Function ---

def _build_system_prompt(session_path: str) -> str:
    """Builds the system prompt, inserting dynamic info."""
    session_dir = session_path
    os_name = get_os_name()
    shell = "/bin/bash" # Default shell - TODO: Get from Emacs?
    homedir = os.path.expanduser("~")

    # Use .format() on the MAIN_SYSTEM_PROMPT template
    prompt = MAIN_SYSTEM_PROMPT.format(
        session_dir=session_dir.replace(os.sep, '/'), # Ensure POSIX paths
        os_name=os_name,
        shell=shell,
        homedir=homedir.replace(os.sep, '/')
    )
    return prompt

def send_message(msg_type, session_path, **kwargs):
    """Sends a JSON message to stdout for the main process."""
    message = {"type": msg_type, "session": session_path, **kwargs}
    try:
        print(json.dumps(message), flush=True)
    except (TypeError, Exception) as e:
        # Handle potential non-serializable data or other errors
        error_data = {"type": "error", "session": session_path}
        if isinstance(e, TypeError):
            error_data["message"] = f"Serialization error: {e}. Data: {repr(kwargs)}"
        else:
            error_data["message"] = f"Error sending message: {e}"
        print(json.dumps(error_data), flush=True)


def handle_interaction_request(request_data):
    """Handles a single interaction request dictionary."""
    session_path = request_data.get("session_path")
    user_prompt_dict = request_data.get("user_prompt") # The original user prompt dict
    history = request_data.get("history", [])
    config = request_data.get("config", {})
    context_str = request_data.get("context", "<context>\n# Error: Context not provided.\n</context>")

    if not session_path or not user_prompt_dict:
        send_message("error", session_path or "unknown", message=f"Worker received incomplete request: {list(request_data.keys())}")
        return

    model_name = config.get("model")
    if not model_name:
        send_message("error", session_path, message="Missing 'model' in config.")
        return

    # --- Initialize LLM Client ---
    try:
        llm_client = LLMClient(
            model_name=model_name,
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            verbose=config.get("verbose", False),
        )
    except Exception as e:
        send_message("error", session_path, message=f"Failed to initialize LLMClient: {e}")
        return

    # --- Interaction Handling ---
    llm_error_occurred = False
    full_response_text = ""
    error_message_detail = ""

    try:
        # 1. Prepare messages
        system_prompt = _build_system_prompt(session_path)
        final_user_content = user_prompt_dict.get("content", "") + f"\n\n{context_str}"
        messages_to_send = [
            {"role": "system", "content": system_prompt},
            *history, # Add truncated history
            {"role": "user", "content": final_user_content}
        ]

        # 2. Call LLM and stream response
        send_message("stream", session_path, role="llm", content="\nAssistant:\n")
        try:
            response_stream = llm_client.send(messages_to_send, stream=True)

            for chunk in response_stream:
                delta_content = None
                try:
                    # Simplified delta access
                    if chunk and chunk.choices and chunk.choices[0].delta:
                        delta_content = chunk.choices[0].delta.content
                except (AttributeError, IndexError, TypeError) as e:
                    # Log minor access errors but continue if possible
                    print(f"  - Minor error accessing chunk delta: {e}. Chunk: {chunk}", file=sys.stderr)
                    continue # Skip malformed chunk

                if delta_content:
                    send_message("stream", session_path, role="llm", content=delta_content)
                    full_response_text += delta_content

        except Exception as e:
            llm_error_occurred = True
            error_message_detail = f"LLM communication error: {e}"
            tb_str = traceback.format_exc()
            print(f"\n[Error during LLM communication: {e}]\n{tb_str}", file=sys.stderr)
            send_message("stream", session_path, role="error", content=f"[LLM Error: {e}]")

        # --- Finalize Interaction ---
        if llm_error_occurred:
            status = "llm_error"
            finish_message = f"Interaction ended due to LLM error: {error_message_detail}"
            finish_data = {"status": status, "message": finish_message}
        else:
            status = "success"
            finish_message = "Interaction completed."
            filtered_response_text = _filter_context(full_response_text.strip())
            assistant_response_dict = {"role": "assistant", "content": filtered_response_text}
            finish_data = {
                "status": status,
                "message": finish_message,
                "original_user_prompt": user_prompt_dict,
                "assistant_response": assistant_response_dict
            }

        send_message("finished", session_path, **finish_data)

    except Exception as e:
        # Catch broader errors in the handler logic itself
        tb_str = traceback.format_exc()
        error_msg = f"Critical error in interaction handler: {e}\n{tb_str}"
        print(error_msg, file=sys.stderr)
        # Ensure session_path is valid before sending final error messages
        valid_session_path = session_path or "unknown_session"
        # Attempt to notify Emacs about the critical failure
        send_message("stream", valid_session_path, role="error", content=f"[Worker Critical Error: {e}]")
        send_message("finished", valid_session_path, status="critical_error", message=error_msg)

def main():
    """Reads requests from stdin and handles them."""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break # End of input

            message = json.loads(line)
            if message.get("type") == "interaction_request":
                handle_interaction_request(message.get("data"))
            else:
                # Log unknown message types but don't crash
                print(f"Worker received unknown message type: {message.get('type')}", file=sys.stderr)

        except json.JSONDecodeError as e:
            print(f"Worker received invalid JSON: {line.strip()}. Error: {e}", file=sys.stderr)
            # Send error back if possible, otherwise just log
            send_message("error", "unknown", message=f"Worker received invalid JSON: {line.strip()}")
        except Exception as e:
            # Log unexpected errors in the main loop
            tb_str = traceback.format_exc()
            print(f"Worker main loop error: {e}\n{tb_str}", file=sys.stderr)
            # Send error back if possible
            send_message("error", "unknown", message=f"Worker main loop error: {e}")
            time.sleep(0.1) # Avoid tight loop on persistent error

if __name__ == "__main__":
    main()
