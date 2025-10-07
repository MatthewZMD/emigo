from typing import Dict, Type, Any
from .schema import (
    Intent, TargetSelector, AddArgument, WrapWith, AddDefcustom,
    AddKeybinding, InsertAdvice, AddDocstring, RenameSymbol, ReplaceSexp
)

INTENT_REGISTRY: Dict[str, Type[Intent]] = {
    "AddArgument": AddArgument,
    "WrapWith": WrapWith,
    "AddDefcustom": AddDefcustom,
    "AddKeybinding": AddKeybinding,
    "InsertAdvice": InsertAdvice,
    "AddDocstring": AddDocstring,
    "RenameSymbol": RenameSymbol,
    "ReplaceSexp": ReplaceSexp,
}

def deserialize_intent(data: Dict[str, Any]) -> Intent:
    """Given a dictionary, deserialize it into the correct Intent subclass."""
    intent_type = data.get("type")
    if not intent_type:
        raise ValueError("Intent data must have a 'type' field.")

    intent_class = INTENT_REGISTRY.get(intent_type)
    if not intent_class:
        raise ValueError(f"Unknown intent type: {intent_type}")

    instance = intent_class(
        target_selector=TargetSelector(**data["target_selector"]),
        params=data.get("params", {}),
    )
    
    # Allow intent_id to be passed in for round-tripping
    if "intent_id" in data:
        instance.intent_id = data["intent_id"]
        
    return instance
