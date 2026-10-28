"""Where the app keeps things on disk, and writing files safely."""

import os
import tempfile

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "golf-cli")
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "golf-cli")


def write_atomic(path, data):
    """Write via a temporary file in the same folder, so the file at path is
    always either absent or complete, even if the write is interrupted."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
