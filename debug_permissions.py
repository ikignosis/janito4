import sys
import os
sys.path.insert(0, '.')

import inspect

def debug_get_calling_tool_permissions():
    """Debug version of the function."""
    try:
        frame = inspect.currentframe()
        if frame is None:
            print("No frame")
            return ""
        
        # Print frames
        current = frame
        i = 0
        while current is not None and i < 5:
            print(f"Frame {i}: {current.f_code.co_name}")
            current = current.f_back
            i += 1
        
        # Go back 2 frames
        current = frame
        for _ in range(2):
            if current.f_back is None:
                print("Not enough frames!")
                return ""
            current = current.f_back
        
        func_name = current.f_code.co_name
        print(f"Detected function name: {func_name}")
        
        # Try to get from globals
        if func_name in current.f_globals:
            tool_func = current.f_globals[func_name]
            print(f"Found in globals: {tool_func}")
            permissions = getattr(tool_func, '_tool_permissions', "NOT SET")
            print(f"Permissions from globals: {permissions}")
            if permissions != "NOT SET":
                return permissions
        
        # Try registry
        try:
            from janito4.tooling.tools_registry import AVAILABLE_TOOLS
            print(f"Available tools: {list(AVAILABLE_TOOLS.keys())}")
            if func_name in AVAILABLE_TOOLS:
                perms = getattr(AVAILABLE_TOOLS[func_name], '_tool_permissions', "")
                print(f"Permissions from registry: {perms}")
                return perms
        except Exception as e:
            print(f"Registry error: {e}")
            
    except Exception as e:
        print(f"Error: {e}")
    
    return ""

# Test with actual tool
from janito4.tools.files.read_file import read_file

def test_wrapper():
    return debug_get_calling_tool_permissions()

# Call through the actual tool mechanism
result = read_file("README.md", max_lines=1)
print(f"Final result: '{result}'")