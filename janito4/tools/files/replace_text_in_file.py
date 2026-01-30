#!/usr/bin/env python3
"""
Replace Text In File Tool - A class-based tool for replacing text in a file.

This tool demonstrates how to use the base tool class with progress reporting.

Note: This tool requires the progress reporting system from the tooling package.
For direct execution, use: python -m janito4.tools.files.replace_text_in_file [args]
For AI function calling, use through the tool registry (tooling.tools_registry).
"""

import os
import json
from typing import Dict, Any
from ...tooling import BaseTool, norm_path
from ..decorator import tool


@tool(permissions="rw")
class ReplaceTextInFile(BaseTool):
    """
    Tool for replacing text in a file.
    
    Requirements:
    - If old_str is not found, returns an error with warning
    - If old_str is found multiple times, returns an error explaining that multiple occurrences were found
    - The old_str needs to be unique in the file
    """
    
    def run(self, filepath: str, old_str: str, new_str: str) -> Dict[str, Any]:
        """
        Replace text in a file.
        
        Args:
            filepath (str): The path to the file to modify
            old_str (str): The exact text to search for and replace
            new_str (str): The text to replace with
        
        Returns:
            Dict[str, Any]: A dictionary containing:
                - 'success': bool indicating if operation succeeded
                - 'filepath': the file that was modified
                - 'old_str': the text that was searched for
                - 'new_str': the replacement text
                - 'occurrences': number of occurrences found
                - 'error': error message if operation failed (only present if success=False)
        """
        try:
            abs_filepath = os.path.abspath(filepath)
            norm_path_str = norm_path(abs_filepath)
            
            # Report start
            self.report_start(f"? Replacing text in file {norm_path_str}", end="")
            
            if not os.path.exists(abs_filepath):
                self.report_error(f"File does not exist: {norm_path_str}")
                return {
                    "success": False,
                    "error": f"File does not exist: {norm_path_str}",
                    "filepath": filepath,
                    "old_str": old_str,
                    "new_str": new_str
                }
            
            if not os.path.isfile(abs_filepath):
                self.report_error(f"Path is not a file: {norm_path_str}")
                return {
                    "success": False,
                    "error": f"Path is not a file: {norm_path_str}",
                    "filepath": filepath,
                    "old_str": old_str,
                    "new_str": new_str
                }
            
            # Get file size for progress indication
            file_size = os.path.getsize(abs_filepath)
            size_str = f"({file_size} bytes)"
            self.report_progress(f" {size_str}", end="")
            
            # Read the file content
            with open(abs_filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Count occurrences of old_str
            occurrences = content.count(old_str)
            
            if occurrences == 0:
                error_msg = f"Warning: Search text '{old_str}' not found in file"
                self.report_error(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "filepath": filepath,
                    "old_str": old_str,
                    "new_str": new_str,
                    "occurrences": 0
                }
            
            if occurrences > 1:
                error_msg = f"Error: Multiple occurrences ({occurrences}) of '{old_str}' found. The search text needs to be unique in the file."
                self.report_error(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "filepath": filepath,
                    "old_str": old_str,
                    "new_str": new_str,
                    "occurrences": occurrences
                }
            
            # Perform the replacement (should be exactly one occurrence)
            new_content = content.replace(old_str, new_str, 1)  # Replace only first occurrence (though there should be only one)
            
            # Write the modified content back to the file
            with open(abs_filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            self.report_result(f"? Text replaced successfully")
            
            return {
                "success": True,
                "filepath": filepath,
                "old_str": old_str,
                "new_str": new_str,
                "occurrences": occurrences
            }
            
        except Exception as e:
            self.report_error(f"Error replacing text: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "filepath": filepath,
                "old_str": old_str,
                "new_str": new_str
            }


# CLI interface for testing
def main():
    """Command line interface for testing the ReplaceTextInFile tool."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Replace text in a file (requires unique match)")
    parser.add_argument("filepath", help="File path to modify")
    parser.add_argument("old_str", help="Text to search for and replace")
    parser.add_argument("new_str", help="Replacement text")
    parser.add_argument("--json", "-j", action="store_true", help="Output in JSON format")
    
    args = parser.parse_args()
    
    tool_instance = ReplaceTextInFile()
    result = tool_instance.run(
        filepath=args.filepath,
        old_str=args.old_str,
        new_str=args.new_str
    )
    
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["success"]:
            print(f"? Text replaced successfully in '{result['filepath']}'")
        else:
            print(f"? Error: {result['error']}")


if __name__ == "__main__":
    main()