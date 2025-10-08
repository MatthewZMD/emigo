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

from . import planner


def main():
    """
    Starts the EPC server, connects back to Emacs, and routes messages.
    """
    if len(sys.argv) < 2:
        print("ERROR: Missing Elisp EPC server port argument.", file=sys.stderr, flush=True)
        sys.exit(1)

    try:
        emacs_port = int(sys.argv[1])
        planner.emacs_client = EPCClient(('localhost', emacs_port))
        print(f"Connected to Emacs EPC server on port {emacs_port}", file=sys.stderr, flush=True)
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

            tool_func = planner.RPC_METHODS.get(method_name)
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
        except (json.JSONDecodeError, NotImplementedError, ValueError, TypeError) as e:
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


    # Finalize setup by telling Emacs the port of this server.
    try:
        python_epc_port = server.server_address[1]
        planner.emacs_client.call_sync('emigo--first-start', [python_epc_port])
        print(f"Sent emigo--first-start signal for port {python_epc_port}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"WARNING: Failed sending emigo--first-start signal to Elisp: {e}", file=sys.stderr, flush=True)


    print(f"Starting Emigo Python agent server on port {server.server_address[1]}", file=sys.stderr, flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
