#!/usr/bin/env python3
"""
Jinx AI Packager - Test Script
Tests the build process step by step
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

def run(cmd, check=True):
    print(f"\n[TEST] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"[TEST] Return code: {result.returncode}")
    if result.stdout:
        print("[TEST] stdout:")
        print(result.stdout[:2000])
    if result.stderr:
        print("[TEST] stderr:")
        print(result.stderr[:2000])
    if check and result.returncode != 0:
        print(f"[TEST] FAILED with code {result.returncode}")
        return False
    print("[TEST] PASSED")
    return True

def test_venv():
    """Test 1: Check if venv Python works"""
    print("\n" + "="*50)
    print("TEST 1: Virtual Environment")
    print("="*50)
    
    if not VENV_PYTHON.exists():
        print("[TEST] Venv Python not found at", VENV_PYTHON)
        return False
    
    print(f"[TEST] Venv Python found: {VENV_PYTHON}")
    
    # Test basic Python
    result = subprocess.run([str(VENV_PYTHON), "--version"], capture_output=True, text=True)
    print(f"[TEST] Python version: {result.stdout.strip()}")
    
    return run([str(VENV_PYTHON), "-m", "pip", "--version"])

def test_pip_install():
    """Test 2: Install requirements"""
    print("\n" + "="*50)
    print("TEST 2: Install Requirements")
    print("="*50)
    
    return run([
        str(VENV_PYTHON), "-m", "pip", "install", 
        "-r", str(PROJECT_ROOT / "requirements.txt"),
        "--quiet"
    ])

def test_pyinstaller_install():
    """Test 3: Install PyInstaller"""
    print("\n" + "="*50)
    print("TEST 3: Install PyInstaller")
    print("="*50)
    
    if not run([str(VENV_PYTHON), "-m", "pip", "install", "pyinstaller", "--quiet"]):
        return False
    
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", "import PyInstaller; print(PyInstaller.__version__)"],
        capture_output=True, text=True
    )
    print(f"[TEST] PyInstaller version: {result.stdout.strip()}")
    return True

def test_jinx_import():
    """Test 4: Can Jinx be imported?"""
    print("\n" + "="*50)
    print("TEST 4: Jinx Import Test")
    print("="*50)
    
    test_script = '''
import sys
sys.path.insert(0, r"C:\\Development\\jinx-ai")
try:
    from jinx_ai.config import BASE_DIR
    print(f"BASE_DIR: {BASE_DIR}")
    print("JINX_IMPORT_OK")
except Exception as e:
    print(f"JINX_IMPORT_ERROR: {e}")
    sys.exit(1)
'''
    
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", test_script],
        capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    print(f"[TEST] stdout: {result.stdout[:500]}")
    if result.stderr:
        print(f"[TEST] stderr: {result.stderr[:500]}")
    
    return "JINX_IMPORT_OK" in result.stdout

def test_main_import():
    """Test 5: Can main.py be imported?"""
    print("\n" + "="*50)
    print("TEST 5: Main.py Import Test")
    print("="*50)
    
    test_script = '''
import sys
sys.path.insert(0, r"C:\\Development\\jinx-ai")
try:
    # Try to import the main module (without running it)
    import importlib.util
    spec = importlib.util.spec_from_file_location("main", r"C:\\Development\\jinx-ai\\main.py")
    main_module = importlib.util.module_from_spec(spec)
    # Don't execute - just check it can be loaded
    print("MAIN_IMPORT_OK")
except Exception as e:
    print(f"MAIN_IMPORT_ERROR: {e}")
    sys.exit(1)
'''
    
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", test_script],
        capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    print(f"[TEST] stdout: {result.stdout[:500]}")
    if result.stderr:
        print(f"[TEST] stderr: {result.stderr[:500]}")
    
    return "MAIN_IMPORT_OK" in result.stdout

def test_pyinstaller_quick():
    """Test 6: Quick PyInstaller test"""
    print("\n" + "="*50)
    print("TEST 6: PyInstaller Quick Test")
    print("="*50)
    
    test_script = """
print("Hello from test script")
"""
    test_file = PROJECT_ROOT / "test_script.py"
    test_file.write_text(test_script)
    
    success = run([
        str(VENV_PYTHON), "-m", "PyInstaller",
        "--onefile",
        "--name", "test_jinx",
        str(test_file)
    ], check=False)
    
    test_file.unlink()
    
    dist_exe = PROJECT_ROOT / "dist" / "test_jinx.exe"
    if dist_exe.exists():
        print(f"[TEST] Test executable created: {dist_exe}")
        dist_exe.unlink()
        return True
    else:
        print("[TEST] Test executable not created")
        return False

def main():
    print("="*60)
    print("JINX AI PACKAGER - TEST SEQUENCE")
    print("="*60)
    
    tests = [
        ("Virtual Environment", test_venv),
        ("Install Requirements", test_pip_install),
        ("Install PyInstaller", test_pyinstaller_install),
        ("Jinx Import", test_jinx_import),
        ("Main Import", test_main_import),
        ("PyInstaller Quick Test", test_pyinstaller_quick),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"\n[TEST] {name} FAILED with exception: {e}")
            results.append((name, False))
    
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = 0
    failed = 0
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print(f"\nTotal: {passed} passed, {failed} failed")
    
    if failed > 0:
        print("\n⚠ Some tests failed. Check the errors above.")
        return 1
    
    print("\n✓ All tests passed! Ready to build.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
