from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _safe_rmtree(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def pytest_sessionfinish(session, exitstatus) -> None:
    """Remove local pytest artifacts created inside the repo after test runs."""
    cleanup_paths = [
        ROOT / ".pytest_tmp",
        ROOT / ".pytest_cache",
        ROOT / "__pycache__",
        ROOT / "bot" / "__pycache__",
        ROOT / "scripts" / "__pycache__",
        ROOT / "tests" / "__pycache__",
    ]
    for path in cleanup_paths:
        _safe_rmtree(path)

    for pattern in ("pytest-tmp-run*", "pytest-tmp-*"):
        for path in ROOT.glob(pattern):
            if path.is_dir():
                _safe_rmtree(path)
