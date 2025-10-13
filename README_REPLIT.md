# Emigo on Replit - Setup Guide

## What is Emigo?

**Emigo** is an AI coding assistant for Emacs - it's an intelligent, agentic plugin that understands and interacts with your codebase directly within Emacs.

⚠️ **Important:** This is **NOT a web application**. It's an Emacs editor plugin with a Python backend.

## What's Set Up on Replit

✅ **Python 3.11** - Installed and configured  
✅ **All Dependencies** - litellm, epc, networkx, pygments, and all other requirements  
✅ **Code Validation** - All modules pass syntax checks  
✅ **Test Workflow** - Validation test runs successfully  

## How to Use This Project

### Option 1: Use in Your Own Emacs Setup

1. **Clone this repo to your local machine:**
   ```bash
   git clone <your-replit-repo-url>
   cd emigo
   ```

2. **Ensure Python dependencies are installed:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install in Emacs** using straight.el:
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

4. **Set up your API key** (e.g., for OpenRouter):
   ```bash
   export OPENROUTER_API_KEY="your-api-key-here"
   ```

5. **Use in Emacs:**
   - Open your project in Emacs
   - Run `M-x emigo`
   - Start coding with AI assistance!

### Option 2: Test/Develop on Replit

Since Emacs isn't pre-installed on Replit, you can:

1. **Validate the setup** (already configured):
   - Click the "Run" button to see the validation test
   - All dependencies and modules should show ✓ OK

2. **Install Emacs on Replit** (optional, for testing):
   ```bash
   # Install Emacs
   nix-env -iA nixpkgs.emacs
   
   # Verify installation
   emacs --version
   ```

3. **Develop/modify the Python backend:**
   - All Python files are ready for development
   - Core modules: `emigo.py`, `llm_worker.py`, `session.py`, `tools.py`
   - Test your changes with: `python3 test_setup.py`

## Key Features

- 🤖 **Agentic Tool Use** - LLM can execute tools (read/write files, run commands)
- 🔌 **Flexible LLM Support** - Works with OpenRouter, Deepseek, OpenAI, and more
- 📝 **Context-Aware** - Maintains chat history and project context
- 🎯 **Repository Mapping** - Understands your codebase structure

## Emacs Commands (when running in Emacs)

- `M-x emigo` - Start Emigo
- `C-c C-c` or `C-m` - Send prompt
- `C-c f` - Add files to context
- `C-c l` - List files in context
- `C-c j` - Remove files from context
- `C-c H` - Clear chat history
- `C-c r` - Restart Emigo process

## Project Structure

```
emigo/
├── emigo.py              # Main Python backend (EPC server)
├── llm_worker.py         # LLM interaction subprocess
├── emigo.el              # Emacs Lisp frontend
├── emigo-epc.el          # EPC client for Emacs
├── session.py            # Session management
├── tools.py              # Tool implementations
├── repomapper.py         # Repository mapping
├── requirements.txt      # Python dependencies
└── test_setup.py         # Validation script
```

## Architecture

1. **Emacs** (frontend) ↔️ **EPC** ↔️ **emigo.py** (backend)
2. **emigo.py** manages **llm_worker.py** subprocess
3. **llm_worker.py** handles LLM API calls via LiteLLM
4. Tools execute in **tools.py** and report back to Emacs

## For Developers

If you're contributing to Emigo:

1. **Run validation:** `python3 test_setup.py`
2. **Syntax check:** `python3 -m py_compile <filename>.py`
3. **Check imports:** `python3 -c "import <module>"`

## Resources

- **Original Repository:** [MatthewZMD/emigo](https://github.com/MatthewZMD/emigo)
- **Documentation:** See main [README.md](README.md)
- **Emacs Config:** See [emigo.el](emigo.el) for Elisp side

## Getting Help

This project is in active development. Check the [issues page](https://github.com/MatthewZMD/emigo/issues) for known problems and feature requests.

---

**Note:** Emigo requires Emacs to run. This Replit setup validates that the Python backend is correctly configured and ready to use.
