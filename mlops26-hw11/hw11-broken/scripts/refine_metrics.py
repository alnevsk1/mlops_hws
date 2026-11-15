"""Отдельная проверка уточнённых bucket после основных сравнений."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.scenarios_a import experiment, finish, upgrade

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    upgrade("model=A")
    name = "metrics-refined-buckets"
    process = experiment(name, 120, 1.5, 8, service="sticky", traffic="skew")
    finish(process, name)
    subprocess.run(
        [sys.executable, "-m", "scripts.export_metrics"], cwd=ROOT, check=True
    )


if __name__ == "__main__":
    main()
