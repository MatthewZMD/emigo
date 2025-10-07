from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Literal

SafetyClass = Literal["safe", "requires_approval", "dangerous"]

@dataclass
class TargetSelector:
    """Specifies the target for an intent."""
    file_path: Optional[str] = None
    buffer_name: Optional[str] = None
    defun_name: Optional[str] = None

@dataclass
class Intent:
    """Base class for a semantic, reversible action."""
    target_selector: TargetSelector
    params: Dict[str, Any] = field(default_factory=dict)
    intent_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # The following are defined at the class level in subclasses
    type: str = field(init=False)
    safety_class: SafetyClass = field(init=False)
    inverse_type: Optional[str] = field(init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "target_selector": self.target_selector.__dict__,
            "params": self.params,
            "safety_class": self.safety_class,
            "inverse_type": self.inverse_type,
            "intent_id": self.intent_id,
        }

# --- Intent Definitions ---

@dataclass
class AddArgument(Intent):
    type: str = field(default="AddArgument", init=False, repr=False)
    safety_class: SafetyClass = field(default="safe", init=False, repr=False)
    inverse_type: Optional[str] = field(default="RemoveArgument", init=False, repr=False)

@dataclass
class WrapWith(Intent):
    type: str = field(default="WrapWith", init=False, repr=False)
    safety_class: SafetyClass = field(default="safe", init=False, repr=False)
    inverse_type: Optional[str] = field(default="Unwrap", init=False, repr=False)

@dataclass
class AddDefcustom(Intent):
    type: str = field(default="AddDefcustom", init=False, repr=False)
    safety_class: SafetyClass = field(default="safe", init=False, repr=False)
    inverse_type: Optional[str] = field(default="RemoveDefcustom", init=False, repr=False)

@dataclass
class AddKeybinding(Intent):
    type: str = field(default="AddKeybinding", init=False, repr=False)
    safety_class: SafetyClass = field(default="requires_approval", init=False, repr=False)
    inverse_type: Optional[str] = field(default="RemoveKeybinding", init=False, repr=False)

@dataclass
class InsertAdvice(Intent):
    type: str = field(default="InsertAdvice", init=False, repr=False)
    safety_class: SafetyClass = field(default="requires_approval", init=False, repr=False)
    inverse_type: Optional[str] = field(default="RemoveAdvice", init=False, repr=False)

@dataclass
class AddDocstring(Intent):
    type: str = field(default="AddDocstring", init=False, repr=False)
    safety_class: SafetyClass = field(default="safe", init=False, repr=False)
    inverse_type: Optional[str] = field(default="RemoveDocstring", init=False, repr=False)

@dataclass
class RenameSymbol(Intent):
    type: str = field(default="RenameSymbol", init=False, repr=False)
    safety_class: SafetyClass = field(default="requires_approval", init=False, repr=False)
    inverse_type: Optional[str] = field(default="RenameSymbol", init=False, repr=False)

@dataclass
class ReplaceSexp(Intent):
    type: str = field(default="ReplaceSexp", init=False, repr=False)
    safety_class: SafetyClass = field(default="requires_approval", init=False, repr=False)
    inverse_type: Optional[str] = field(default=None, init=False, repr=False)
