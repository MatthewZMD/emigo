# -*- coding: utf-8 -*-

"""
Manages the state and context generation for a single Emigo chat session.

This includes chat history, files added to the context, file caching,
repository mapping, and generating the <context> string for the LLM.
"""

import os
import sys
import time
import re
import traceback
import tiktoken
from typing import List, Dict, Optional, Tuple

from repomapper import RepoMapper
from utils import (
    eval_in_emacs, read_file_content, message_emacs
)


class Context:
    """Encapsulates the state and context generation for a single Emigo session."""

    def __init__(self, session_path: str, verbose: bool = False):
        """
        Initializes the Context for a given session path.

        Args:
            session_path: The absolute path to the root directory of the session.
            verbose: Flag for enabling verbose output.
        """
        self.session_path = session_path
        self.verbose = verbose
        self.history: List[Tuple[float, Dict]] = [] # List of (timestamp, message_dict)
        self.chat_files: List[str] = [] # List of relative file paths
        self.caches: Dict[str, any] = {'mtimes': {}, 'contents': {}}
        self.repo_mapper = RepoMapper(root_dir=self.session_path, verbose=self.verbose)

        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            print(f"Warning: Could not initialize tokenizer. Token counts may be inaccurate. Error: {e}", file=sys.stderr)
            self.tokenizer = None # Handle cases where tiktoken might fail

        print(f"Initialized Context for path: {self.session_path}", file=sys.stderr)

    # --- Token Counting (Moved from worker.py) ---
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text using the context's tokenizer or fallback."""
        if not text:
            return 0

        if self.tokenizer:
            try:
                return len(self.tokenizer.encode(text))
            except Exception as e:
                print(f"Token counting error, using fallback: {e}", file=sys.stderr)
        # Fallback: approximate tokens as 4 chars per token
        return max(1, len(text) // 4)

    # --- History Management ---

    def get_history(self) -> List[Tuple[float, Dict]]:
        """Returns a copy of the chat history for this session."""
        return list(self.history)

    def append_history(self, message: Dict):
        """Appends a message with a timestamp to the history, filtering content."""
        if not isinstance(message, dict) or "role" not in message or "content" not in message:
            print(f"Warning: Attempted to add invalid message to history: {message}", file=sys.stderr)
            return
        # Filter content before appending
        filtered_message = dict(message) # Create a copy
        # Ensure content is string before filtering
        if not isinstance(filtered_message.get("content"), str):
             filtered_message["content"] = str(filtered_message.get("content", ""))
        # No filtering here, filtering happens when sending/displaying if needed
        self.history.append((time.time(), message))

    def clear_history(self):
        """Clears the chat history for this session."""
        self.history = []
        print(f"Cleared history for context: {self.session_path}", file=sys.stderr)

    def set_history(self, history_dicts: List[Dict]):
        """Replaces the current history with the provided list of message dictionaries."""
        new_history_tuples = []
        current_time = time.time() # Use a consistent timestamp for the batch
        for i, msg_dict in enumerate(history_dicts):
            if isinstance(msg_dict, dict) and "role" in msg_dict and "content" in msg_dict:
                # Add with a slightly incrementing timestamp to maintain order if needed
                new_history_tuples.append((current_time + i * 0.000001, msg_dict))
            else:
                print(f"Warning: Skipping invalid message dict during set_history: {msg_dict}", file=sys.stderr)
        self.history = new_history_tuples # Directly replace the history list
        print(f"Set history for context {self.session_path} with {len(self.history)} messages.", file=sys.stderr)

    def add_interaction_to_history(self, user_message: Dict, assistant_message: Dict):
        """Appends both the user and assistant messages from a completed interaction."""
        self.append_history(user_message)
        self.append_history(assistant_message)
        print(f"Added interaction to history for {self.session_path}. New length: {len(self.history)}", file=sys.stderr)

    def _truncate_history(self, history_tuples: List[Tuple[float, Dict]], max_tokens: int, min_messages: int) -> List[Tuple[float, Dict]]:
        """
        Truncate a list of history tuples to fit token limits.
        """
        original_length = len(history_tuples)
        if not history_tuples:
            return []

        # Always keep the first message (system prompt or initial user message) if history exists
        truncated_tuples = [history_tuples[0]] if history_tuples else []
        current_tokens = self._count_tokens(truncated_tuples[0][1].get("content", "")) if truncated_tuples else 0

        # Iterate through the rest of the history tuples from newest to oldest
        for hist_tuple in reversed(history_tuples[1:]):
            msg_content = hist_tuple[1].get("content", "")
            msg_tokens = self._count_tokens(msg_content)

            # Check if adding this message exceeds the token limit
            if current_tokens + msg_tokens > max_tokens:
                # If we already have the minimum required messages, stop here
                if len(truncated_tuples) >= min_messages:
                    break
                # Otherwise, we must add it, even if it exceeds the limit slightly
                # If we're below min messages, keep going but warn
                print("Warning: History exceeds token limit but below min message count", file=sys.stderr)

            truncated_tuples.insert(1, hist_tuple)  # Insert tuple after first message
            current_tokens += msg_tokens

        # If truncation occurred, update self.history
        if len(truncated_tuples) < original_length:
             print(f"History truncated from {original_length} to {len(truncated_tuples)} messages ({current_tokens} tokens). Updating context history.", file=sys.stderr)
             self.history = truncated_tuples

        # Return the list of truncated message tuples
        return truncated_tuples

    def get_truncated_history_dicts(self, max_tokens: int, min_messages: int, include_pending_user_prompt: Optional[Dict] = None) -> List[Dict]:
        """
        Returns a truncated list of message *dictionaries* based on current history
        and an optional pending user prompt (used for calculation but not saved here).

        Args:
            max_tokens: The maximum number of tokens allowed for the history.
            min_messages: The minimum number of messages to keep (including the first).
            include_pending_user_prompt: A user prompt dict to temporarily include
                                         for the truncation calculation.

        Returns:
            A list of message dictionaries representing the truncated history to be sent.
        """
        # Start with a copy of the current history tuples
        history_with_pending = list(self.history)

        # Temporarily add the pending user prompt for calculation if provided
        pending_tuple = None
        if include_pending_user_prompt:
            pending_tuple = (time.time(), include_pending_user_prompt)
            history_with_pending.append(pending_tuple)

        # Call the internal truncation method on the list *including* the pending prompt.
        # This call will now modify self.history if truncation occurs based on the combined length.
        truncated_tuples_with_pending = self._truncate_history(history_with_pending, max_tokens, min_messages)

        # --- Prepare the list of dictionaries to return ---
        # Extract dictionaries from the *returned* truncated list (which might include the pending prompt)
        truncated_dicts_to_send = [msg_dict for _, msg_dict in truncated_tuples_with_pending]

        # Check if the pending prompt survived truncation (needed for removal below)
        pending_survived = False
        if pending_tuple and truncated_tuples_with_pending and truncated_tuples_with_pending[-1] == pending_tuple:
            pending_survived = True

        # If the pending prompt was included and survived truncation, remove its *dictionary*
        # from the list we return to the caller (emigo.py), as the worker receives it separately.
        if pending_survived and truncated_dicts_to_send and truncated_dicts_to_send[-1] == include_pending_user_prompt:
             truncated_dicts_to_send.pop()

        return truncated_dicts_to_send


    # --- Chat File Management ---

    def get_chat_files(self) -> List[str]:
        """Returns a copy of the list of files currently in the chat context."""
        return list(self.chat_files)

    def add_file_to_context(self, filename: str) -> Tuple[bool, str]:
        """
        Adds a file to the chat context. Ensures it's relative and exists.
        Updates caches and Emacs UI.
        Returns (success: bool, message: str).
        """
        try:
            # Expand user directory
            filename = os.path.expanduser(filename)
            # Ensure filename is relative to session_path for consistency
            # Check if already relative first
            if not os.path.isabs(filename):
                rel_filename = filename
            else:
                # Check if the file is within the session path before making relative
                abs_path_input = os.path.abspath(filename)
                if not abs_path_input.startswith(self.session_path):
                     # Check if it's *outside* but maybe contains the session path (e.g. symlink target)
                     # This logic might need refinement depending on desired symlink behavior.
                     # For now, strictly enforce containment.
                     return False, f"File is outside session directory: {filename}"
                rel_filename = os.path.relpath(abs_path_input, self.session_path)

            # Get the absolute path based on the relative path and session root
            abs_path = os.path.abspath(os.path.join(self.session_path, rel_filename))

            # Final checks
            if not os.path.isfile(abs_path):
                 return False, f"File not found or not a regular file: {rel_filename}"
            # Double-check containment after resolving potential symlinks in join/abspath
            if not abs_path.startswith(self.session_path):
                 return False, f"Resolved file path is outside session directory: {rel_filename}"

            # Add to context if not already present
            if rel_filename not in self.chat_files:
                self.chat_files.append(rel_filename)
                # Read initial content into cache
                self._update_file_cache(rel_filename)
                # Update chat files information in Emacs *after* successful add and cache update
                self._update_chat_files_info_in_emacs()
                return True, f"Added '{rel_filename}' to context."
            else:
                return False, f"File '{rel_filename}' already in context."

        except ValueError as e: # Handles relpath errors (e.g., different drives on Windows)
            return False, f"Cannot add file '{filename}': {e}"
        except Exception as e:
            print(f"Error adding file '{filename}': {e}\n{traceback.format_exc()}", file=sys.stderr)
            return False, f"Error adding file '{filename}': {e}"

    def remove_file_from_context(self, filename: str) -> Tuple[bool, str]:
        """
        Removes a file from the chat context. Cleans up caches and updates Emacs UI.
        Returns (success: bool, message: str).
        """
        # Ensure filename is relative for comparison
        if os.path.isabs(filename):
            try:
                # Check if the file is within the session path before making relative
                abs_path_input = os.path.abspath(filename)
                if not abs_path_input.startswith(self.session_path):
                    # If it's absolute but outside, it couldn't have been added legally.
                    # We still might want to allow removing it if its *relative* form exists.
                    # Check relative path directly.
                    rel_filename_check = os.path.relpath(abs_path_input, self.session_path)
                    if rel_filename_check not in self.chat_files:
                        return False, f"File '{filename}' (relative: '{rel_filename_check}') not found in context."
                    rel_filename = rel_filename_check # Use the relative form found
                else:
                     rel_filename = os.path.relpath(abs_path_input, self.session_path)
            except ValueError: # filename might be on a different drive on Windows
                # If it's on a different drive, it couldn't be in chat_files by relative path.
                # Check if the raw absolute path string was somehow added (shouldn't happen).
                if filename in self.chat_files:
                     rel_filename = filename # Treat the absolute path as the key if found
                else:
                    return False, f"Cannot remove file from different drive/location: {filename}"
        else:
            rel_filename = filename # Assume it's already relative

        if rel_filename in self.chat_files:
            self.chat_files.remove(rel_filename)
            # Clean up cache for the removed file
            self.invalidate_cache(rel_filename) # Use invalidate for cleanup
            # Update chat files information in Emacs *after* successful removal
            self._update_chat_files_info_in_emacs()
            return True, f"Removed '{rel_filename}' from context."
        else:
            return False, f"File '{rel_filename}' not found in context."

    def _update_chat_files_info_in_emacs(self):
        """Calculates token count for chat files and updates Emacs header line."""
        file_number = 0
        tokens = 0
        if not self.tokenizer:
            print("Warning: Tokenizer not available, cannot update token count in Emacs.", file=sys.stderr)
            chat_file_info = f"{len(self.chat_files)} file(s) [token count unavailable]"
        else:
            for rel_path in self.chat_files:
                content = self.get_cached_content(rel_path) # Use cached content
                if content is not None:
                    try:
                        token_count = len(self.tokenizer.encode(content))
                        file_number += 1
                        tokens += token_count
                    except Exception as e:
                        print(f"Error encoding content for token count ({rel_path}): {e}", file=sys.stderr)
                        # Skip token count for this file if encoding fails
                else:
                    # File might have been deleted or is inaccessible
                    print(f"Warning: Could not get content for {rel_path} to count tokens.", file=sys.stderr)

            if file_number != len(self.chat_files):
                 # Indicate if some files couldn't be counted
                 chat_file_info = f"{len(self.chat_files)} file(s) [{tokens} tokens from {file_number} files]"
            elif file_number == 1:
                 chat_file_info = f"1 file [{tokens} tokens]"
            else:
                 chat_file_info = f"{file_number} files [{tokens} tokens]"

        try:
            eval_in_emacs("emigo-update-chat-files-info", self.session_path, chat_file_info)
        except Exception as e:
            print(f"Error calling emigo-update-chat-files-info: {e}", file=sys.stderr)

    # --- File Caching ---

    def _update_file_cache(self, rel_path: str, content: Optional[str] = None) -> bool:
        """Updates the cache (mtime, content) for a given relative file path."""
        abs_path = os.path.abspath(os.path.join(self.session_path, rel_path))
        try:
            # Use RepoMapper's mtime getter if available
            current_mtime = self.repo_mapper.repomap.get_mtime(abs_path) if self.repo_mapper else None
            if current_mtime is None: # File deleted, inaccessible, or RepoMapper failed
                # Fallback to os.path.getmtime if RepoMapper didn't provide it
                if os.path.isfile(abs_path):
                    current_mtime = os.path.getmtime(abs_path)
                else:
                    # File truly gone or inaccessible
                    self.invalidate_cache(rel_path) # Ensure cache is cleared
                    if self.verbose:
                        print(f"File {rel_path} not found or inaccessible, cache cleared.", file=sys.stderr)
                    return False

            # If content is provided (e.g., after write/replace), use it. Otherwise, read.
            if content is None:
                # Read only if mtime changed or not cached
                last_mtime = self.caches['mtimes'].get(rel_path)
                if last_mtime is None or current_mtime != last_mtime or rel_path not in self.caches['contents']:
                    if self.verbose:
                        print(f"Cache miss/stale for {rel_path}, reading file.", file=sys.stderr)
                    content = read_file_content(abs_path) # Reads the file
                    if content is None: # Handle read failure
                         print(f"Error reading file content for {rel_path}", file=sys.stderr)
                         self.invalidate_cache(rel_path)
                         return False
                else:
                    # Content is up-to-date, no need to update cache content again
                    return True # Indicate cache was already fresh

            # Update cache
            self.caches['mtimes'][rel_path] = current_mtime
            self.caches['contents'][rel_path] = content
            return True

        except Exception as e:
            print(f"Error updating cache for '{rel_path}': {e}", file=sys.stderr)
            self.invalidate_cache(rel_path) # Invalidate cache on error
            return False

    def get_cached_content(self, rel_path: str) -> Optional[str]:
        """Gets content from cache, updating if stale or reading if necessary."""
        # Ensure the cache is up-to-date first. _update_file_cache reads if needed.
        if self._update_file_cache(rel_path):
            return self.caches['contents'].get(rel_path)
        # Return None if update failed (e.g., file deleted, read error)
        return None

    def invalidate_cache(self, rel_path: Optional[str] = None):
        """Invalidates cache for a specific file or the entire context."""
        if rel_path:
            if rel_path in self.caches['mtimes']:
                del self.caches['mtimes'][rel_path]
            if rel_path in self.caches['contents']:
                del self.caches['contents'][rel_path]
            if self.verbose:
                print(f"Invalidated cache for {rel_path}", file=sys.stderr)
        else:
            self.caches['mtimes'].clear()
            self.caches['contents'].clear()
            if self.verbose:
                print(f"Invalidated all caches for context {self.session_path}", file=sys.stderr)

    # --- Context String Generation ---

    def _handle_mentions(self, prompt: str) -> List[str]:
        """
        Finds @file mentions in the prompt, adds them to context, and returns
        a list of absolute paths for the successfully processed mentioned files.
        """
        mention_pattern = r'@(\S+)'
        # TODO: Add identifier extraction if needed, e.g., @ident:my_func
        # For now, only handles file mentions.

        mentioned_files_in_prompt = re.findall(mention_pattern, prompt)
        processed_mention_files_abs = [] # Store absolute paths of files successfully added/found

        if mentioned_files_in_prompt:
            if self.verbose: print(f"Found file mentions in prompt: {mentioned_files_in_prompt}", file=sys.stderr)
            added_files_rel = []
            failed_files_info = []
            already_present_files_rel = []

            for file_mention in mentioned_files_in_prompt:
                # Attempt to add the mentioned file
                success, msg = self.add_file_to_context(file_mention)
                # Need the relative path for reporting and the absolute path for get_related_files
                try:
                    # Resolve to absolute path relative to session path
                    abs_path = os.path.abspath(os.path.join(self.session_path, os.path.relpath(os.path.expanduser(file_mention), self.session_path)))
                    rel_path = os.path.relpath(abs_path, self.session_path) # Get canonical relative path

                    if success:
                        added_files_rel.append(rel_path)
                        processed_mention_files_abs.append(abs_path)
                    elif "already in context" in msg:
                        already_present_files_rel.append(rel_path)
                        processed_mention_files_abs.append(abs_path) # Include already present files
                    else: # Failed for other reasons
                        failed_files_info.append(f"{file_mention} ({msg})")
                except ValueError:
                     failed_files_info.append(f"{file_mention} (Invalid path or outside session)")
                except Exception as e:
                     failed_files_info.append(f"{file_mention} (Error: {e})")


            # Report summary to Emacs after processing all mentions
            report_parts = []
            if added_files_rel:
                report_parts.append(f"Auto-added mentioned files: {', '.join(added_files_rel)}")
            if already_present_files_rel:
                 # Optionally report files that were already present
                 # report_parts.append(f"Mentioned files already in context: {', '.join(already_present_files_rel)}")
                 pass # Keep report concise
            if failed_files_info:
                report_parts.append(f"Failed to add mentioned files: {', '.join(failed_files_info)}")

            if report_parts:
                 message_emacs(f"[Emigo] {'. '.join(report_parts).strip()}")

        return processed_mention_files_abs # Return list of absolute paths


    def generate_context_string(self, current_prompt: Optional[str] = None) -> str:
        """
        Generates the full context string (<context>) for the LLM,
        including file structure, chat file content, and the repository map.
        Handles @file mentions in the current_prompt if provided and includes
        related files based on those mentions.

        Args:
            current_prompt: The user's latest prompt (optional). If provided,
                            @file mentions within it will be processed.

        Returns:
            The formatted <context> string.
        """
        mentioned_files_abs = []
        # Handle @file mentions if a prompt is provided and get absolute paths
        if current_prompt:
            mentioned_files_abs = self._handle_mentions(current_prompt)
            # TODO: Extract mentioned identifiers here if needed

        # Clean up session cache for files no longer in chat_files list before generating
        current_chat_files_set = set(self.chat_files)
        for rel_path in list(self.caches['mtimes'].keys()):
            if rel_path not in current_chat_files_set:
                self.invalidate_cache(rel_path)

        # --- Start Building Context Sections ---
        token_counts = {}
        total_tokens = 0

        # Section 1: Header and Session Path
        header_section = "<context>\n"
        session_path_section = f"# Session Directory\n{self.session_path.replace(os.sep, '/')}\n\n" # Use POSIX path
        token_counts["Session Path"] = self._count_tokens(session_path_section)

        # Section 2: File/Directory Listing
        file_listing_section = "# File/Directory Structure (Source Files Only)\n"
        try:
            if self.repo_mapper:
                # Use RepoMapper's file finding logic for consistency
                all_files_abs = self.repo_mapper._find_src_files(self.session_path) # Find files respecting ignores
                tree_lines = []
                processed_dirs = set()
                for abs_file in sorted(all_files_abs):
                    try:
                        # Ensure the file is actually within the session path directory tree
                        if not abs_file.startswith(self.session_path):
                            continue # Skip files outside the session path (e.g., symlinks pointing out)

                        rel_file = os.path.relpath(abs_file, self.session_path).replace(os.sep, '/')
                        parts = rel_file.split('/')
                        current_path_prefix = ""
                        for i, part in enumerate(parts[:-1]): # Iterate through directories
                            dir_indent_level = i
                            current_path_prefix = f"{current_path_prefix}{part}/"
                            if current_path_prefix not in processed_dirs:
                                indent = '  ' * dir_indent_level
                                tree_lines.append(f"{indent}- {part}/")
                                processed_dirs.add(current_path_prefix)
                        # Add the file
                        file_indent_level = len(parts) - 1
                        indent = '  ' * file_indent_level
                        tree_lines.append(f"{indent}- {parts[-1]}")
                    except ValueError:
                        if self.verbose:
                            print(f"Warning: Could not get relative path for {abs_file} relative to {self.session_path}", file=sys.stderr)
                        continue # Skip this file

                if tree_lines:
                    file_listing_content = "```\n" + "\n".join(tree_lines) + "\n```\n\n"
                else:
                    file_listing_content = "(No relevant source files or directories found)\n\n"
            else:
                file_listing_content = "(RepoMapper not available for file listing)\n\n"
        except Exception as e:
            file_listing_content = f"# Error listing files/directories: {str(e)}\n\n"
            if self.verbose:
                import traceback
                file_listing_content += f"# Traceback:\n{traceback.format_exc()}\n"
        file_listing_section += file_listing_content
        token_counts["File Listing"] = self._count_tokens(file_listing_section)


        # Section 3: Repository Map
        repo_map_section = "# Repository Map (Ranked by Relevance, Excludes Chat Files)\n"
        try:
            if self.repo_mapper:
                # Convert relative chat file paths to absolute for RepoMapper
                chat_files_abs = [os.path.abspath(os.path.join(self.session_path, f)) for f in self.chat_files]
                # Generate the map - RepoMapper handles finding other files and ranking
                # Pass only chat_files_abs for ranking bias
                repo_map_content = self.repo_mapper.generate_map(chat_files=chat_files_abs)
                if repo_map_content and repo_map_content.strip():
                    repo_map_section += repo_map_content # Add the generated map
                else:
                    repo_map_section += "(No relevant files found for repository map or map is empty)\n\n" # Add newline
            else:
                repo_map_section += "(RepoMapper not available for map generation)\n"
        except Exception as e:
            repo_map_section += f"# Error generating repository map: {str(e)}\n"
            # Optionally include traceback if verbose
            if self.verbose:
                import traceback
                repo_map_section += f"# Traceback:\n{traceback.format_exc()}\n\n" # Add newline
        token_counts["Repo Map"] = self._count_tokens(repo_map_section)


        # Section 4: Related Files (Based on Mentions)
        related_files_section = "\n# Related Files (Based on @file mentions)\n"
        related_files_content = "(No @file mentions in the last prompt or no related files found)\n"
        if mentioned_files_abs:
            try:
                # Pass absolute paths of mentioned files, get relative paths back
                # TODO: Pass mentioned identifiers if extracted
                related_files_rel = self.repo_mapper.get_related_files(
                    target_files=mentioned_files_abs
                )
                if related_files_rel:
                    related_files_content = "```\n" + "\n".join(sorted(related_files_rel)) + "\n```\n"
                else:
                     related_files_content = "(No files found referencing or defining elements from mentioned files)\n"
            except Exception as e:
                related_files_content = f"# Error finding related files: {str(e)}\n"
                if self.verbose:
                    import traceback
                    related_files_content += f"# Traceback:\n{traceback.format_exc()}\n"
        related_files_section += related_files_content + "\n"
        token_counts["Related Files"] = self._count_tokens(related_files_section)


        # Section 5: Chat File Content
        chat_files_section = ""
        if self.chat_files:
            chat_files_section += "# Files Currently in Chat (The ONLY source-of-truth representing up-to-date file content)\n"
            for rel_path in sorted(self.chat_files): # Sort for consistent order
                posix_rel_path = rel_path.replace(os.sep, '/')
                file_content_str = ""
                try:
                    # Get content using the internal cache getter method
                    content = self.get_cached_content(rel_path) # This handles cache updates
                    if content is None:
                        content = f"# Error: Could not get content for {posix_rel_path}"
                        if self.verbose:
                             print(f"Content retrieval failed for {rel_path} during context generation.", file=sys.stderr)
                    else:
                        # Only add actual content if retrieved successfully
                        pass # Content is ready

                    # Use markdown code block for file content
                    file_content_str = f"## File: {posix_rel_path}\n```\n{content}\n```\n\n"

                except Exception as e:
                    file_content_str = f"## File: {posix_rel_path}\n# Error accessing file content: {e}\n"

                chat_files_section += file_content_str
        else:
             chat_files_section += "# Files Currently in Chat Context\n(None)\n"
        token_counts["Chat Files"] = self._count_tokens(chat_files_section)


        # Section 6: Footer
        footer_section = "\n</context>"
        token_counts["Tags"] = self._count_tokens(header_section + footer_section) # Count <context> tags

        # --- Combine Sections and Print Token Counts ---
        details = (
            header_section +
            session_path_section +
            file_listing_section +
            repo_map_section +
            related_files_section +
            chat_files_section +
            footer_section
        )

        total_tokens = sum(token_counts.values())
        print("--- Context Token Counts ---", file=sys.stderr)
        for section, count in token_counts.items():
            print(f"- {section}: {count}", file=sys.stderr)
        print(f"--- Total Context Tokens: {total_tokens} ---", file=sys.stderr)

        return details
