import json
import os
from typing import Dict, Any
import jsonschema
from .schema import Intent

# Load the schema once
SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "schemas")
INTENT_SCHEMA_PATH = os.path.join(SCHEMA_DIR, "intents.json")

with open(INTENT_SCHEMA_PATH, "r") as f:
    INTENT_SCHEMA = json.load(f)

class IntentValidationError(Exception):
    pass

def validate_intent_schema(intent_data: Dict[str, Any]):
    """
    Validates a dictionary representing an intent against the JSON schema.
    Raises IntentValidationError on failure.
    """
    try:
        jsonschema.validate(instance=intent_data, schema=INTENT_SCHEMA)
    except jsonschema.exceptions.ValidationError as e:
        raise IntentValidationError(f"Intent failed schema validation: {e.message}") from e

def validate_intent_policy(intent: Intent):
    """
    Validates a deserialized Intent object against runtime policies.
    For now, this is a placeholder.
    """
    if intent.safety_class == "dangerous":
        # This might require special handling or be outright rejected
        # depending on the agent's policy settings.
        pass
