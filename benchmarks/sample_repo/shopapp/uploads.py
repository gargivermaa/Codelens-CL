"""Product image uploads."""
import os
from pathlib import Path

UPLOAD_ROOT = Path("/var/shopapp/uploads")
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def safe_upload_path(filename: str) -> Path:
    name = os.path.basename(filename)
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("unsupported file type")
    path = (UPLOAD_ROOT / name).resolve()
    if UPLOAD_ROOT.resolve() not in path.parents:
        raise ValueError("invalid file name")
    return path


def save_upload(filename: str, data: bytes) -> Path:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("file too large")
    path = safe_upload_path(filename)
    path.write_bytes(data)
    return path
