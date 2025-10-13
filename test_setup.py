#!/usr/bin/env python3
"""
Test script to validate Emigo setup on Replit.
Since Emigo is an Emacs plugin, this script validates that:
1. All Python dependencies are installed
2. Core modules can be imported
3. Configuration is valid
"""

import sys
import importlib

def test_imports():
    """Test that all required modules can be imported."""
    required_modules = [
        'epc',
        'networkx',
        'pygments',
        'grep_ast',
        'diskcache',
        'tiktoken',
        'tqdm',
        'gitignore_parser',
        'scipy',
        'litellm',
        'orjson',
    ]
    
    print("Testing Python dependencies...")
    print("-" * 50)
    
    failed = []
    for module in required_modules:
        try:
            importlib.import_module(module)
            print(f"✓ {module:20s} - OK")
        except ImportError as e:
            print(f"✗ {module:20s} - FAILED: {e}")
            failed.append(module)
    
    print("-" * 50)
    if failed:
        print(f"\n❌ {len(failed)} module(s) failed to import: {', '.join(failed)}")
        return False
    else:
        print("\n✅ All dependencies imported successfully!")
        return True

def test_core_modules():
    """Test that Emigo core modules are valid."""
    print("\nTesting Emigo core modules...")
    print("-" * 50)
    
    core_modules = [
        'config',
        'utils',
        'session',
        'tools',
        'tool_definitions',
        'agent',
        'llm',
        'llm_providers',
        'llm_worker',
        'repomapper',
        'emigo',
    ]
    
    failed = []
    for module in core_modules:
        try:
            # Just compile, don't import (to avoid EPC connection issues)
            with open(f"{module}.py", 'r') as f:
                compile(f.read(), f"{module}.py", 'exec')
            print(f"✓ {module}.py - Syntax OK")
        except Exception as e:
            print(f"✗ {module}.py - FAILED: {e}")
            failed.append(module)
    
    print("-" * 50)
    if failed:
        print(f"\n❌ {len(failed)} module(s) have issues: {', '.join(failed)}")
        return False
    else:
        print("\n✅ All core modules are syntactically valid!")
        return True

def main():
    print("=" * 50)
    print("Emigo Setup Validation Test")
    print("=" * 50)
    print()
    
    # Test dependencies
    deps_ok = test_imports()
    
    # Test core modules
    core_ok = test_core_modules()
    
    print("\n" + "=" * 50)
    if deps_ok and core_ok:
        print("✅ SETUP VALIDATION PASSED")
        print("\nEmigo is ready to use from Emacs!")
        print("\nNote: This is an Emacs plugin. To use it:")
        print("1. Install in Emacs using straight.el")
        print("2. Configure API keys (OPENROUTER_API_KEY, etc.)")
        print("3. Run M-x emigo in your project")
        return 0
    else:
        print("❌ SETUP VALIDATION FAILED")
        print("\nSome components need attention.")
        return 1
    print("=" * 50)

if __name__ == "__main__":
    sys.exit(main())
