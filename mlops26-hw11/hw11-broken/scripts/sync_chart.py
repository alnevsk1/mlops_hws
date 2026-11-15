"""Скопировать единственный исходный params.yaml и артефакты в Helm package."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    dest = root / "chart" / "files"
    dest.mkdir(parents=True, exist_ok=True)
    for source in (root / "params.yaml", *sorted((root / "artifacts").glob("*.json"))):
        (dest / source.name).write_bytes(source.read_bytes())


if __name__ == "__main__":
    main()
