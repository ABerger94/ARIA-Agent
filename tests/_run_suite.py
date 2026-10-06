"""Run a pytest-style suite file: execute every test_* function, print RESULT."""
import importlib.util
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("suite", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
tests = sorted(n for n in dir(mod) if n.startswith("test_"))
passed = failed = 0
for name in tests:
    try:
        getattr(mod, name)()
        passed += 1
    except Exception as e:
        failed += 1
        print(f"FAIL {name}: {type(e).__name__}: {e}")
print(f"RESULT {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
