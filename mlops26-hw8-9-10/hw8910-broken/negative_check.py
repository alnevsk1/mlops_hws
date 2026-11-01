"""Намеренная мутация кеша для демонстрации красного быстрого гейта."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

import release


def broken_cache_key(data_path: Path, config: dict) -> str:
    return hashlib.sha256(str(data_path).encode("utf-8")).hexdigest()


def main() -> None:
    release.cache_key = broken_cache_key
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_release.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
