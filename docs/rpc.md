# Emigo RPC Protocol

This document specifies the JSON-based RPC protocol used for communication between the Emacs Lisp frontend and the Python agent core. The transport layer is EPC (Emacs Lisp RPC).

## Message Envelope

All communication is wrapped in a standard message envelope.

### Request

A request from Emacs to Python to execute a method.

- `id`: A unique string (e.g., UUID) to correlate requests with responses.
- `type`: Must be `"call"`.
- `method`: The name of the method to invoke on the agent (e.g., `"search_symbols"`).
- `params`: A JSON object or array containing the parameters for the method.

**Example:**
```json
{
  "id": "c7b7a5d0-1b1f-4e8d-8f2b-0e1c5a9b3d4e",
  "type": "call",
  "method": "execute_intent",
  "params": [
    {
      "type": "AddKeybinding",
      "target_selector": { "file_path": "/path/to/your/init.el" },
      "params": {
        "command_name": "my-new-function",
        "key": "C-c e k"
      }
    }
  ]
}
```

### Response

A response from Python to Emacs after executing a method.

- `id`: The `id` from the original request.
- `ok`: A boolean indicating success (`true`) or failure (`false`).
- `result`: If `ok` is `true`, this field contains the return value of the method.
- `error`: If `ok` is `false`, this field contains an error object.

> **Note:** While the examples below use JSON, the EPC transport layer handles the serialization. In the Python agent, a response is a standard dictionary. In Emacs Lisp, it is received as a property list (plist). The RPC client functions are responsible for interpreting this structure.

**Example (Success):**
```json
{
  "id": "c7b7a5d0-1b1f-4e8d-8f2b-0e1c5a9b3d4e",
  "ok": true,
  "result": {
    "status": "intent_executed",
    "outcome": "Key 'C-c e k' is now bound to 'my-new-function'."
  }
}
```

**Example (Error):**
```json
{
  "id": "c7b7a5d0-1b1f-4e8d-8f2b-0e1c5a9b3d4e",
  "ok": false,
  "error": {
    "code": -32601,
    "message": "Method not found: 'execute_intent_typo'"
  }
}
```

### Notification

A notification is a request where no response is expected. The `id` field may be omitted. The structure is otherwise identical to a request. This is useful for fire-and-forget messages like logging or state updates.

## Standard Error Codes

| Code    | Message          | Meaning                                        |
|---------|------------------|------------------------------------------------|
| -32700  | Parse error      | Invalid JSON was received by the server.       |
| -32600  | Invalid Request  | The JSON sent is not a valid Request object.   |
| -32601  | Method not found | The method does not exist / is not available.  |
| -32602  | Invalid params   | Invalid method parameter(s).                   |
| -32603  | Internal error   | Internal JSON-RPC error.                       |
| -32000 to -32099 | Server error | Reserved for implementation-defined server-errors. |
