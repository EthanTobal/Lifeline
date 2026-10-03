"""Zero-dependency test runner (no pytest needed).

Imports tests/test_backend.py, runs every test_* function, prints a summary,
and exits non-zero on any failure. Usage:  py backend/run_tests.py
"""
import importlib.util
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)  # make `app` importable

TEST_FILE = os.path.join(ROOT, "tests", "test_backend.py")
spec = importlib.util.spec_from_file_location("test_backend", TEST_FILE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

tests = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")]
passed = failed = 0
for fn in tests:
    try:
        fn()
        passed += 1
        print(f"PASS  {fn.__name__}")
    except Exception:
        failed += 1
        print(f"FAIL  {fn.__name__}")
        traceback.print_exc()

print("-" * 40)
print(f"{passed} passed, {failed} failed, {len(tests)} total")
sys.exit(1 if failed else 0)
