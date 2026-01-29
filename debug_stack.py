import inspect

def get_caller_info():
    frame = inspect.currentframe()
    if frame is None:
        return "No frame"
    
    # Go back 2 frames
    current = frame
    for i in range(3):
        if current.f_back is None:
            return f"Frame {i} is None"
        current = current.f_back
        print(f"Frame {i+1}: {current.f_code.co_name}")
    
    return current.f_code.co_name

def test_function():
    return get_caller_info()

print("Direct call:", test_function())