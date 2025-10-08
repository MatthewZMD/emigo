from ..storage.db import storage_manager
from ..tools import act, code, state, symbols
from ..planner import loop as planner_loop
from typing import Dict, Callable, Any

# This registry is partially populated here to avoid circular imports.
# The server module will add the remaining tools at startup.
# The registry of all functions callable via RPC from Emacs.
# This is used by the server to dispatch requests.
TOOL_REGISTRY: Dict[str, Callable[..., Any]] = {
    # Planner/Host control
    "start_planner": planner_loop.start_planner,
    "clear_history": planner_loop.clear_history,
    "get_history": planner_loop.get_history,

    # LLM-callable tools
    # act.py
    "execute_intent": act.execute_intent,
    "undo_intent": act.undo_intent,
    # code.py
    "read_sexps": code.read_sexps,
    "select_target": code.select_target,
    # state.py
    "list_buffers": state.list_buffers,
    "buffer_metadata": state.buffer_metadata,
    "current_mode": state.current_mode,
    # symbols.py
    "search_symbols": symbols.search_symbols,
    "read_doc": symbols.read_doc,
    "find_keybindings": symbols.find_keybindings,
    # storage
    "update_symbols_batch": storage_manager.update_symbols_batch,
}
