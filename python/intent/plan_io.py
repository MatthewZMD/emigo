from .schema import Intent

def summarize_intent(intent: Intent) -> str:
    """
    Generate a human-readable summary of an intent.
    """
    target_str = ""
    if intent.target_selector.defun_name:
        target_str = f" in function '{intent.target_selector.defun_name}'"
    elif intent.target_selector.file_path:
        target_str = f" in file '{intent.target_selector.file_path}'"
    elif intent.target_selector.buffer_name:
        target_str = f" in buffer '{intent.target_selector.buffer_name}'"

    if intent.type == "AddKeybinding":
        return f"Bind key '{intent.params['key']}' to command '{intent.params['command_name']}'."
    if intent.type == "AddDocstring":
        doc_preview = intent.params['docstring'][:50].replace('\\n', ' ') + "..."
        return f"Add docstring \"{doc_preview}\" to '{intent.params['symbol_name']}'{target_str}."
    if intent.type == "RenameSymbol":
        return f"Rename symbol '{intent.params['old_name']}' to '{intent.params['new_name']}'{target_str}."
    if intent.type == "AddDefcustom":
        return f"Add new defcustom '{intent.params['name']}'{target_str}."
    if intent.type == "WrapWith":
        return f"Wrap the target expression{target_str} with `({intent.params['wrapper_form']} ...)`."
    if intent.type == "AddArgument":
        return f"Add argument '{intent.params['arg_name']}' to function '{intent.target_selector.defun_name}'."
    if intent.type == "ReplaceSexp":
        return f"Replace S-expression{target_str}."

    # Fallback
    return f"Execute intent '{intent.type}' with params {intent.params}{target_str}."
