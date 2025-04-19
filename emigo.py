#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Central orchestrator for the Emigo Python backend.

Copyright (C) 2025 Emigo
Author: Mingde (Matthew) Zeng <matthewzmd@posteo.net>
        Andy Stewart <lazycat.manatee@gmail.com>
Maintainer: Mingde (Matthew) Zeng <matthewzmd@posteo.net>
            Andy Stewart <lazycat.manatee@gmail.com>
"""


import os
import sys
import threading
import traceback
import subprocess
import json
import queue
import time
from typing import Dict, List, Optional, Tuple

from epc.server import ThreadingEPCServer

from context import Context
# Import utility functions
from utils import (close_epc_client, eval_in_emacs, get_emacs_func_result,
                   get_emacs_vars, init_epc_client, message_emacs,
                   _filter_context)

class Emigo:
    def __init__(self, args):
        print("Emigo __init__: Starting initialization...", file=sys.stderr, flush=True) # DEBUG + flush
        print(f"Emigo __init__: Received args: {args}", file=sys.stderr, flush=True)
        if not args:
            print("Emigo __init__: ERROR - No parameters received (expected EPC port). Exiting.", file=sys.stderr, flush=True)
            sys.exit(1)
        try:
            elisp_epc_port = int(args[0])
            print(f"Emigo __init__: Attempting to connect to Elisp EPC server on port {elisp_epc_port}...", file=sys.stderr, flush=True) # DEBUG + flush
            # Initialize EPC client connection to Emacs
            init_epc_client(elisp_epc_port)
            print(f"Emigo __init__: EPC client initialized for Elisp port {elisp_epc_port}", file=sys.stderr, flush=True) # DEBUG + flush
        except (IndexError, ValueError) as e:
            print(f"Emigo __init__: ERROR - Invalid or missing Elisp EPC port argument: {args}. Error: {e}", file=sys.stderr, flush=True) # DEBUG + flush
            sys.exit(1)
        except Exception as e:
            print(f"Emigo __init__: ERROR initializing/connecting EPC client to Elisp: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True) # DEBUG + flush
            sys.exit(1) # Exit if we can't connect back to Emacs

        # Init vars.
        print("Emigo __init__: Initializing internal variables...", file=sys.stderr, flush=True) # DEBUG + flush
        # Replace individual state dicts with a single sessions dictionary
        self.sessions: Dict[str, Context] = {} # Key: session_path, Value: Context object

        # --- Worker Process Management ---
        self.llm_worker_process: Optional[subprocess.Popen] = None
        self.llm_worker_reader_thread: Optional[threading.Thread] = None
        self.llm_worker_stderr_thread: Optional[threading.Thread] = None
        self.llm_worker_lock = threading.Lock()
        self.worker_output_queue = queue.Queue() # Messages from worker stdout
        self.active_interaction_session: Optional[str] = None # Track which session is currently interacting

        # --- EPC Server Setup ---
        print("Emigo __init__: Setting up Python EPC server...", file=sys.stderr, flush=True) # DEBUG + flush
        try:
            self.server = ThreadingEPCServer(('127.0.0.1', 0), log_traceback=True)
            # self.server.logger.setLevel(logging.DEBUG)
            self.server.allow_reuse_address = True
            print(f"Emigo __init__: Python EPC server created. Will listen on port {self.server.server_address[1]}", file=sys.stderr, flush=True) # DEBUG + flush
        except Exception as e:
            print(f"Emigo __init__: ERROR creating Python EPC server: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True) # DEBUG + flush
            sys.exit(1)

        # ch = logging.FileHandler(filename=os.path.join(emigo_config_dir, 'epc_log.txt'), mode='w')
        # formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(lineno)04d | %(message)s')
        # ch.setFormatter(formatter)
        # ch.setLevel(logging.DEBUG)
        # self.server.logger.addHandler(ch)
        # self.server.logger = logger # Keep logging setup if needed

        print("Emigo __init__: Registering instance methods with Python EPC server...", file=sys.stderr, flush=True) # DEBUG + flush
        self.server.register_instance(self)  # register instance functions let elisp side call
        print("Emigo __init__: Instance registered with Python EPC server.", file=sys.stderr, flush=True) # DEBUG + flush

        # Start Python EPC server with sub-thread.
        try:
            print("Emigo __init__: Starting Python EPC server thread...", file=sys.stderr, flush=True) # DEBUG + flush
            self.server_thread = threading.Thread(target=self.server.serve_forever, name="PythonEPCServerThread")
            self.server_thread.daemon = True # Allow main thread to exit even if this hangs
            self.server_thread.start()
            # Give the server a moment to bind the port
            time.sleep(0.1)
            if not self.server_thread.is_alive():
                print("Emigo __init__: ERROR - Python EPC server thread failed to start.", file=sys.stderr, flush=True)
                sys.exit(1)
                print(f"Emigo __init__: Python EPC server thread started. Listening on port {self.server.server_address[1]}", file=sys.stderr, flush=True) # DEBUG + flush
        except Exception as e:
            print(f"Emigo __init__: ERROR starting Python EPC server thread: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True) # DEBUG + flush
            sys.exit(1) # Exit if server thread fails

        # Start the worker process
        print("Emigo __init__: Starting LLM worker process...", file=sys.stderr, flush=True) # DEBUG + flush
        self._start_llm_worker()
        # Check if worker started successfully
        worker_ok = False
        with self.llm_worker_lock: # Ensure check happens after potential start attempt
            if self.llm_worker_process and self.llm_worker_process.poll() is None:
                worker_ok = True

        if not worker_ok:
            print("Emigo __init__: ERROR - LLM worker process failed to start or exited immediately.", file=sys.stderr, flush=True)
            # Attempt to read stderr if process object exists
            if self.llm_worker_process and self.llm_worker_process.stderr:
                try:
                    stderr_output = self.llm_worker_process.stderr.read()
                    print(f"Emigo __init__: Worker stderr upon exit:\n{stderr_output}", file=sys.stderr, flush=True)
                except Exception as read_err:
                    print(f"Emigo __init__: Error reading worker stderr after exit: {read_err}", file=sys.stderr, flush=True)
                    sys.exit(1) # Exit if worker failed

        print("Emigo __init__: LLM worker process started successfully.", file=sys.stderr, flush=True) # DEBUG + flush


        self.worker_processor_thread = threading.Thread(target=self._process_worker_queue, name="WorkerQueueProcessorThread", daemon=True)
        self.worker_processor_thread.start()
        if not self.worker_processor_thread.is_alive():
            print("Emigo __init__: ERROR - Worker queue processor thread failed to start.", file=sys.stderr, flush=True)
            sys.exit(1)
            print("Emigo __init__: Worker queue processor thread started.", file=sys.stderr, flush=True) # DEBUG + flush

        # Pass Python epc port back to Emacs when first start emigo.
        try:
            python_epc_port = self.server.server_address[1]
            print(f"Emigo __init__: Sending emigo--first-start signal to Elisp for Python EPC port {python_epc_port}...", file=sys.stderr, flush=True) # DEBUG + flush
            eval_in_emacs('emigo--first-start', python_epc_port)
            print(f"Emigo __init__: Sent emigo--first-start signal for port {python_epc_port}", file=sys.stderr, flush=True) # DEBUG + flush
        except Exception as e:
            # This might happen if Emacs EPC server isn't ready yet or the connection failed earlier.
            print(f"Emigo __init__: ERROR sending emigo--first-start signal to Elisp: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True) # DEBUG + flush
            # Don't exit here, maybe the connection will recover, but log clearly.

        # Initialization complete. The main thread will likely wait for EPC events or signals.
        print("Emigo __init__: Initialization sequence complete. Emigo should be running.", file=sys.stderr, flush=True) # DEBUG + flush

    # --- Worker Process Management ---

    def _start_llm_worker(self):
        """Starts the llm_worker.py subprocess."""
        with self.llm_worker_lock:
            if self.llm_worker_process and self.llm_worker_process.poll() is None:
                print("LLM worker process already running.", file=sys.stderr)
                return # Already running

            worker_script = os.path.join(os.path.dirname(__file__), "llm_worker.py")
            python_executable = sys.executable # Use the same python interpreter
            worker_script_path = os.path.abspath(worker_script)

            try:
                print(f"_start_llm_worker: Starting LLM worker process: {python_executable} {worker_script_path}", file=sys.stderr, flush=True) # DEBUG + flush
                self.llm_worker_process = subprocess.Popen(
                    [python_executable, worker_script_path],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, # Capture stderr
                    text=True, # Work with text streams
                    encoding='utf-8', # Ensure UTF-8 for JSON
                    bufsize=0, # Use 0 for unbuffered binary mode (stdin/stdout)
                    # bufsize=1, # Use 1 for line buffered text mode
                    cwd=os.path.dirname(worker_script_path), # Set CWD to script's directory
                    # Use process_group=True on Unix-like systems if needed for cleaner termination
                    # process_group=True if os.name != 'nt' else False
                )
                # Brief pause to see if process exits immediately
                time.sleep(0.5) # Increased sleep time
                if self.llm_worker_process.poll() is not None:
                    print(f"_start_llm_worker: ERROR - LLM worker process exited immediately with code {self.llm_worker_process.poll()}.", file=sys.stderr, flush=True)
                    # Try reading stderr quickly
                    try:
                        stderr_output = self.llm_worker_process.stderr.read() if self.llm_worker_process.stderr else "N/A"
                        print(f"_start_llm_worker: Worker stderr upon exit:\n{stderr_output}", file=sys.stderr, flush=True)
                    except Exception as read_err:
                        print(f"_start_llm_worker: Error reading worker stderr after exit: {read_err}", file=sys.stderr, flush=True)

                    # Regardless of stderr read success, set process to None and notify Emacs
                    exit_code = self.llm_worker_process.poll() # Get exit code again just in case
                    self.llm_worker_process = None
                    message_emacs(f"Error: LLM worker process failed to start (exit code {exit_code}). Check *Messages* or Emigo process buffer.")
                    return # Exit the function

                print(f"_start_llm_worker: LLM worker started (PID: {self.llm_worker_process.pid}).", file=sys.stderr, flush=True) # DEBUG + flush

                # Create and start the stdout reader thread *after* process starts
                print("_start_llm_worker: Starting stdout reader thread...", file=sys.stderr, flush=True) # DEBUG + flush
                self.llm_worker_reader_thread = threading.Thread(target=self._read_worker_stdout, name="WorkerStdoutReader", daemon=True)
                self.llm_worker_reader_thread.start()
                if not self.llm_worker_reader_thread.is_alive(): # DEBUG + flush
                    print("_start_llm_worker: ERROR - stdout reader thread failed to start.", file=sys.stderr, flush=True) # DEBUG + flush
                    # Attempt to stop worker if it's running
                    if self.llm_worker_process and self.llm_worker_process.poll() is None:
                        self.llm_worker_process.terminate()
                        self.llm_worker_process = None
                    return

                print("_start_llm_worker: Starting stderr reader thread...", file=sys.stderr, flush=True) # DEBUG + flush
                self.llm_worker_stderr_thread = threading.Thread(target=self._read_worker_stderr, name="WorkerStderrReader", daemon=True)
                self.llm_worker_stderr_thread.start()
                if not self.llm_worker_stderr_thread.is_alive(): # DEBUG + flush
                    print("_start_llm_worker: ERROR - stderr reader thread failed to start.", file=sys.stderr, flush=True)
                    # Attempt cleanup
                    if self.llm_worker_process and self.llm_worker_process.poll() is None:
                        self.llm_worker_process.terminate()
                        self.llm_worker_process = None
                    return

                print("_start_llm_worker: Worker process and reader threads seem to be started.", file=sys.stderr, flush=True)

            except Exception as e:
                print(f"_start_llm_worker: Failed to start LLM worker: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
                self.llm_worker_process = None
                # Optionally notify Emacs of the failure
                message_emacs(f"Error: Failed to start LLM worker subprocess: {e}")

    def _stop_llm_worker(self):
        """Stops the LLM worker subprocess and reader threads."""
        with self.llm_worker_lock:
            if self.llm_worker_process:
                print("Stopping LLM worker process...", file=sys.stderr)
                if self.llm_worker_process.poll() is None: # Check if still running
                    try:
                        # Try closing stdin first to signal worker
                        if self.llm_worker_process.stdin:
                            self.llm_worker_process.stdin.close()
                    except OSError:
                        pass # Ignore errors if already closed
                    try:
                        self.llm_worker_process.terminate() # Ask nicely first
                        self.llm_worker_process.wait(timeout=2) # Wait a bit
                    except subprocess.TimeoutExpired:
                        print("LLM worker did not terminate gracefully, killing.", file=sys.stderr)
                        self.llm_worker_process.kill() # Force kill
                    except Exception as e:
                        print(f"Error stopping LLM worker: {e}", file=sys.stderr)
                        self.llm_worker_process = None # Ensure process is marked as None
                        print("LLM worker process stopped.", file=sys.stderr)

            # Signal and wait for the queue processor thread to finish
            if hasattr(self, 'worker_processor_thread') and self.worker_processor_thread and self.worker_processor_thread.is_alive():
                print("Signaling worker queue processor thread to stop...", file=sys.stderr)
                self.worker_output_queue.put(None) # Signal loop to exit
                self.worker_processor_thread.join(timeout=2) # Wait for it
                if self.worker_processor_thread.is_alive():
                    print("Warning: Worker queue processor thread did not exit cleanly.", file=sys.stderr)
                    self.worker_processor_thread = None # Mark as stopped

    def _read_worker_stdout(self):
        """Reads stdout lines from the worker and puts them in a queue."""
        # Use a loop that checks if the process is alive
        proc = self.llm_worker_process # Local reference
        if proc and proc.stdout:
            try:
                for line in iter(proc.stdout.readline, ''):
                    if line:
                        self.worker_output_queue.put(line.strip())
                    else:
                        # Empty string indicates EOF (stream closed)
                        print("LLM worker stdout stream ended (EOF).", file=sys.stderr)
                        break
            except ValueError as e:
                # Catch ValueError: I/O operation on closed file.
                print(f"Error reading from LLM worker stdout (stream likely closed): {e}", file=sys.stderr)
            except Exception as e:
                # Handle other exceptions during read
                print(f"Error reading from LLM worker stdout: {e}", file=sys.stderr)
            finally:
                # Ensure the sentinel is put even if errors occur or loop finishes
                print("Signaling end of worker output.", file=sys.stderr)
                self.worker_output_queue.put(None)
        else:
            print("Worker process or stdout not available for reading.", file=sys.stderr)
            # Still signal end if the thread was started but process died quickly
            self.worker_output_queue.put(None)

    def _read_worker_stderr(self):
        """Reads and prints stderr lines from the worker."""
        # Use a loop that checks if the process is alive
        proc = self.llm_worker_process # Local reference
        if proc and proc.stderr:
            try:
                for line in iter(proc.stderr.readline, ''):
                    if line:
                        # Print worker errors clearly marked
                        print(f"[WORKER_STDERR] {line.strip()}", file=sys.stderr, flush=True)
                    else:
                        # Empty string indicates EOF
                        print("LLM worker stderr stream ended (EOF).", file=sys.stderr)
                        break
            except ValueError as e:
                # Catch ValueError: I/O operation on closed file.
                print(f"Error reading from LLM worker stderr (stream likely closed): {e}", file=sys.stderr)
            except Exception as e:
                print(f"Error reading from LLM worker stderr: {e}", file=sys.stderr)
        else:
            print("Worker process or stderr not available for reading.", file=sys.stderr)

    def _send_to_worker(self, data: Dict):
        """Sends a JSON message to the worker's stdin."""
        with self.llm_worker_lock:
            if not self.llm_worker_process or self.llm_worker_process.poll() is not None:
                print("Cannot send to worker, process not running. Attempting restart...", file=sys.stderr)
                self._start_llm_worker() # Try restarting
                if not self.llm_worker_process:
                    print("Worker restart failed. Cannot send message.", file=sys.stderr)
                    # Notify Emacs about the failure
                    session = data.get("session", "unknown")
                    eval_in_emacs("emigo--flush-buffer", session, "[Error: LLM worker process is not running]", "error")
                    return

            if self.llm_worker_process and self.llm_worker_process.stdin:
                try:
                    json_str = json.dumps(data) + '\n' # Add newline separator
                    # print(f"Sending to worker: {json_str.strip()}", file=sys.stderr) # Debug
                    self.llm_worker_process.stdin.write(json_str)
                    self.llm_worker_process.stdin.flush()
                except (OSError, BrokenPipeError, ValueError) as e: # Added ValueError for closed file
                    print(f"Error sending to LLM worker (Pipe closed or invalid state): {e}", file=sys.stderr)
                    # Worker has likely crashed or exited. Stop tracking it.
                    self._stop_llm_worker() # Attempt cleanup, might set self.llm_worker_process to None
                    # Notify Emacs about the failure
                    session = data.get("session", "unknown")
                    eval_in_emacs("emigo--flush-buffer", session, f"[Error: Failed to send message to worker ({e})]", "error")
                except Exception as e:
                    print(f"Unexpected error sending to LLM worker: {e}", file=sys.stderr)
                    # Also notify Emacs
                    session = data.get("session", "unknown")
                    eval_in_emacs("emigo--flush-buffer", session, f"[Error: Unexpected error sending message to worker ({e})]", "error")
            elif not self.llm_worker_process: # Check if process is None
                 print("Cannot send to worker, process is not running.", file=sys.stderr)
                 # Notify Emacs
                 session = data.get("session", "unknown")
                 eval_in_emacs("emigo--flush-buffer", session, "[Error: LLM worker process is not running]", "error")
            else: # Process exists but stdin might be closed
                 print("Cannot send to worker, stdin not available or closed.", file=sys.stderr)
                 # Notify Emacs
                 session = data.get("session", "unknown")
                 eval_in_emacs("emigo--flush-buffer", session, "[Error: Cannot write to LLM worker process]", "error")


    def _process_worker_queue(self):
        """Processes messages received from the worker via the queue."""
        while True:
            line = self.worker_output_queue.get()
            if line is None:
                print("Worker output queue processing stopped.", file=sys.stderr)
                break # Sentinel value received

            try:
                message = json.loads(line)
                msg_type = message.get("type")
                session_path = message.get("session")

                if not session_path:
                    print(f"Worker message missing session path: {message}", file=sys.stderr)
                    continue

                # print(f"Processing worker message: {message}", file=sys.stderr) # Debug

                if msg_type == "stream":
                    role = message.get("role", "llm") # e.g., "llm", "user", "tool_json", "tool_json_args"
                    content = message.get("content", "") # Default to empty string
                    if role != "tool_json_args":
                        filtered_content = _filter_context(content)
                    else:
                        filtered_content = content # Pass tool args unfiltered

                    # Flush to Emacs if content is non-empty OR if it's a tool start marker
                    if filtered_content or role == "tool_json":
                        # Pass all relevant info to Elisp (tool info removed)
                        eval_in_emacs("emigo--flush-buffer", session_path, filtered_content, role) # Removed tool_id, tool_name
                    # History is updated via the 'finished' message

                # Removed tool_request handling block

                elif msg_type == "finished":
                    status = message.get("status", "unknown")
                    finish_message = message.get("message", "")
                    print(f"Worker finished interaction for {session_path}. Status: {status}. Message: {finish_message}", file=sys.stderr)

                    # Clear active session *before* processing history or signaling Emacs
                    if self.active_interaction_session == session_path:
                        self.active_interaction_session = None # Mark session as no longer active
                        print(f"Cleared active interaction flag for session: {session_path}", file=sys.stderr) # Debug

                    # Append final assistant message to history here if needed
                    # If the interaction finished successfully, update the session history
                    if status in ["success", "max_turns_reached"]:
                        final_history = message.get("final_history")
                        if final_history and isinstance(final_history, list):
                            context = self._get_or_create_context(session_path)
                            if context:
                                # Filter history content before setting it
                                filtered_history = []
                                for msg in final_history:
                                    if isinstance(msg, dict) and "content" in msg:
                                        filtered_msg = dict(msg) # Copy message
                                        # Ensure content is string before filtering
                                        if not isinstance(filtered_msg.get("content"), str):
                                            filtered_msg["content"] = str(filtered_msg.get("content", ""))
                                        filtered_msg["content"] = _filter_context(filtered_msg["content"])
                                        filtered_history.append(filtered_msg)
                                    else:
                                        filtered_history.append(msg) # Keep non-dict or content-less items as is

                                print(f"Updating session history for {session_path} with {len(filtered_history)} filtered messages.", file=sys.stderr)
                                context.set_history(filtered_history) # Use the filtered history
                            else:
                                print(f"Error: Could not find context {session_path} to update history.", file=sys.stderr) # Update error message
                        elif status in ["success", "max_turns_reached"]: # Only warn if history was expected
                            print(f"Warning: Worker finished successfully but did not provide final history for {session_path}.", file=sys.stderr) # DEBUG

                    # Signal Emacs regardless of history update success
                    eval_in_emacs("emigo--agent-finished", session_path)
                    # active_interaction_session is now cleared earlier

                elif msg_type == "error":
                    error_msg = message.get("message", "Unknown error from worker")
                    # DEBUG: Print the raw error message received
                    print(f"Raw error message from worker ({session_path}): {message}", file=sys.stderr)
                    print(f"Error from worker ({session_path}): {error_msg}", file=sys.stderr) # DEBUG
                    eval_in_emacs("emigo--flush-buffer", session_path, f"[Worker Error: {error_msg}]", "error")
                    # If an error occurs, consider the interaction finished
                    if self.active_interaction_session == session_path:
                        self.active_interaction_session = None
                        print(f"Cleared active interaction flag for session {session_path} due to worker error.", file=sys.stderr) # Debug

                # get_context_request is removed as context is generated upfront in emigo_send

                elif msg_type == "pong": # Handle ping response
                    print(f"Received pong from worker for session: {session_path}", file=sys.stderr) # DEBUG

                # Handle other message types (status, etc.) if needed
            except json.JSONDecodeError:
                print(f"Received invalid JSON from worker queue: {line}", file=sys.stderr)
            except Exception as e:
                print(f"Error processing worker queue message: {e}\n{traceback.format_exc()}", file=sys.stderr)

    # --- Context Management ---

    def _get_or_create_context(self, session_path: str) -> Optional[Context]:
        """Gets the Context object for a path, creating it if necessary."""
        if not os.path.isdir(session_path):
            print(f"ERROR: Invalid context path (not a directory): {session_path}", file=sys.stderr)
            # Maybe notify Emacs here?
            eval_in_emacs("message", f"[Emigo Error] Invalid context path: {session_path}")
            return None

        if session_path not in self.sessions:
            print(f"Creating new context object for: {session_path}", file=sys.stderr)
            # Get verbose setting from config (assuming a way to get it, defaulting to True for now)
            # Example: config_verbose = get_config_value('verbose', True)
            config_verbose = True # Placeholder
            self.sessions[session_path] = Context(session_path=session_path, verbose=config_verbose)
        return self.sessions[session_path]

    # --- EPC Methods Called by Emacs ---

    def get_history(self, session_path: str) -> List[Tuple[float, Dict]]:
        """EPC: Retrieves the chat history via the Context object."""
        context = self._get_or_create_context(session_path)
        if not context:
            message_emacs(f"Error: Could not establish context for {session_path}")
            return []
        return context.get_history()

    def add_file_to_context(self, session_path: str, filename: str) -> bool:
        """EPC: Adds a file via the Context object."""
        context = self._get_or_create_context(session_path)
        if not context:
            message_emacs(f"Error: Could not establish context for {session_path}")
            return False
        # Call context method directly
        success, msg = context.add_file_to_context(filename)
        # Context class handles messaging Emacs
        return success

    def remove_file_from_context(self, session_path: str, filename: str) -> bool:
        """EPC: Removes a file via the Context object."""
        context = self._get_or_create_context(session_path)
        if not context:
            message_emacs(f"Error: No context found for {session_path}")
            return False
        # Call context method directly
        success, msg = context.remove_file_from_context(filename)
        # Context class handles messaging Emacs
        return success

    def get_chat_files(self, session_path: str) -> List[str]:
        """EPC: Retrieves the list of chat files via the Context object."""
        context = self._get_or_create_context(session_path)
        if not context:
            message_emacs(f"Error: No context found for {session_path}")
            return [] # Return empty list on error
        # Call context method directly
        return context.get_chat_files()

    def emigo_send(self, session_path: str, prompt: str, history_override: Optional[List[Dict]] = None):
        """
        EPC: Handles a user prompt or revised history to initiate an LLM interaction.

        Gets the session, handles history override or appends the new prompt,
        generates the context string (which also handles @file mentions),
        retrieves necessary config, and sends the interaction request to the worker.

        Args:
        Args:
            session_path: The path identifying the session.
            prompt: The user's input prompt (used if history_override is None).
            history_override: If provided, replaces the current session history via the Context.
        """
        # --- Pre-Interaction Checks ---
        if history_override:
            print(f"Received revised history for session: {session_path}", file=sys.stderr) # DEBUG
            if not isinstance(history_override, list):
                 message_emacs(f"[Emigo Error] Received history_override is not a list: {type(history_override)}")
                 return
            # Assuming history_override is List[Dict] as sent from Elisp
            history_dicts = history_override
        else:
            print(f"Received prompt for session: {session_path}: {prompt[:100]}...", file=sys.stderr) # DEBUG (limit prompt length)

        # Check if another interaction is already running
        if self.active_interaction_session:
            # DEBUG: Log which session is active
            print(f"DEBUG: Active interaction session is {self.active_interaction_session}", file=sys.stderr)
            # Determine message based on whether it's a new prompt or revised history
            action_desc = "re-run with the revised history" if history_override else "re-run with your new prompt"
            print(f"Interaction already active for session {self.active_interaction_session}. Asking user about new request for {session_path}.", file=sys.stderr) # DEBUG
            try:
                # Ask user in Emacs if they want to cancel the active session and proceed
                confirm_cancel = get_emacs_func_result("yes-or-no-p",
                                                       f"LLM is currently running for '{self.active_interaction_session}', do you want to stop it and {action_desc}?")

                if confirm_cancel:
                    print(f"User confirmed cancellation of {self.active_interaction_session}. Proceeding with {session_path}.", file=sys.stderr) # DEBUG
                    # Cancel the currently active interaction. This also resets self.active_interaction_session.
                    if not self.cancel_llm_interaction(self.active_interaction_session): # Check return value
                        message_emacs("[Emigo Error] Failed to cancel previous interaction.")
                        return # Stop if cancellation failed
                    # Cancellation successful, self.active_interaction_session is now None
                    print(f"DEBUG: Active session should be None after successful cancellation: {self.active_interaction_session}", file=sys.stderr)
                else:
                    # User declined, ignore the new request
                    ignore_desc = "Revised history" if history_override else "New prompt"
                    print(f"User declined cancellation. Ignoring {ignore_desc.lower()} for {session_path}.", file=sys.stderr) # DEBUG
                    eval_in_emacs("message", f"[Emigo] LLM busy with {self.active_interaction_session}. {ignore_desc} ignored.")
                    return # Stop processing the new request

            except Exception as e:
                print(f"Error during confirmation/cancellation: {e}\n{traceback.format_exc()}", file=sys.stderr) # DEBUG
                message_emacs(f"[Emigo Error] Failed to ask for cancellation confirmation: {e}") # Keep error message generic
                return # Stop processing on error

        # --- Prepare Interaction ---
        # If we reach here, either no interaction was active, or the user confirmed cancellation.
        # Mark the *new* context as active *before* any potentially failing operations.
        self.active_interaction_session = session_path
        print(f"DEBUG: Set active interaction context to: {self.active_interaction_session}", file=sys.stderr)

        # Get or create the context object
        context = self._get_or_create_context(session_path)
        if not context:
            # Error already logged by _get_or_create_context
            eval_in_emacs("emigo--flush-buffer", f"invalid-context-{session_path}", f"[Error: Invalid context path '{session_path}']", "error")
            self.active_interaction_session = None # Clear flag on error
            return

        # --- History & Context Handling (Directly on Context) ---
        if history_override:
            # Replace the context's history
            print(f"Replacing history for context {session_path}.", file=sys.stderr) # DEBUG
            context.set_history(history_dicts)
            # Optionally flush a marker to Emacs buffer?
            eval_in_emacs("emigo--flush-buffer", context.session_path, "\n[History revised by user]\n", "info") # Example
            # Generate context string *without* processing the placeholder prompt for mentions
            context_str = context.generate_context_string(current_prompt=None)
        else:
            # Standard prompt: Flush to buffer and append to history
            eval_in_emacs("emigo--flush-buffer", context.session_path, f"\n\nUser:\n{prompt}\n", "user")
            context.append_history({"role": "user", "content": prompt})
            # Generate context string *and* handle @file mentions in the prompt
            print(f"Generating context string for {session_path}, processing prompt for mentions.", file=sys.stderr) # DEBUG
            context_str = context.generate_context_string(current_prompt=prompt)

        # --- Prepare data for worker ---
        # Get current state snapshot from the Context
        session_history = context.get_history()
        session_chat_files = context.get_chat_files()

        # Get model config from Emacs vars
        vars_result = get_emacs_vars(["emigo-model", "emigo-base-url", "emigo-api-key"])
        if not vars_result or len(vars_result) < 3:
            # DEBUG: Log the result received from Emacs
            print(f"DEBUG: Failed to get Emacs vars. Received: {vars_result}", file=sys.stderr)
            message_emacs(f"Error retrieving Emacs variables for context {session_path}.")
            self.active_interaction_session = None # Unset active context
            return
        # Unpack results carefully, providing defaults if None
        model = vars_result[0]
        base_url = vars_result[1]
        api_key = vars_result[2]

        # Validate model format (must exist and contain '/')
        if not model or '/' not in model:
            error_msg = f"Invalid or missing emigo-model: '{model}'. Expected 'provider/model_name' (e.g., 'ollama/llama3', 'openai/gpt-4o')."
            print(f"ERROR: {error_msg}", file=sys.stderr) # Log error
            message_emacs(f"[Emigo Error] {error_msg}") # Send error to Emacs user
            self.active_interaction_session = None # Unset active context
            return

        worker_config = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "verbose": context.verbose
        }

        # Prepare the state snapshot for the worker
        # Use the last message content from history as the nominal 'prompt' for the worker.
        # This is important because the worker uses it for context/logging.
        effective_prompt = session_history[-1][1].get("content", "") if session_history else prompt

        request_data = {
            "session_path": context.session_path, # Use absolute path from context
            "prompt": effective_prompt, # Use content of last message or original prompt
            "history": session_history, # Pass current history snapshot (list of tuples)
            "config": worker_config,
            "chat_files": session_chat_files, # Pass chat files snapshot
            "context": context_str, # Pass generated context string
        }

        # --- Send request to worker ---
        print(f"Sending interaction request to worker for context {context.session_path}", file=sys.stderr) # DEBUG
        self._send_to_worker({
            "type": "interaction_request",
            "data": request_data
        })
        # The response handling happens asynchronously in _process_worker_queue

    def cancel_llm_interaction(self, session_path: str) -> bool:
        """
        Cancels the current LLM interaction by killing and restarting the worker.
        Also clears the active session flag and invalidates the session cache.

        Returns:
            bool: True if cancellation (including worker restart) was successful, False otherwise.
        """
        print(f"Received request to cancel interaction for session: {session_path}", file=sys.stderr) # DEBUG
        # Check if the cancellation request is for the currently active session
        if self.active_interaction_session != session_path:
            message_emacs(f"No active interaction found for session {session_path} to cancel.")
            # Return True because there was nothing *to* cancel for this session.
            # Or False? Let's return False as no cancellation *action* was performed.
            return False

        print("Stopping and restarting LLM worker due to cancellation request...", file=sys.stderr) # DEBUG
        self._stop_llm_worker() # Stops process and queue processor thread

        # Drain the queue *after* stopping the old processor thread
        print("Draining worker output queue...", file=sys.stderr) # DEBUG
        drained_count = 0
        while True: # Loop until queue is empty or error
            try:
                # Use timeout to avoid blocking indefinitely if queue is empty
                stale_msg = self.worker_output_queue.get(block=True, timeout=0.1)
                if stale_msg is None: # Check for sentinel from previous run
                    continue
                # print(f"Discarding stale message: {stale_msg}", file=sys.stderr) # Optional: very verbose
                drained_count += 1
                self.worker_output_queue.task_done() # Mark task as done
            except queue.Empty:
                break # Exit loop when queue is empty
            except Exception as e:
                print(f"Error draining queue: {e}", file=sys.stderr) # DEBUG
                break # Stop draining on error
        print(f"Worker output queue drained ({drained_count} messages discarded).", file=sys.stderr) # DEBUG

        # Restart the worker process
        self._start_llm_worker() # Starts process and reader threads

        # Check if worker restart was successful before proceeding
        worker_restarted_ok = False
        with self.llm_worker_lock:
            if self.llm_worker_process and self.llm_worker_process.poll() is None:
                worker_restarted_ok = True

        if not worker_restarted_ok:
            print("ERROR: Failed to restart LLM worker after cancellation.", file=sys.stderr) # DEBUG
            message_emacs("[Emigo Error] Failed to restart LLM worker after cancellation.")
            # Clear active session state even on failure
            self.active_interaction_session = None
            return False # Indicate failure

        print("LLM worker restarted successfully.", file=sys.stderr) # DEBUG

        # --- Restart the worker queue processor thread ---
        print("Restarting worker queue processor thread...", file=sys.stderr) # DEBUG
        self.worker_processor_thread = threading.Thread(target=self._process_worker_queue, name="WorkerQueueProcessorThread", daemon=True)
        self.worker_processor_thread.start()
        if not self.worker_processor_thread.is_alive():
            print("ERROR: Failed to restart worker queue processor thread.", file=sys.stderr) # DEBUG
            message_emacs("[Emigo Error] Failed to restart worker queue processor thread.")
            # Stop the worker again if the processor fails
            self._stop_llm_worker()
            self.active_interaction_session = None
            return False # Indicate failure
        print("Worker queue processor thread restarted.", file=sys.stderr) # DEBUG
        # --- End restart queue processor ---

        # --- Post-Cancellation State Updates ---
        # Get the context object
        context = self.sessions.get(session_path)

        # Remove the last user message (the cancelled prompt) from history via Context
        if context:
            # History is stored as (timestamp, message_dict)
            history = context.get_history() # Get current history
            if history:
                last_timestamp, last_message = history[-1]
                if last_message.get("role") == "user":
                    print(f"Removing cancelled user prompt from history for {session_path}", file=sys.stderr) # DEBUG
                    # Create a new list excluding the last message
                    new_history_dicts = [msg for ts, msg in history[:-1]]
                    context.set_history(new_history_dicts) # Update history
                else:
                    print(f"Warning: Last message in history for cancelled context {session_path} was not from user.", file=sys.stderr) # DEBUG
            else:
                 print(f"DEBUG: History for context {session_path} was empty, nothing to remove.", file=sys.stderr)

            # Invalidate the cache for the cancelled context to ensure fresh context next time
            print(f"Invalidating cache for cancelled context: {session_path}", file=sys.stderr) # DEBUG
            context.invalidate_cache()
        else:
            print(f"Warning: Could not find context {session_path} to update history or invalidate cache after cancellation.", file=sys.stderr) # DEBUG

        # Clear active context state *after* all operations
        print(f"DEBUG: Clearing active interaction context flag (was {self.active_interaction_session}).", file=sys.stderr)
        self.active_interaction_session = None

        # Notify Emacs buffer
        eval_in_emacs("emigo--flush-buffer", session_path, "\n[Interaction cancelled by user.]\n", "warning")
        return True # Indicate success

    def cleanup(self):
        """Do some cleanup before exit python process."""
        print("Running Emigo cleanup...", file=sys.stderr) # DEBUG
        self._stop_llm_worker()
        close_epc_client()
        print("Emigo cleanup finished.", file=sys.stderr) # DEBUG

    def clear_history(self, session_path: str) -> bool:
        """EPC: Clear the chat history for the given context path."""
        print(f"Clearing history for context: {session_path}", file=sys.stderr) # DEBUG
        context = self._get_or_create_context(session_path)
        if context:
            context.clear_history()
            # Also clear local buffer via Emacs side
            eval_in_emacs("emigo--clear-local-buffer", context.session_path)
            message_emacs(f"Cleared history for context: {context.session_path}")
            return True
        else:
            message_emacs(f"No context found to clear history for: {session_path}")
            return False


if __name__ == "__main__":
    print("emigo.py starting execution...", file=sys.stderr, flush=True) # DEBUG + flush
    if len(sys.argv) < 2:
        print("ERROR: Missing EPC server port argument.", file=sys.stderr, flush=True) # DEBUG + flush
        sys.exit(1)
    try:
        print("Initializing Emigo class...", file=sys.stderr, flush=True) # DEBUG + flush
        emigo = Emigo(sys.argv[1:])
        print("Emigo class initialized.", file=sys.stderr, flush=True) # DEBUG + flush

        # Keep the main thread alive. Instead of joining the server thread (which might exit),
        # just wait indefinitely or until interrupted.
        print("Main thread entering wait loop (Ctrl+C to exit)...", file=sys.stderr, flush=True) # DEBUG + flush
        while True:
            # Check if the EPC server thread is still alive periodically
            if not emigo.server_thread.is_alive():
                 print("ERROR: Python EPC server thread has died. Exiting.", file=sys.stderr, flush=True)
                 break # Exit the loop if server thread dies
            # Check if worker process is alive (optional, might restart automatically)
            # with emigo.llm_worker_lock:
            #     if emigo.llm_worker_process and emigo.llm_worker_process.poll() is not None:
            #         print("Warning: LLM worker process seems to have died.", file=sys.stderr, flush=True)
            #         # Consider attempting restart here or letting _send_to_worker handle it
            time.sleep(5) # Check every 5 seconds

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received, cleaning up...", file=sys.stderr, flush=True) # DEBUG + flush
        if 'emigo' in locals() and emigo:
            emigo.cleanup()
    except Exception as e:
        print(f"\nFATAL ERROR in main execution block: {e}", file=sys.stderr, flush=True) # DEBUG + flush
        print(traceback.format_exc(), file=sys.stderr, flush=True) # DEBUG + flush
        # Attempt cleanup even on fatal error
        if 'emigo' in locals() and emigo:
            try:
                emigo.cleanup()
            except Exception as cleanup_err:
                print(f"Error during cleanup: {cleanup_err}", file=sys.stderr, flush=True) # DEBUG + flush
                sys.exit(1) # Exit with error code
    finally:
        print("emigo.py main execution finished.", file=sys.stderr, flush=True) # DEBUG + flush
