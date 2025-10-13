from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import json
import sys

# To avoid circular imports, the llm_client is passed in as an argument.

@dataclass
class _HistoryItem:
    """A single item in the conversation history, including message and usage."""
    message: Dict
    usage: Optional[Dict] = None

SUMMARY_PROMPT = """You are a summarization expert. The following is a conversation history between a user and an Emacs assistant. Your task is to create a concise summary of the conversation that can be used to continue the work. The summary should cover the user's goal, progress so far, and any pending tasks.

Here is the conversation history:
---
{formatted_history}
---

Provide a detailed summary that captures all the essential information for the assistant to continue the conversation without losing context.
"""

def _format_history_for_summary(history: List[_HistoryItem]) -> str:
    """Formats the history list into a string for the summarization prompt."""
    formatted_history = ""
    for item in history:
        role = item.message.get("role")
        content = item.message.get("content", "")
        tool_calls = item.message.get("tool_calls")

        full_content = content if isinstance(content, str) else json.dumps(content)

        if tool_calls:
            full_content += f"\n[TOOL CALLS: {json.dumps(tool_calls)}]"

        formatted_history += f"--- {role.upper()} ---\n{full_content}\n\n"
    return formatted_history

def summarize_history(history: List[_HistoryItem], llm_client) -> List[_HistoryItem]:
    """
    Internal implementation of history summarization.
    """
    if not history:
        return history

    print("Summarizing conversation history...", file=sys.stderr)
    formatted_history = _format_history_for_summary(history)
    prompt = SUMMARY_PROMPT.format(formatted_history=formatted_history)

    try:
        messages = [{"role": "user", "content": prompt}]
        response_stream = llm_client.generate_plan(messages, []) # No tools for summarization

        summary_text = ""
        for chunk in response_stream:
            if chunk.choices[0].delta.content:
                summary_text += chunk.choices[0].delta.content

        if not summary_text:
            print("History summarization failed: LLM returned empty summary.", file=sys.stderr)
            return history

        instructional_summary = f"""The previous conversation history has been summarized to save tokens. Here is the summary of our progress so far:

{summary_text}

Based on this summary, please decide on the next logical step to continue the task."""

        new_history = [
            _HistoryItem(
                message={"role": "user", "content": instructional_summary},
                usage=None,  # Reset usage
            )
        ]
        print("History summarization complete.", file=sys.stderr)
        return new_history
    except Exception as e:
        print(f"Failed to summarize history: {e}", file=sys.stderr)
        # If summarization fails, return the original history to avoid losing context.
        return history

def summarize_history_if_needed(history: List[_HistoryItem], llm_client, threshold: int = 8000) -> List[_HistoryItem]:
    """
    Summarizes the conversation history if the total token count exceeds a threshold.
    """
    total_tokens = 0
    last_item = history[-1] if history else None
    if last_item and last_item.usage and "total_tokens" in last_item.usage:
        total_tokens = last_item.usage["total_tokens"]

    if total_tokens < threshold:
        return history

    print(f"Token count ({total_tokens}) exceeds threshold ({threshold}). Summarizing history.", file=sys.stderr)
    return summarize_history(history, llm_client)

def get_total_tokens(history: List[_HistoryItem]) -> int:
    """Gets total tokens from the last history item."""
    if not history:
        return 0
    # Find the last item with usage info.
    for item in reversed(history):
        if item.usage and "total_tokens" in item.usage:
            return item.usage.get("total_tokens", 0)
    return 0
