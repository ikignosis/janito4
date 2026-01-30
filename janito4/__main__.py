#!/usr/bin/env python3
"""
OpenAI CLI - A simple command-line interface to interact with OpenAI-compatible endpoints.

This CLI uses environment variables for configuration:
- BASE_URL: The base URL of the OpenAI-compatible API endpoint (optional for standard OpenAI)
- API_KEY: The API key for authentication
- MODEL: The model name to use for completions

The CLI includes function calling tools that can be used by the AI model.

Usage:
    python -m janito4 "Your prompt here"
    echo "Your prompt" | python -m janito4
    python -m janito4 --chat  # Start interactive chat session
"""

import os
import sys
import argparse
from typing import Optional, List, Dict, Any

# Import the send_prompt function from the new module
try:
    from .openai_client import send_prompt
except ImportError:
    # When running directly, not as a module
    from openai_client import send_prompt

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="OpenAI CLI - Send prompts to OpenAI-compatible endpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  BASE_URL    - Base URL of the OpenAI-compatible API endpoint (optional for standard OpenAI)
  API_KEY     - API key for authentication  
  MODEL       - Model name to use for completions

Examples:
  python -m janito4 "What is the capital of France?"
  echo "Tell me a joke" | python -m janito4
  python -m janito4 --chat
        """
    )
    
    parser.add_argument(
        "prompt", 
        nargs="?", 
        help="The prompt to send to the AI"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output (shows model and backend info)"
    )
    
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Start an interactive chat session using prompt_toolkit"
    )
    
    args = parser.parse_args()
    
    # Handle chat mode
    if args.chat:
        if not PROMPT_TOOLKIT_AVAILABLE:
            print("Error: prompt_toolkit is required for chat mode. Install it with 'pip install prompt_toolkit'", file=sys.stderr)
            sys.exit(1)
        
        print("Starting interactive chat session. Type 'exit' or 'quit' to end the session.")
        session = PromptSession(history=InMemoryHistory())
        messages_history: List[Dict[str, Any]] = []
        
        try:
            while True:
                try:
                    user_input = session.prompt(">>> ")
                    if user_input.lower() in ['exit', 'quit']:
                        break
                    if user_input.strip():
                        response = send_prompt(user_input, verbose=args.verbose, previous_messages=messages_history)
                        # Add the user message and AI response to history
                        messages_history.append({"role": "user", "content": user_input})
                        if response:
                            messages_history.append({"role": "assistant", "content": response})
                except KeyboardInterrupt:
                    print("\nUse 'exit' or 'quit' to end the session.")
                    continue
        except EOFError:
            pass  # Handle Ctrl+D gracefully
        print("\nChat session ended.")
        return
    
    # Handle single prompt mode
    if args.prompt is None:
        parser.print_help()
        sys.exit(0)
    
    prompt = args.prompt
    
    if not prompt:
        print("Error: Empty prompt provided.", file=sys.stderr)
        sys.exit(1)
    
    try:
        send_prompt(prompt, verbose=args.verbose)
    except ValueError as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Runtime Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()