from typing import List, Dict, Any, Optional
from ..bridge import client as bridge_client

def list_buffers() -> List[str]:
    """Return a list of all buffer names in Emacs."""
    return bridge_client.emacs_client.call('emigo-list-buffers', [])

def buffer_metadata(buffer_name: str) -> Optional[Dict[str, Any]]:
    """Return metadata for a specific buffer."""
    return bridge_client.emacs_client.call('emigo-buffer-metadata', [buffer_name])

def current_mode() -> Optional[str]:
    """Return the major-mode of the current buffer in Emacs."""
    return bridge_client.emacs_client.call('emigo-current-mode', [])
