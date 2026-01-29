#!/usr/bin/env python3
"""
OpenAI CLI - A simple command-line interface to interact with OpenAI-compatible endpoints.

This CLI uses environment variables for configuration:
- BASE_URL: The base URL of the OpenAI-compatible API endpoint (optional for standard OpenAI)
- API_KEY: The API key for authentication
- MODEL: The model name to use for completions

The CLI includes function calling tools that can be used by the AI model.

Usage:
    python openai_cli.py "Your prompt here"
    echo "Your prompt" | python openai_cli.py
"""

import os
import sys
import argparse
import json
from typing import Optional, Tuple, Dict, Any, List
from openai import OpenAI
from rich.console import Console
from rich.markdown import Markdown

# Import tools
try:
    from .tooling.tools_registry import get_all_tool_schemas, get_tool_by_name
    TOOLS_AVAILABLE = True
except ImportError:
    TOOLS_AVAILABLE = False
    def get_all_tool_schemas():
        return []
    def get_tool_by_name(name):
        raise NotImplementedError("Tools not available")


def get_env_vars() -> Tuple[str, str, str]:
    """Retrieve required environment variables."""
    base_url = os.getenv("BASE_URL")
    api_key = os.getenv("API_KEY")
    model = os.getenv("MODEL")
    
    if not api_key:
        raise ValueError("API_KEY environment variable is required")
    if not model:
        raise ValueError("MODEL environment variable is required")
    
    return base_url, api_key, model


def send_prompt(prompt: str, verbose: bool = False) -> str:
    """Send prompt to OpenAI endpoint and return response."""
    base_url, api_key, model = get_env_vars()
    
    # Print model and backend info only in verbose mode
    if verbose:
        backend = base_url if base_url else "api.openai.com"
        print(f"────────────── Model: {model} | Backend: {backend}", file=sys.stderr)
    
    # Create OpenAI client - base_url can be None for standard OpenAI
    client = OpenAI(
        api_key=api_key,
        base_url=base_url
    )
    
    # Get available tools
    tools_schemas = get_all_tool_schemas() if TOOLS_AVAILABLE else []
    
    try:
        messages = [{"role": "user", "content": prompt}]
        
        while True:
            # Make API call with tools if available
            if tools_schemas:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=1.0,
                    tools=tools_schemas,
                    tool_choice="auto"
                )
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=1.0
                )
            
            message = response.choices[0].message
            
            # Check if the model wants to call a function
            if hasattr(message, 'tool_calls') and message.tool_calls:
                # Add the model's response to messages
                messages.append(message)
                
                # Process each tool call
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)
                    
                    try:
                        # Call the actual tool function
                        tool_function = get_tool_by_name(tool_name)
                        tool_result = tool_function(**tool_args)
                        
                        # Add the tool response to messages
                        messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": tool_name,
                            "content": json.dumps(tool_result)
                        })
                        

                        
                    except Exception as e:
                        # Handle tool execution errors
                        error_result = {
                            "success": False,
                            "error": f"Tool execution failed: {str(e)}"
                        }
                        messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": tool_name,
                            "content": json.dumps(error_result)
                        })
                        print(f"❌ Tool error: {tool_name} - {e}", file=sys.stderr)
                
                # Continue the loop to get the final response after tool calls
                continue
            else:
                # No more tool calls, return the final response
                return message.content if message.content else ""
                
    except Exception as e:
        raise RuntimeError(f"Error communicating with API: {e}")


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
    
    args = parser.parse_args()
    
    # If no prompt is provided, show usage and exit
    if args.prompt is None:
        parser.print_help()
        sys.exit(0)
    
    prompt = args.prompt
    
    if not prompt:
        print("Error: Empty prompt provided.", file=sys.stderr)
        sys.exit(1)
    
    try:
        response = send_prompt(prompt, verbose=args.verbose)
        console = Console()
        console.print(Markdown(response))
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