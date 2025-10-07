"""
Shared EPC client for communicating with Emacs.
This module exists to break a circular import between server.py and the tool modules.
"""
from epc.client import EPCClient
from typing import Optional

# Global client to communicate back to Emacs.
# Other agent modules can import this to send messages to Emacs.
emacs_client: Optional[EPCClient] = None
