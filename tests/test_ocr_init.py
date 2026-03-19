import sys
sys.path.insert(0, r'F:\AI\GF2TableToolkit')

print("Testing OCR module initialization...")
try:
    from core.ocr import _get_digit, _get_rapid
    
    print("\nTrying to initialize ddddocr...")
    ocr_digit = _get_digit()
    print(f"✓ ddddocr initialized successfully")
    
    print("\nTrying to initialize RapidOCR...")
    ocr_rapid = _get_rapid()
    print(f"✓ RapidOCR initialized successfully")
    
    print("\n✓ All OCR modules initialized without errors!")
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
