from pathlib import Path
from .config import settings


def storage_path(key: str) -> Path:
    root = settings().storage_root.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Invalid private storage key")
    return path


def write_private(key: str, data: bytes):
    path = storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
