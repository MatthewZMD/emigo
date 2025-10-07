from ..intent.schema import Intent
from ..intent.registry import deserialize_intent
from ..intent.validate import validate_intent_schema
from ..intent.plan_io import summarize_intent
from ..bridge.server import emacs_client
from ..storage.db import storage_manager

def execute_intent(intent_data: dict) -> dict:
    """
    Validates an intent, calls the Emacs actuator, and logs it on success.
    """
    # 1. Validate against schema
    validate_intent_schema(intent_data)

    # 2. Deserialize into an Intent object
    intent = deserialize_intent(intent_data)

    # 3. Get a human-readable summary and prepare data for Emacs
    summary = summarize_intent(intent)
    emacs_client.call_async('message', f"[Emigo] Executing: {summary}")

    intent_as_dict = intent.to_dict()
    intent_as_dict['summary'] = summary # Add summary for trace log

    # 4. Call the Emacs actuator to perform the action
    result = emacs_client.call('emigo-execute-intent', intent_as_dict)

    # 5. On success, log the intent to the persistent journal
    if result and result.get('status') == 'success':
        storage_manager.log_intent(intent_as_dict)

    return result

def undo_intent(intent_id: str) -> dict:
    """Calls Emacs to undo a previously executed intent."""
    return emacs_client.call('emigo-undo-intent', intent_id)
