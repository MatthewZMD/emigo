<p align="center">
  <img style='height: auto; width: 35%; object-fit: contain' src="./Emigo.png">
</p>

# Emigo: Future of Agentic Development in Emacs

Emigo intends to be an intelligent, agentic Emacs-native AI assistant that understands and interacts with your codebase. Brought to you by the creators of [Emacs Application Framework](https://github.com/emacs-eaf/emacs-application-framework) and [lsp-bridge](https://github.com/manateelazycat/lsp-bridge), built on the shoulders of giants like [Cursor](https://www.cursor.com/en), [Aider](https://github.com/paul-gauthier/aider) and [Cline](https://github.com/sturdy-dev/cline), Emigo is the spiritual successor to [Aidermacs](https://github.com/MatthewZMD/aidermacs), reimagined from the ground up for Emacs.

## ⚠️ Emigo is under *very* active development, experimentation, and rapid-prototyping ⚠️

The project is at its early-stage. Expect frequent breaking changes and unstable features. Please use only for testing, try it out and report issues - your feedback helps shape Emigo!

## Key Features

*   **Agentic Tool Use:** Emigo doesn't just generate text; it uses tools to interact with your environment based on the LLM's reasoning.
*   **Emacs Integration:** Designed to feel native within Emacs, leveraging familiar interfaces and workflows.
*   **Flexible LLM Support:** Connects to various LLM providers through [LiteLLM](https://github.com/BerriAI/litellm), allowing you to choose the model that best suits your needs.
*   **Context-Aware Interactions:** Manages chat history and project context for coherent sessions.

## Installation

1.  **Prerequisites:**
    *   Emacs 28 or higher.
    *   Python 3.x.
2.  **Install Python Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Install with straight.el:** Add to your Emacs config:

    ```emacs-lisp
    (use-package emigo
      :straight (:host github :repo "MatthewZMD/emigo" :files (:defaults "*.py" "*.el"))
      :config
      (emigo-enable) ;; Starts the background process automatically
      :custom
      ;; Encourage using OpenRouter with Deepseek
      (emigo-model "openrouter/deepseek/deepseek-chat-v3-0324")
      (emigo-base-url "https://openrouter.ai/api/v1")
      (emigo-api-key (getenv "OPENROUTER_API_KEY")))
    ```

## Usage

### Basic Interaction
1. **Start Emigo:** Navigate to your project directory (or any directory you want to work in) and run `M-x emigo`.
2. **Interact:** Emigo will open a dedicated buffer. The AI will respond, potentially using tools. You might be asked for approval for certain actions (like running commands or writing files).
3. **Send Prompts:** Type your prompt and press `C-c C-c` or `C-m` to send it to Emigo.

### Context Management
- **Add Files:**
  - Mention files in your prompt using `@` (e.g., `Refactor @src/utils.py`)
  - Or use `C-c f` to interactively add files
- **List Files in Context:** `C-c l`
- **Remove Files from Context:** `C-c j`
- **Clear Chat History:** `C-c H`
- **View History:** `C-c h` (shows in Org mode buffer)

### Keybindings
#### Core Commands
- `C-c C-c` / `C-m` - Send current prompt
- `C-c r` - Restart Emigo process
- `C-c k` - Cancel current interaction

Note: Emigo manages sessions based on the directory where you invoke `M-x emigo`. If invoked within a Git repository, the repository root is typically used as the session path. Use `C-u M-x emigo` to force the session path to be the current `default-directory`.

## Understanding Tool Use

The core of Emigo's power lies in its agentic tool use. Instead of just providing code suggestions, the LLM analyzes your request and decides which actions (tools) are necessary to accomplish the task.

1.  **LLM Reasoning:** Based on your prompt and the current context, the LLM determines the next step.
2.  **Tool Selection:** It chooses an appropriate tool, such as `read_file`, `write_to_file`, `replace_in_file`, `execute_command`, `list_files`, `list_repomap`, or `ask_followup_question`.
3.  **Tool Execution:** Emigo executes the chosen tool, potentially asking for your approval for sensitive operations.
4.  **Result Feedback:** The result of the tool execution (e.g., file content, command output, error message) is fed back into the conversation history.
5.  **Iteration:** The LLM uses this new information to decide the next step, continuing the cycle until the task is complete or requires further input.

This iterative process allows Emigo to tackle more complex tasks that involve multiple steps and interactions with your project files and system. The LLM uses an XML format to specify the tool and its parameters.
