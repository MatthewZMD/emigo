"""
EPC server to handle communication with Emacs.
This script establishes a bidirectional communication channel:
1. It starts an EPC server that Emacs connects to as a client.
2. It connects back to an EPC server running in Emacs, acting as a client itself.
"""
import sys
import json
from epc.server import EPCServer
from epc.client import EPCClient

from ..intent.validate import IntentValidationError
from ..tools import act, code, state, symbols
from ..planner import loop as planner_loop
from ..storage.db import storage_manager


# Global client to communicate back to Emacs.
# Other agent modules can import this to send messages to Emacs.
emacs_client: EPCClient = None

def notify_file_changed(file_path: str, mtime: int):
    """
    Handle file change notifications from Emacs.
    If the file is stale, trigger a re-index.
    """
    last_mtime = storage_manager.get_doc_digest(file_path)
    if not last_mtime or mtime > last_mtime:
        emacs_client.call_async('message', f"[Emigo] File {file_path} is stale, re-indexing.")
        # This is a simplification. A more robust implementation would
        # re-index only the symbols from that file. For now, we trigger a full rebuild.
        emacs_client.call_async('emigo-index-build')
    storage_manager.update_doc_digest(file_path, mtime)

# Tool registry for the LLM to call
TOOL_REGISTRY = {
    # Planner
    "start_planner": planner_loop.run_planner_loop,
    # Agent Internals
    "update_symbols_batch": storage_manager.update_symbols_batch,
    "notify_file_changed": notify_file_changed,
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
}

def main():
    """
    Starts the EPC server, connects back to Emacs, and routes messages.
    """
    global emacs_client

    if len(sys.argv) < 2:
        print("ERROR: Missing Elisp EPC server port argument.", file=sys.stderr, flush=True)
        sys.exit(1)

    try:
        emacs_port = int(sys.argv[1])
        emacs_client = EPCClient(('localhost', emacs_port))
        emacs_client.call_async('message', "[Emigo Agent] Python agent connected to Emacs.")
    except (ValueError, ConnectionRefusedError) as e:
        print(f"ERROR: Could not connect to Emacs EPC server on port {sys.argv[1]}: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

    # This is the server that Emacs connects to.
    server = EPCServer(('localhost', 0))

    @server.register_function
    def handle_request(request_json):
        """
        Generic handler for incoming requests from Emacs.
        `request_json` is a JSON string with the envelope.
        """
        request_id = None
        try:
            request = json.loads(request_json)
            request_id = request.get("id")
            print(f"Received request: {request}", file=sys.stderr, flush=True)

            method_name = request.get("method")
            params = request.get("params", [])

            tool_func = TOOL_REGISTRY.get(method_name)
            if not tool_func:
                raise NotImplementedError(f"Method '{method_name}' not found in tool registry.")

            # The LLM planner will likely pass params as a dict.
            # The Elisp client passes params as a list.
            if isinstance(params, dict):
                result = tool_func(**params)
            elif isinstance(params, list):
                result = tool_func(*params)
            else:
                raise ValueError("Params must be a list or dict.")

            return { "id": request_id, "ok": True, "result": result }
        except (json.JSONDecodeError, NotImplementedError, ValueError, TypeError, IntentValidationError) as e:
            print(f"Error handling request: {e}", file=sys.stderr, flush=True)
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": -32600, "message": str(e)} # Invalid Request
            }
        except Exception as e:
            print(f"Internal error handling request: {e}", file=sys.stderr, flush=True)
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": -32603, "message": str(e)} # Internal Error
            }


    print(f"Starting Emigo Python agent server on port {server.port}", file=sys.stderr, flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import threading
import traceback
import time
from epc.server import ThreadingEPCServer
from epc.client import EPCClient

# This logic is simplified from legacy/utils.py
epc_client: EPCClient = None

def init_epc_client(port):
    global epc_client
    if epc_client:
        return
    epc_client = EPCClient(('127.0.0.1', port))

def close_epc_client():
    global epc_client
    if epc_client:
        epc_client.close()
        epc_client = None

def call_emacs_method(method_name, *args):
    if not epc_client:
        raise ConnectionError("EPC client not initialized.")
    epc_client.call(method_name, args)


class AgentServer:
    def __init__(self, args):
        if not args:
            print("ERROR: Missing Elisp EPC port argument.", file=sys.stderr, flush=True)
            sys.exit(1)
        try:
            elisp_epc_port = int(args[0])
            init_epc_client(elisp_epc_port) # Connect back to Emacs
        except (IndexError, ValueError) as e:
            print(f"ERROR: Invalid Elisp EPC port argument: {args}. Error: {e}", file=sys.stderr, flush=True)
            sys.exit(1)
        except Exception as e:
            print(f"ERROR: Failed to connect EPC client to Emacs: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            sys.exit(1)

        # --- EPC Server Setup ---
        try:
            self.server = ThreadingEPCServer(('127.0.0.1', 0), log_traceback=True)
            self.server.allow_reuse_address = True
            self.server.register_instance(self)
            self.server_thread = threading.Thread(target=self.server.serve_forever, name="PythonEPCServerThread", daemon=True)
            self.server_thread.start()
            time.sleep(0.1) # Give server time to bind port
            if not self.server_thread.is_alive():
                raise RuntimeError("Python EPC server thread failed to start.")
            print(f"Python EPC server listening on port {self.server.server_address[1]}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"ERROR: Failed to start Python EPC server: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            sys.exit(1)

        # --- Finalize Setup ---
        try:
            python_epc_port = self.server.server_address[1]
            call_emacs_method('emigo--first-start', python_epc_port)
            print(f"Sent emigo--first-start signal for port {python_epc_port}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"WARNING: Failed sending emigo--first-start signal to Elisp: {e}", file=sys.stderr, flush=True)

        print("Emigo Agent Server initialization complete.", file=sys.stderr, flush=True)

    def cleanup(self):
        """Clean up resources before exiting."""
        print("Running Agent Server cleanup...", file=sys.stderr)
        close_epc_client()
        print("Agent Server cleanup finished.", file=sys.stderr)

    # Placeholder for v2 entrypoint
    def instruct(self, goal: str):
        print(f"Received instruction: {goal}", file=sys.stderr, flush=True)
        # Here will be the planner loop call
        return "Instruction received."


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("ERROR: Missing Elisp EPC server port argument.", file=sys.stderr, flush=True)
        sys.exit(1)

    server_instance = None
    exit_code = 1
    try:
        server_instance = AgentServer(sys.argv[1:])

        while True:
            if not server_instance.server_thread.is_alive():
                 print("ERROR: Python EPC server thread has died. Exiting.", file=sys.stderr, flush=True)
                 break
            time.sleep(5)

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received, cleaning up...", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"\nFATAL ERROR in main execution block: {e}", file=sys.stderr, flush=True)
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        exit_code = 1
    else:
        exit_code = 0
    finally:
        if server_instance:
            try:
                server_instance.cleanup()
            except Exception as cleanup_err:
                print(f"Error during cleanup: {cleanup_err}", file=sys.stderr, flush=True)
                exit_code = 1
        print("python.bridge.server main execution finished.", file=sys.stderr, flush=True)
        sys.exit(exit_code)
