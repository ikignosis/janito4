import inspect

def get_caller_info():
    frame = inspect.currentframe()
    if frame is None:
        return "No frame"
    
    # Print all available frames
    current = frame
    i = 0
    while current is not None and i < 5:
        print(f"Frame {i}: {current.f_code.co_name}")
        current = current.f_back
        i += 1
    
    # Try to get the tool function (should be 2 frames back)
    current = frame
    for _ in range(2):
        if current.f_back is None:
            print("Not enough frames!")
            return ""
        current = current.f_back
    
    return current.f_code.co_name

def report_start_debug(message):
    caller = get_caller_info()
    print(f"Called from: {caller}")

def test_tool():
    report_start_debug("Hello")

test_tool()