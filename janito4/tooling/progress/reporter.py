#!/usr/bin/env python3
"""
Tool Progress Reporter - A utility for tools to report their progress during execution.

This module provides a standardized way for tools to communicate their progress
to the user without interfering with the final tool result that gets returned
to the AI model.
"""

import sys
import inspect
from typing import Optional, Dict, Any


def _get_calling_tool_permissions() -> str:
    """
    Get the permissions of the calling tool function.
    
    This function inspects the call stack to find the tool function that called
    the progress reporter and returns its declared permissions.
    
    Returns:
        str: Permission string (e.g., "r", "rw", "rwx") or empty string if not found
    """
    try:
        # Get the current frame and walk up the stack
        frame = inspect.currentframe()
        if frame is None:
            return ""
        
        # Go back 2 frames to reach the tool function that called the reporter
        current_frame = frame
        for _ in range(2):
            if current_frame.f_back is None:
                return ""
            current_frame = current_frame.f_back
        
        # Get the function name from the frame
        func_name = current_frame.f_code.co_name
        
        # Look up the function in the tools registry
        try:
            # Import sys at the module level already exists
            if 'janito4.tooling.tools_registry' in sys.modules:
                AVAILABLE_TOOLS = sys.modules['janito4.tooling.tools_registry'].AVAILABLE_TOOLS
            else:
                from janito4.tooling.tools_registry import AVAILABLE_TOOLS
            
            if func_name in AVAILABLE_TOOLS:
                return getattr(AVAILABLE_TOOLS[func_name], '_tool_permissions', "")
                
        except Exception:
            # If registry lookup fails, try to get from frame globals as fallback
            if func_name in current_frame.f_globals:
                tool_func = current_frame.f_globals[func_name]
                if callable(tool_func):
                    return getattr(tool_func, '_tool_permissions', "")
            
    except Exception:
        # Silently fail if we can't determine permissions
        pass
    
    return ""


def _get_permission_color(permissions: str) -> str:
    """
    Get ANSI color code based on permissions.
    
    Args:
        permissions (str): Permission string
        
    Returns:
        str: ANSI color escape sequence
    """
    if not permissions:
        return "\033[36m"  # Cyan for no permissions (default)
    elif "w" in permissions or "x" in permissions:
        return "\033[31m"  # Red for write/execute (dangerous)
    elif "n" in permissions:
        return "\033[35m"  # Magenta for network access
    elif "r" in permissions:
        return "\033[32m"  # Green for read-only (safe)
    else:
        return "\033[36m"  # Cyan as fallback


class ToolProgressReporter:
    """
    A progress reporter for AI tools that allows real-time status updates.
    
    This class provides methods for tools to report their current state,
    progress, and intermediate results to the user via stderr (so it doesn't
    interfere with the tool's actual return value).
    """
    
    @staticmethod
    def report_start(message: str, end: str = "\n") -> None:
        """
        Report that a tool operation is starting.
        
        Args:
            message (str): The message to display
            end (str): String appended after the message (default: "\n")
        """
        # Get the calling tool's permissions
        permissions = _get_calling_tool_permissions()
        color = _get_permission_color(permissions)
        reset_color = "\033[0m"
        colored_message = f"{color}{message}{reset_color}"
        print(colored_message, file=sys.stderr, end=end, flush=True)
    
    @staticmethod
    def report_progress(message: str, end: str = "\n") -> None:
        """
        Report ongoing progress of a tool operation.
        
        Args:
            message (str): The progress message to display
            end (str): String appended after the message (default: "\n")
        """
        print(f"{message}", file=sys.stderr, end=end, flush=True)
    
    @staticmethod
    def report_result(message: str, end: str = "\n") -> None:
        """
        Report intermediate or final results from a tool operation.
        
        Args:
            message (str): The result message to display
            end (str): String appended after the message (default: "\n")
        """
        print(f"{message}", file=sys.stderr, end=end, flush=True)
    
    @staticmethod
    def report_error(message: str, end: str = "\n") -> None:
        """
        Report an error during tool execution.
        
        Args:
            message (str): The error message to display
            end (str): String appended after the message (default: "\n")
        """
        print(f"❌{message}", file=sys.stderr, end=end, flush=True)
    
    @staticmethod
    def report_warning(message: str, end: str = "\n") -> None:
        """
        Report a warning during tool execution.
        
        Args:
            message (str): The warning message to display
            end (str): String appended after the message (default: "\n")
        """
        print(f"⚠️{message}", file=sys.stderr, end=end, flush=True)


# Convenience functions for backward compatibility and ease of use
def report_start(message: str, end: str = "\n") -> None:
    """Convenience function to report tool start."""
    ToolProgressReporter.report_start(message, end)


def report_progress(message: str, end: str = "\n") -> None:
    """Convenience function to report tool progress."""
    ToolProgressReporter.report_progress(message, end)


def report_result(message: str, end: str = "\n") -> None:
    """Convenience function to report tool results."""
    ToolProgressReporter.report_result(message, end)


def report_error(message: str, end: str = "\n") -> None:
    """Convenience function to report tool errors."""
    ToolProgressReporter.report_error(message, end)


def report_warning(message: str, end: str = "\n") -> None:
    """Convenience function to report tool warnings."""
    ToolProgressReporter.report_warning(message, end)