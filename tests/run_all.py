"""Run every offline test (no network, no SolidWorks):  .venv\\Scripts\\python tests\\run_all.py   (pytest works too, if installed)."""
import contextlib
import importlib
import io
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

failed = passed = 0
for path in sorted(HERE.glob("test_*.py")):
    module = importlib.import_module(path.stem)
    for name, fn in vars(module).items():
        if name.startswith("test_") and callable(fn):
            out = io.StringIO()  # keep server logs / job JSON out of the report; show them only for a failure
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    fn()
                passed += 1
            except Exception:
                failed += 1
                print(f"FAIL {path.stem}.{name}", out.getvalue(), sep="\n")
                traceback.print_exc()
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
