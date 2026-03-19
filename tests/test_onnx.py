import sys
print(f"Testing onnxruntime import...")
try:
    import onnxruntime
    print(f"✓ onnxruntime {onnxruntime.__version__} loaded successfully")
    print(f"  Available providers: {onnxruntime.get_available_providers()}")
except Exception as e:
    print(f"✗ Failed to import onnxruntime: {e}")
    import traceback
    traceback.print_exc()
