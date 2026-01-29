# Tool Progress Reporting System

This module provides a standardized way for AI tools to report their progress during execution.

**Location**: `tooling/progress/` (support code for tools, not actual tools themselves)

## Features

- **Real-time progress updates**: Tools can communicate their current state to users
- **Non-interfering output**: Progress messages are sent to `stderr` so they don't affect the tool's return value
- **Consistent formatting**: Standardized emoji prefixes for different types of messages
- **Easy integration**: Simple function calls that work both in tool context and standalone

## Usage

### Basic Usage

```python
from tooling.progress import report_start, report_progress, report_result, report_error

def my_tool(param1, param2):
    # Report that the tool is starting
    report_start(f"Processing {param1} with {param2}", end="")
    
    try:
        # Do some work...
        intermediate_result = do_work(param1, param2)
        
        # Report progress or intermediate results
        report_progress(f"Processed {len(intermediate_result)} items", end="")
        
        # Report final result
        report_result(f" Completed successfully!")
        
        return {"success": True, "result": intermediate_result}
        
    except Exception as e:
        report_error(f"Failed: {str(e)}")
        return {"success": False, "error": str(e)}
```

### Available Functions

| Function | Purpose | Emoji | Example Output |
|----------|---------|-------|----------------|
| `report_start()` | Indicate tool operation is beginning | ? | `? Processing files...` |
| `report_progress()` | Show ongoing progress | ? | `? Processed 50/100 items` |
| `report_result()` | Report successful completion or intermediate results | ? | `? Found 25 files` |
| `report_error()` | Report errors during execution | ? | `? File not found` |
| `report_warning()` | Report warnings or non-critical issues | ?? | `?? Skipping hidden files` |

### Key Features

1. **End parameter**: All functions accept an `end` parameter (default: `"\n"`) to control line endings. This allows for continuous progress updates on the same line.

2. **Automatic flushing**: Messages are automatically flushed to ensure immediate display.

3. **Fallback support**: The functions include fallback implementations when the module is run directly (without the full package structure).

4. **Tool integration**: Designed to work seamlessly with the existing tool calling system in `openai_cli.py`.

## Example: Enhanced File Listing

The `list_files` tool demonstrates the progress reporting system:

```python
# Before processing
report_start(f"Listing files at {abs_directory} {recursive_str}", end="")

# After processing completes  
report_result(f" ✅ Found {total_found} items ({file_count} files, {dir_count} dirs)")
```

This produces output like:
```
? Listing files at /home/user/project recursively?  Found 31 items (24 files, 7 dirs)
```

## Integration with OpenAI CLI

When used with the OpenAI CLI, progress messages appear in real-time while the tool executes, providing transparency into what the AI agent is doing. The actual tool result is still returned to the AI model as structured JSON, while progress messages are displayed to the user via stderr.

## Best Practices

1. **Use `end=""`** when you want to continue the message on the same line
2. **Always flush** - the system handles this automatically
3. **Keep messages concise** - they should be informative but not overwhelming
4. **Use appropriate message types** - start, progress, result, error, or warning
5. **Don't rely on progress messages for critical information** - the tool's return value is what gets sent to the AI model

## Error Handling

The progress reporting functions are designed to be safe - they won't crash your tool if there are issues with stderr output. However, they should still be used within proper try/except blocks in your tool implementation.