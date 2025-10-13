# Emigo - AI Assistant for Emacs

## Project Overview

**Emigo** is an intelligent, agentic Emacs-native AI assistant that understands and interacts with your codebase. It's designed to run as a Python backend that communicates with Emacs via EPC (Emacs Process Communication).

**Key Features:**
- Agentic tool use - interacts with your environment based on LLM reasoning
- Native Emacs integration
- Flexible LLM support via LiteLLM (OpenRouter, Deepseek, etc.)
- Context-aware interactions with chat history and project context

## Project Type

This is **NOT a web application** - it's an Emacs plugin with a Python backend. The Python code (`emigo.py`) runs as an EPC server that Emacs connects to for AI-powered coding assistance.

## Architecture

### Main Components:
1. **emigo.el** - Emacs Lisp frontend
2. **emigo.py** - Python EPC orchestrator (main backend)
3. **llm_worker.py** - LLM interaction subprocess
4. **session.py** - Session management
5. **tools.py** - Tool implementations (read_file, write_file, execute_command, etc.)
6. **repomapper.py** - Repository mapping for context

### How It Works:
1. Emacs starts the Python backend (`emigo.py`) as a subprocess
2. Python starts an EPC server on a random port
3. Emacs connects to the EPC server
4. User interacts via Emacs buffers
5. LLM worker handles AI interactions and tool execution

## Replit Setup Status

✅ **Python 3.11 installed**
✅ **All Python dependencies installed** (litellm, epc, networkx, etc.)
✅ **Project is ready to use**

## Usage in Emacs

This project is designed to be used from within Emacs:

1. **Install in Emacs** using straight.el:
```emacs-lisp
(use-package emigo
  :straight (:host github :repo "MatthewZMD/emigo" :files (:defaults "*.py" "*.el"))
  :config
  (emigo-enable)
  :custom
  (emigo-model "openrouter/deepseek/deepseek-chat-v3-0324")
  (emigo-base-url "https://openrouter.ai/api/v1")
  (emigo-api-key (getenv "OPENROUTER_API_KEY")))
```

2. **Basic Usage:**
- `M-x emigo` - Start Emigo in your project
- Type prompts and press `C-c C-c` to send
- Use `@filename` to add files to context
- `C-c f` - Add files interactively
- `C-c l` - List files in context
- `C-c H` - Clear chat history

## Testing on Replit

Since this is an Emacs plugin and Replit doesn't have Emacs pre-installed, you can:

1. **Validate Python Setup:**
   ```bash
   python3 --version  # Check Python
   pip list | grep -E "(litellm|epc)"  # Verify dependencies
   ```

2. **Check Code Structure:**
   ```bash
   python3 -m py_compile emigo.py  # Syntax check
   ```

3. **Install Emacs (optional):**
   If you want to actually run Emigo in Replit, you would need to install Emacs first.

## Development Notes

- Written in Python 3.x (tested with 3.11)
- Uses EPC for Emacs-Python communication
- LLM interactions via LiteLLM (supports multiple providers)
- Active development - expect breaking changes

## Recent Setup

- 2025-10-13: Imported to Replit, installed Python 3.11 and all dependencies
