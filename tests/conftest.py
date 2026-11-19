import tempfile
from pathlib import Path

import pytest

# macOS limits a Unix socket path to about 104 bytes, and pytest's tmp_path can be longer.
MAX_SOCKET_PATH = 100


@pytest.fixture
def socket_path() -> Path:
    """A socket path in a fresh private (mode 700) folder that is short enough for macOS."""
    path = Path(tempfile.mkdtemp(prefix="mg-")) / "d.sock"
    if len(str(path)) > MAX_SOCKET_PATH:
        path = Path(tempfile.mkdtemp(prefix="mg-", dir="/tmp")) / "d.sock"
    return path
