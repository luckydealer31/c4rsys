from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def files_root(settings: Any, tmp_path: Path) -> Path:
    """Uploaded files of a test go to its own folder (A10)."""
    root = tmp_path / "files"
    settings.C4R_FILES_ROOT = str(root)
    return root
