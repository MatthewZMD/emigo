from .schema import Intent

def generate_inverse(intent: Intent) -> Intent:
    """
    Generates the inverse of a given intent.
    """
    if not intent.inverse_type:
        raise ValueError(f"Intent type '{intent.type}' does not have a defined inverse.")

    # The registry will be needed to create the new intent instance
    from .registry import deserialize_intent

    inverse_params = {}
    # Special handling for symmetric inverses like RenameSymbol
    if intent.type == "RenameSymbol" and intent.inverse_type == "RenameSymbol":
        inverse_params = {
            "old_name": intent.params["new_name"],
            "new_name": intent.params["old_name"],
        }
    else:
        # For many intents, the inverse might not need special params.
        # This is a simplification for now.
        inverse_params = intent.params

    inverse_data = {
        "type": intent.inverse_type,
        "target_selector": intent.target_selector.__dict__,
        "params": inverse_params,
    }
    
    return deserialize_intent(inverse_data)
