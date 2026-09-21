#!/usr/bin/env python3
"""Build a milestone release ZIP from the working tree.

Ships source, tests, docs, config examples, manifests and lockfiles. Excludes
version control, virtual environments, dependency and build caches, databases,
vector stores, model files, secrets, logs, QA artifacts and generated runtime
data. `.env.example` is deliberately retained.

Usage: python scripts/make_release_zip.py <zip-name> [output-dir]
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", ".next", "out", "dist",
    "build", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".turbo", ".cache", ".idea", ".vscode", "chroma", ".chroma", "coverage",
    "htmlcov", ".eggs", ".tox", "playwright-report", "test-results",
    "screenshots", ".playwright",
}

EXCLUDED_SUFFIXES = {
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".sqlite3-journal",
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".log", ".tmp", ".temp",
    ".swp", ".swo", ".bak", ".orig", ".rej", ".pem", ".key", ".crt", ".p12",
    ".pfx", ".onnx", ".bin", ".pt", ".pth", ".safetensors", ".gguf", ".ckpt",
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".png", ".jpg", ".jpeg",
    ".webm", ".mp4", ".mov",
}

# Runtime data directories: databases, vector stores and their WAL/SHM
# sidecars are generated state, never release content.
EXCLUDED_DIR_PATHS = {
    "backend/data",
}

# SQLite sidecars do not have a clean suffix (`foo.sqlite3-wal`), so they are
# matched as patterns rather than suffixes.
EXCLUDED_PATTERNS = (
    "-wal", "-shm", "-journal", ".sqlite3-", ".db-",
)

EXCLUDED_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    ".DS_Store", "Thumbs.db", ".netrc", ".git-credentials", "credentials.json",
    "secrets.json", "token.json", "npm-debug.log", "yarn-error.log",
}

# Frontend ships generated landing frames as .webp; they are product assets,
# not QA artifacts, so image exclusions must not swallow them.
KEEP_SUFFIXES = {".webp", ".svg", ".ico"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    posix = rel.as_posix()
    if any(posix == d or posix.startswith(f"{d}/") for d in EXCLUDED_DIR_PATHS):
        return False
    if any(pattern in path.name for pattern in EXCLUDED_PATTERNS):
        return False
    if path.name in EXCLUDED_NAMES:
        return False
    if path.name == ".env.example":
        return True
    if path.suffix.lower() in KEEP_SUFFIXES:
        return True
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    name = sys.argv[1]
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name

    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and included(p))
    if target.exists():
        target.unlink()

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for path in files:
            archive.write(path, Path(name).stem / path.relative_to(ROOT))

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip()
        count = len(archive.namelist())

    print(f"archive: {target}")
    print(f"files:   {count}")
    print(f"size:    {target.stat().st_size:,} bytes")
    print(f"crc:     {'OK' if bad is None else f'CORRUPT at {bad}'}")
    print(f"sha256:  {digest}")
    return 0 if bad is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
