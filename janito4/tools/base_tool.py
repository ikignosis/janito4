#!/usr/bin/env python3
"""
Base Tool Class - A foundation for AI tools with built-in progress reporting.

This module provides a base class that tools can inherit from to get automatic
progress reporting capabilities and permission awareness.
"""

import sys
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from ..tooling.progress.reporter import ToolProgressReporter


class BaseTool(ABC):
    """
    Base class for AI tools with built-in progress reporting and permissions.
    
    Tools should inherit from this class and implement the `run` method.
    The class automatically provides progress reporting methods that are
    aware of the tool's declared permissions.
    """
    
    # Class-level permissions attribute (set by the @tool decorator)
    _tool_permissions: str = ""
    
    def __init__(self):
        """Initialize the base tool."""
        pass
    
    @abstractmethod
    def run(self, **kwargs) -> Dict[str, Any]:
        """
        Execute the tool's main functionality.
        
        This method must be implemented by subclasses.
        
        Args:
            **kwargs: Tool-specific parameters
            
        Returns:
            Dict[str, Any]: Tool result dictionary
        """
        pass
    
    def report_start(self, message: str, end: str = "\n") -> None:
        """
        Report that the tool operation is starting.
        
        Args:
            message (str): The message to display
            end (str): String appended after the message (default: "\n")
        """
        self._report_with_permissions(message, end, "start")
    
    def report_progress(self, message: str, end: str = "\n") -> None:
        """
        Report ongoing progress of the tool operation.
        
        Args:
            message (str): The progress message to display
            end (str): String appended after the message (default: "\n")
        """
        self._report_with_permissions(message, end, "progress")
    
    def report_result(self, message: str, end: str = "\n") -> None:
        """
        Report intermediate or final results from the tool operation.
        
        Args:
            message (str): The result message to display
            end (str): String appended after the message (default: "\n")
        """
        self._report_with_permissions(message, end, "result")
    
    def report_error(self, message: str, end: str = "\n") -> None:
        """
        Report an error during tool execution.
        
        Args:
            message (str): The error message to display
            end (str): String appended after the message (default: "\n")
        """
        print(f"?{message}", file=sys.stderr, end=end, flush=True)
    
    def report_warning(self, message: str, end: str = "\n") -> None:
        """
        Report a warning during tool execution.
        
        Args:
            message (str): The warning message to display
            end (str): String appended after the message (default: "\n")
        """
        print(f"??{message}", file=sys.stderr, end=end, flush=True)
    
    def _get_permission_color(self) -> str:
        """
        Get ANSI color code based on tool permissions.
        
        Returns:
            str: ANSI color escape sequence
        """
        permissions = getattr(self, '_tool_permissions', "")
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
    
    def _report_with_permissions(self, message: str, end: str, report_type: str) -> None:
        """
        Internal method to report messages with permission-based coloring.
        
        Args:
            message (str): The message to display
            end (str): String appended after the message
            report_type (str): Type of report ("start", "progress", "result")
        """
        permissions = getattr(self, '_tool_permissions', "")
        color = self._get_permission_color()
        reset_color = "\033[0m"
        
        # Add permission indicator to start messages only
        if report_type == "start" and permissions:
            permission_indicator = f"[{permissions}] "
        else:
            permission_indicator = ""
            
        colored_message = f"{color}{permission_indicator}{message}{reset_color}"
        print(colored_message, file=sys.stderr, end=end, flush=True)