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

        # Init state
        self.sessions: Dict[str, Context] = {}
        self.worker_process: Optional[subprocess.Popen] = None
        self.worker_reader_thread: Optional[threading.Thread] = None
        self.worker_stderr_thread: Optional[threading.Thread] = None
        self.worker_lock = threading.Lock()
        self.worker_output_queue = queue.Queue() # Messages from worker stdout
        self.active_interaction_session: Optional[str] = None

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

        # --- Worker Process Management ---
        self._start_worker()
        if not self.worker_process or self.worker_process.poll() is not None:
            print("ERROR: Worker process failed to start or exited immediately.", file=sys.stderr, flush=True)
            # Attempt to read stderr if process object exists
            if self.worker_process and self.worker_process.stderr:
                try:
                    stderr_output = self.worker_process.stderr.read()
                    print(f"Worker stderr upon exit:\n{stderr_output}", file=sys.stderr, flush=True)
                except Exception as read_err:
                    print(f"Error reading worker stderr after exit: {read_err}", file=sys.stderr, flush=True)
            sys.exit(1) # Exit if worker failed

        self.worker_processor_thread = threading.Thread(target=self._process_worker_queue, name="WorkerQueueProcessorThread", daemon=True)
        self.worker_processor_thread.start()
        if not self.worker_processor_thread.is_alive():
            print("ERROR: Worker queue processor thread failed to start.", file=sys.stderr, flush=True)
            self._stop_worker() # Attempt cleanup
            sys.exit(1)

        # --- Finalize Setup ---
        try:
            python_epc_port = self.server.server_address[1]
            eval_in_emacs('emigo--first-start', python_epc_port)
            print(f"Sent emigo--first-start signal for port {python_epc_port}", file=sys.stderr, flush=True)
        except Exception as e:
            # This might happen if Emacs EPC server isn't ready yet or the connection failed earlier.
            print(f"WARNING: Failed sending emigo--first-start signal to Elisp: {e}", file=sys.stderr, flush=True)

        print("Emigo initialization complete.", file=sys.stderr, flush=True)

    def _start_worker(self):
        """Starts the worker.py subprocess."""
        with self.worker_lock:
            if self.worker_process and self.worker_process.poll() is None:
                print("Worker process already running.", file=sys.stderr)
                return # Already running

            worker_script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "worker.py"))
            python_executable = sys.executable

            try:
                print(f"Starting Worker process: {python_executable} {worker_script_path}", file=sys.stderr, flush=True)
                self.worker_process = subprocess.Popen(
                    [python_executable, worker_script_path],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, # Capture stderr
                    text=True, # Work with text streams
                    encoding='utf-8', # Ensure UTF-8 for JSON
                    bufsize=0, # Use 0 for unbuffered binary mode (stdin/stdout)
                    # bufsize=1, # Use 1 for line buffered text mode
                    cwd=os.path.dirname(worker_script_path) # Set CWD to script's directory
                )
                # Brief pause to allow immediate exit detection
                time.sleep(0.2)
                if self.worker_process.poll() is not None:
                    exit_code = self.worker_process.poll()
                    print(f"ERROR: Worker process exited immediately with code {exit_code}.", file=sys.stderr, flush=True)
                    try:
                        stderr_output = self.worker_process.stderr.read() if self.worker_process.stderr else "N/A"
                        print(f"Worker stderr upon exit:\n{stderr_output}", file=sys.stderr, flush=True)
                    except Exception as read_err:
                        print(f"Error reading worker stderr after exit: {read_err}", file=sys.stderr, flush=True)
                    self.worker_process = None
                    message_emacs(f"Error: Worker process failed to start (exit code {exit_code}).")
                    return

                print(f"Worker started (PID: {self.worker_process.pid}).", file=sys.stderr, flush=True)

                # Start reader threads
                self.worker_reader_thread = threading.Thread(target=self._read_worker_stdout, name="WorkerStdoutReader", daemon=True)
                self.worker_reader_thread.start()

                self.worker_stderr_thread = threading.Thread(target=self._read_worker_stderr, name="WorkerStderrReader", daemon=True)
                self.worker_stderr_thread.start()

                if not self.worker_reader_thread.is_alive() or not self.worker_stderr_thread.is_alive():
                    print("ERROR: Worker reader thread(s) failed to start.", file=sys.stderr, flush=True)
                    if self.worker_process and self.worker_process.poll() is None:
                        self.worker_process.terminate()
                    self.worker_process = None
                    return

            except Exception as e:
                print(f"ERROR: Failed to start Worker: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
                self.worker_process = None
                message_emacs(f"Error: Failed to start Worker subprocess: {e}")

    def _stop_worker(self):
        """Stops the Worker subprocess and associated threads."""
        with self.worker_lock:
            proc = self.worker_process
            if proc and proc.poll() is None: # Check if running
                try:
                    if proc.stdin:
                        proc.stdin.close() # Signal worker
                except OSError: pass # Ignore if already closed
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    print("Worker did not terminate gracefully, killing.", file=sys.stderr)
                    proc.kill()
                except Exception as e:
                    print(f"Error during worker termination: {e}", file=sys.stderr)
                finally:
                    self.worker_process = None # Mark as stopped regardless of errors

            # Stop queue processor thread
            q_thread = getattr(self, 'worker_processor_thread', None)
            if q_thread and q_thread.is_alive():
                try:
                    self.worker_output_queue.put(None) # Signal loop to exit
                    q_thread.join(timeout=1) # Shorter timeout
                    if q_thread.is_alive():
                        print("Warning: Worker queue processor thread did not exit cleanly.", file=sys.stderr)
                except Exception as e:
                    print(f"Error stopping queue processor thread: {e}", file=sys.stderr)
                finally:
                    self.worker_processor_thread = None

    def _read_worker_stdout(self):
        """Reads stdout lines from the worker and puts them in a queue."""
        # Use a loop that checks if the process is alive
        proc = self.worker_process # Local reference
        if proc and proc.stdout:
            try:
                for line in iter(proc.stdout.readline, ''):
                    if line:
                        self.worker_output_queue.put(line.strip())
                    else: # Empty string indicates EOF
                        break
            except ValueError: # I/O operation on closed file
                pass
            except Exception as e:
                print(f"Error reading Worker stdout: {e}", file=sys.stderr)
            finally:
                # Ensure the sentinel is always put to signal the processor thread
                self.worker_output_queue.put(None)
        else:
            # Still signal end if the thread was started but process died quickly
            self.worker_output_queue.put(None)

    def _read_worker_stderr(self):
        """Reads and prints stderr lines from the worker."""
        # Use a loop that checks if the process is alive
        proc = self.worker_process # Local reference
        if proc and proc.stderr:
            try:
                for line in iter(proc.stderr.readline, ''):
                    if line:
                        # Print worker errors clearly marked
                        # Print worker errors clearly marked, keep this logging
                        print(f"[WORKER_STDERR] {line.strip()}", file=sys.stderr, flush=True)
                    else: # Empty string indicates EOF
                        break
            except ValueError: # I/O operation on closed file
                pass
            except Exception as e:
                print(f"Error reading Worker stderr: {e}", file=sys.stderr)

    def _send_to_worker(self, data: Dict):
        """Sends a JSON message to the worker's stdin."""
        with self.worker_lock:
            session = data.get("session_path", "unknown") # Get session path if available
            if not self.worker_process or self.worker_process.poll() is not None:
                print("Worker process not running. Attempting restart...", file=sys.stderr)
                self._start_worker() # Try restarting
                # Check again after attempting restart
                if not self.worker_process or self.worker_process.poll() is not None:
                    print("Worker restart failed. Cannot send message.", file=sys.stderr)
                    eval_in_emacs("emigo--flush-buffer", session, "[Error: Worker process is not running and restart failed]", "error")
                    return

            # Proceed if worker is running and stdin seems available
            if self.worker_process and self.worker_process.stdin:
                try:
                    json_str = json.dumps(data) + '\n'
                    self.worker_process.stdin.write(json_str)
                    self.worker_process.stdin.flush()
                except (OSError, BrokenPipeError, ValueError) as e: # Catches pipe errors and closed file errors
                    print(f"Error sending to Worker (Pipe likely closed): {e}", file=sys.stderr)
                    self._stop_worker() # Attempt cleanup
                    eval_in_emacs("emigo--flush-buffer", session, f"[Error: Failed to send message to worker ({e})]", "error")
                except Exception as e:
                    print(f"Unexpected error sending to Worker: {e}", file=sys.stderr)
                    eval_in_emacs("emigo--flush-buffer", session, f"[Error: Unexpected error sending message to worker ({e})]", "error")
            else:
                 # This case should ideally be caught by the initial check/restart logic
                 print("Cannot send to worker, process or stdin unavailable.", file=sys.stderr)
                 eval_in_emacs("emigo--flush-buffer", session, "[Error: Cannot write to Worker process]", "error")


    def _process_worker_queue(self):
        """Processes messages received from the worker via the queue."""
        while True:
            line = self.worker_output_queue.get()
            if line is None: # Sentinel value received
                # print("Worker output queue processing stopped.", file=sys.stderr) # Optional debug
                break

            try:
                message = json.loads(line)
                msg_type = message.get("type")
                session_path = message.get("session")

                if not session_path:
                    print(f"Worker message missing session path: {line}", file=sys.stderr)
                    continue


                if msg_type == "stream":
                    role = message.get("role", "llm")
                    content = message.get("content", "")
                    # Filter context unless it's specific data like tool args (though tools removed)
                    filtered_content = _filter_context(content) if role != "tool_json_args" else content
                    if filtered_content: # Only flush if there's content
                        eval_in_emacs("emigo--flush-buffer", session_path, filtered_content, role)

                elif msg_type == "finished":
                    status = message.get("status", "unknown")
                    finish_message = message.get("message", "")

                    # Clear active session flag *before* signaling Emacs
                    if self.active_interaction_session == session_path:
                        self.active_interaction_session = None

                    # Update history only on success
                    if status == "success":
                        original_user_prompt = message.get("original_user_prompt")
                        assistant_response = message.get("assistant_response")
                        if original_user_prompt and assistant_response:
                            context = self.sessions.get(session_path) # Use get to avoid auto-creation here
                            if context:
                                context.add_interaction_to_history(original_user_prompt, assistant_response)
                            else:
                                print(f"Warning: Context {session_path} not found to add interaction to history.", file=sys.stderr)
                        else:
                            print(f"Warning: Worker finished successfully but missing prompt/response data for {session_path}.", file=sys.stderr)

                    # Signal Emacs agent is finished
                    eval_in_emacs("emigo--agent-finished", session_path)

                elif msg_type == "error":
                    error_msg = message.get("message", "Unknown error from worker")
                    print(f"Error from worker ({session_path}): {error_msg}", file=sys.stderr) # Keep this error log
                    eval_in_emacs("emigo--flush-buffer", session_path, f"[Worker Error: {error_msg}]", "error")
                    # Clear active session flag on error
                    if self.active_interaction_session == session_path:
                        self.active_interaction_session = None

                elif msg_type == "pong":
                    pass # No action needed for pong currently

                # Handle other message types if needed

            except json.JSONDecodeError:
                print(f"Received invalid JSON from worker: {line}", file=sys.stderr)
            except Exception as e:
                print(f"Error processing worker message: {e}\n{traceback.format_exc()}", file=sys.stderr)

    # --- Context Management ---

    def _get_or_create_context(self, session_path: str) -> Optional[Context]:
        """Gets the Context object for a path, creating it if necessary."""
        if not os.path.isdir(session_path):
            print(f"ERROR: Invalid context path (not a directory): {session_path}", file=sys.stderr)
            # Maybe notify Emacs here?
            # Maybe notify Emacs here? - Handled by caller usually
            # eval_in_emacs("message", f"[Emigo Error] Invalid context path: {session_path}")
            return None

        if session_path not in self.sessions:
            # TODO: Get verbose setting properly if needed, defaulting to False for now
            config_verbose = True
            self.sessions[session_path] = Context(session_path=session_path, verbose=config_verbose)
        return self.sessions[session_path]

    # --- EPC Methods Called by Emacs ---

    def get_history(self, session_path: str) -> List[Tuple[float, Dict]]:
        """EPC: Retrieves the chat history via the Context object."""
        context = self.sessions.get(session_path) # Use get, don't create here
        if not context:
            # message_emacs(f"Error: No context found for {session_path} to get history") # Let Emacs side handle missing context if needed
            return []
        return context.get_history()

    def add_file_to_context(self, session_path: str, filename: str) -> bool:
        """EPC: Adds a file via the Context object."""
        context = self._get_or_create_context(session_path) # Create if needed for adding files
        if not context:
            # message_emacs(f"Error: Could not establish context for {session_path}") # Context class should message
            return False
        success, _ = context.add_file_to_context(filename) # Context class handles messaging
        return success

    def remove_file_from_context(self, session_path: str, filename: str) -> bool:
        """EPC: Removes a file via the Context object."""
        context = self.sessions.get(session_path) # Use get, don't create if removing
        if not context:
            # message_emacs(f"Error: No context found for {session_path}") # Context class should message
            return False
        success, _ = context.remove_file_from_context(filename) # Context class handles messaging
        return success

    def get_chat_files(self, session_path: str) -> List[str]:
        """EPC: Retrieves the list of chat files via the Context object."""
        context = self.sessions.get(session_path) # Use get, don't create
        if not context:
            # message_emacs(f"Error: No context found for {session_path}")
            return []
        return context.get_chat_files()

    def emigo_send(self, session_path: str, prompt: str):
        """
        EPC: Handles a user prompt to initiate an LLM interaction.

        Gets the session, generates the context string (which also handles @file mentions),
        retrieves necessary config, prepares history, and sends the interaction
        request to the worker.

        Args:
            session_path: The path identifying the session.
            prompt: The user's input prompt.
        """
        # --- Pre-Interaction Checks ---
        if self.active_interaction_session:
            active_session = self.active_interaction_session # Cache locally
            try:
                confirm_cancel = get_emacs_func_result("yes-or-no-p",
                                                       f"LLM is busy with '{active_session}'. Stop it and run new prompt for '{session_path}'?")
                if confirm_cancel:
                    if not self.cancel_llm_interaction(active_session):
                        message_emacs("[Emigo Error] Failed to cancel previous interaction.")
                        return # Stop if cancellation failed
                    # Proceed after successful cancellation
                else:
                    eval_in_emacs("message", f"[Emigo] LLM busy with {active_session}. New prompt ignored.")
                    return # Stop processing the new request
            except Exception as e:
                print(f"Error during confirmation/cancellation: {e}", file=sys.stderr)
                message_emacs(f"[Emigo Error] Failed asking for cancellation: {e}")
                return

        # --- Prepare Interaction ---
        # Mark the *new* session as active *before* potentially failing operations.
        self.active_interaction_session = session_path

        context = self._get_or_create_context(session_path)
        if not context:
            eval_in_emacs("emigo--flush-buffer", f"invalid-context-{session_path}", f"[Error: Invalid context path '{session_path}']", "error")
            self.active_interaction_session = None # Clear flag on error
            return

        # --- History & Context Handling ---
        # Flush user prompt to Emacs buffer for display
        eval_in_emacs("emigo--flush-buffer", context.session_path, f"\n\nUser:\n{prompt}\n", "user")
        # History is appended by worker response handler upon success

        user_prompt_dict = {"role": "user", "content": prompt}

        # Generate context string (handles @file mentions internally)
        context_str = context.generate_context_string(current_prompt=prompt)

        # --- Prepare data for worker ---
        # Get TRUNCATED history dicts including the pending prompt for calculation
        # TODO: Make these configurable via Emacs vars
        max_history_tokens = 8000
        min_history_messages = 3
        truncated_history_dicts = context.get_truncated_history_dicts(
            max_history_tokens, min_history_messages, include_pending_user_prompt=user_prompt_dict
        )

        # Get model config from Emacs vars
        try:
            vars_result = get_emacs_vars(["emigo-model", "emigo-base-url", "emigo-api-key"])
            if not vars_result or len(vars_result) < 3 or not vars_result[0] or '/' not in vars_result[0]:
                model_val = vars_result[0] if vars_result else "None"
                error_msg = f"Invalid or missing emigo-model: '{model_val}'. Expected 'provider/model_name'."
                raise ValueError(error_msg)
            model, base_url, api_key = vars_result
        except Exception as e:
            print(f"ERROR retrieving Emacs config: {e}", file=sys.stderr)
            message_emacs(f"[Emigo Error] Config error: {e}")
            self.active_interaction_session = None # Unset active context
            return

        worker_config = {
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "verbose": context.verbose # Pass context's verbosity setting
        }

        request_data = {
            "session_path": context.session_path,
            "user_prompt": user_prompt_dict,
            "history": truncated_history_dicts,
            "config": worker_config,
            "chat_files": context.get_chat_files(), # Get current chat files
            "context": context_str,
        }

        # --- Print prompt components before sending to worker ---
        print("--- Prompt Components Sent to Worker ---", file=sys.stderr, flush=True)
        print(f"Session: {context.session_path}", file=sys.stderr, flush=True)
        print(f"History ({len(truncated_history_dicts)} messages):", file=sys.stderr, flush=True)
        for msg in truncated_history_dicts:
            print(f"  {msg.get('role', 'unknown')}: {msg.get('content', '')[:100]}...", file=sys.stderr, flush=True) # Print truncated content
        print("Context String:", file=sys.stderr, flush=True)
        print(context_str, file=sys.stderr, flush=True)
        print("User Prompt:", file=sys.stderr, flush=True)
        print(user_prompt_dict, file=sys.stderr, flush=True)
        print("--- End Prompt Components ---", file=sys.stderr, flush=True)

        # --- Send request to worker ---
        self._send_to_worker({
            "type": "interaction_request",
            "session_path": context.session_path, # Include session path at top level too
            "data": request_data
        })
        # Response handling happens asynchronously in _process_worker_queue

    def set_history_and_send(self, session_path: str, new_history_list: List[Dict], user_prompt_dict: Dict):
        """
        EPC: Sets the session history and then sends the provided user prompt.
        Used after editing history in Emacs.

        Args:
            session_path: The path identifying the session.
            new_history_list: The list of message dictionaries to set as the new history.
            user_prompt_dict: The dictionary representing the final user message (the prompt).
        """

        # Attempt conversion from potential Elisp alists
        converted_history = _try_convert_alist_to_dict(new_history_list)
        converted_prompt = _try_convert_alist_to_dict(user_prompt_dict)

        # Basic validation after conversion
        if not isinstance(converted_history, list) or not all(isinstance(item, dict) for item in converted_history):
            message_emacs(f"[Emigo Error] Invalid history format received.")
            return
        if not isinstance(converted_prompt, dict) or converted_prompt.get("role") != "user":
            message_emacs(f"[Emigo Error] Invalid user prompt format received.")
            return

        context = self._get_or_create_context(session_path)
        if not context:
            # Error message handled by _get_or_create_context or caller
            return

        # Set the history in the context object
        context.set_history(converted_history)

        # Extract the prompt string
        prompt_string = converted_prompt.get("content", "")
        if not prompt_string:
             message_emacs("[Emigo Error] User prompt content is empty after history edit.")
             return

        # Call the standard emigo_send method
        self.emigo_send(session_path, prompt_string)


    def cancel_llm_interaction(self, session_path: str) -> bool:
        """
        Cancels the current LLM interaction by killing and restarting the worker.
        Also clears the active session flag and invalidates the session cache.

        Returns:
            bool: True if cancellation (including worker restart) was successful, False otherwise.
        """
        if self.active_interaction_session != session_path:
            # message_emacs(f"No active interaction found for session {session_path} to cancel.") # Optional
            return False # No active interaction for this session to cancel

        self._stop_worker() # Stops process and queue processor thread

        # Drain any remaining messages from the *old* worker run
        drained_count = 0
        while not self.worker_output_queue.empty():
            try:
                stale_msg = self.worker_output_queue.get_nowait()
                if stale_msg is not None: # Ignore potential sentinels
                    drained_count += 1
                self.worker_output_queue.task_done()
            except queue.Empty:
                break
            except Exception as e:
                print(f"Error draining queue during cancel: {e}", file=sys.stderr)
                break # Stop draining on error

        # Restart the worker process and its reader threads
        self._start_worker()

        # Check if worker restart was successful
        worker_restarted_ok = False
        with self.worker_lock: # Re-acquire lock to check process status
            if self.worker_process and self.worker_process.poll() is None:
                worker_restarted_ok = True

        if not worker_restarted_ok:
            print("ERROR: Failed to restart Worker after cancellation.", file=sys.stderr)
            message_emacs("[Emigo Error] Failed to restart Worker after cancellation.")
            self.active_interaction_session = None # Clear flag even on failure
            return False


        # Restart the worker queue processor thread
        self.worker_processor_thread = threading.Thread(target=self._process_worker_queue, name="WorkerQueueProcessorThread", daemon=True)
        self.worker_processor_thread.start()
        if not self.worker_processor_thread.is_alive():
            print("ERROR: Failed to restart worker queue processor thread.", file=sys.stderr)
            message_emacs("[Emigo Error] Failed to restart worker queue processor thread.")
            self._stop_worker() # Stop worker again if processor fails
            self.active_interaction_session = None
            return False

        # --- Post-Cancellation State Updates ---
        context = self.sessions.get(session_path)
        if context:
            context.invalidate_cache()

        # Clear active context state *after* successful restart
        self.active_interaction_session = None

        # Notify Emacs buffer
        eval_in_emacs("emigo--flush-buffer", session_path, "\n[Interaction cancelled by user.]\n", "warning")
        eval_in_emacs("emigo--agent-finished", session_path) # Also signal finished state
        return True

    def cleanup(self):
        """Clean up resources before exiting."""
        print("Running Emigo cleanup...", file=sys.stderr)
        self._stop_worker()
        close_epc_client()
        # No need to explicitly stop the EPC server thread if it's a daemon
        print("Emigo cleanup finished.", file=sys.stderr)

    def clear_history(self, session_path: str) -> bool:
        """EPC: Clear the chat history for the given session path."""
        context = self.sessions.get(session_path) # Use get, don't create
        if context:
            context.clear_history()
            # Let Emacs side handle buffer clearing and user messages
            # eval_in_emacs("emigo--clear-local-buffer", context.session_path) # Emacs should call this
            # message_emacs(f"Cleared history for context: {context.session_path}") # Emacs should show this
            return True
        else:
            # message_emacs(f"No context found to clear history for: {session_path}") # Emacs side can report this
            return False

# --- Helper Functions ---

def _try_convert_alist_to_dict(data):
    """Attempts to convert Elisp alist representation(s) to Python dict(s).

    Handles:
    - Single alist: [[':key1', Symbol('.'), 'val1'], [':key2', Symbol('.'), 'val2']] -> {'key1': 'val1', 'key2': 'val2'}
    - List of alists: [[[':k1', Symbol('.'), 'v1']], [[':k2', Symbol('.'), 'v2']]] -> [{'k1': 'v1'}, {'k2': 'v2'}]
    - Already correct dicts/lists: Passes them through unchanged.
    """
    if isinstance(data, list):
        # Check if it's a list of alist representations (list of lists of lists)
        # Heuristic: Check if the first element looks like an alist representation
        if data and isinstance(data[0], list) and data[0] and isinstance(data[0][0], list) and len(data[0][0]) == 3:
             # It's likely a list of alists, convert each one
             return [_try_convert_alist_to_dict(item) for item in data]
        # Check if it's a single alist representation (list of lists)
        # Heuristic: Check if the first element looks like an alist pair [':key', dot, val]
        elif data and isinstance(data[0], list) and len(data[0]) == 3 and isinstance(data[0][0], str) and data[0][0].startswith(':'):
             # It's likely a single alist, convert it to a dict
             result_dict = {}
             for item in data:
                 # item is like [':key', Symbol('.'), 'value'] or similar
                 if isinstance(item, list) and len(item) == 3 and isinstance(item[0], str) and item[0].startswith(':'):
                     key = item[0][1:] # Remove the leading ':'
                     value = item[2]
                     # Recursively convert value if it's also an alist/list of alists
                     result_dict[key] = _try_convert_alist_to_dict(value)
                 else:
                     # Unexpected format within the supposed alist, return original data
                     # print(f"Warning: Unexpected item format in potential alist: {item}. Returning original data.", file=sys.stderr) # Removed warning
                     return data # Return original list on format error
             return result_dict
        else:
            # Not an alist or list of alists we recognize, could be a simple list.
            # Return list with elements potentially converted.
            return [_try_convert_alist_to_dict(item) for item in data]
    # If it's not a list, return it as is (e.g., already a dict, string, number, etc.)
    return data


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("ERROR: Missing Elisp EPC server port argument.", file=sys.stderr, flush=True)
        sys.exit(1)

    emigo_instance = None # Define outside try block for cleanup access
    exit_code = 1 # Default exit code in case of early failure
    try:
        emigo_instance = Emigo(sys.argv[1:])

        # Keep the main thread alive. The EPC server runs in a daemon thread.
        # We just need to wait for an interruption signal (like Ctrl+C).
        while True:
            # Check if the EPC server thread is still alive periodically
            if not emigo_instance.server_thread.is_alive():
                 print("ERROR: Python EPC server thread has died. Exiting.", file=sys.stderr, flush=True)
                 break # Exit the loop if server thread dies
            time.sleep(5) # Reduce CPU usage

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received, cleaning up...", file=sys.stderr, flush=True)
        # Cleanup handled in finally block
    except Exception as e:
        print(f"\nFATAL ERROR in main execution block: {e}", file=sys.stderr, flush=True)
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        # Cleanup handled in finally block
        exit_code = 1
    else:
        exit_code = 0 # Normal exit if loop broken by server thread death
    finally:
        if emigo_instance:
            try:
                emigo_instance.cleanup()
            except Exception as cleanup_err:
                print(f"Error during cleanup: {cleanup_err}", file=sys.stderr, flush=True)
                exit_code = 1 # Ensure error exit code if cleanup fails
        print("emigo.py main execution finished.", file=sys.stderr, flush=True)
        sys.exit(exit_code)
