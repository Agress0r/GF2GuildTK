import sys
print(f"Testing ddddocr...")
try:
    import ddddocr
    print(f"✓ ddddocr loaded successfully")
except Exception as e:
    print(f"✗ Failed to import ddddocr: {e}")
    import traceback
    traceback.print_exc()

print(f"\nTesting rapidocr-onnxruntime...")
try:
    from rapidocr_onnxruntime import RapidOCR
    print(f"✓ rapidocr-onnxruntime loaded successfully")
except Exception as e:
    print(f"✗ Failed to import rapidocr-onnxruntime: {e}")
    import traceback
    traceback.print_exc()
