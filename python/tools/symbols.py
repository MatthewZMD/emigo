from typing import List, Dict, Any, Optional

from ..bridge import client as bridge_client
from ..storage.db import storage_manager

def search_symbols(query: str, kind: Optional[str] = None, package: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Search the symbol database for symbols matching criteria, ranked by usage.
    """
    return storage_manager.search_symbols(query, kind, package)

def read_doc(symbol_name: str) -> Optional[str]:
    """Read the full docstring for a symbol from Emacs."""
    return bridge_client.emacs_client.call('emacs_symbol_doc', [symbol_name])

def find_keybindings(symbol_name: str) -> List[str]:
    """Find keybindings for a given command symbol in Emacs."""
    return bridge_client.emacs_client.call('emacs_keybindings', [symbol_name])
