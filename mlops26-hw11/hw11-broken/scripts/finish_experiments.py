"""Запустить A/C после завершения матрицы B; сохранить итог каждого сценария."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if (ROOT / "results" / "integration-checks.json").exists():
        raise SystemExit("Archive previous integration checks before rerunning")
    deadline = time.monotonic() + 7200
    expected = [
        ROOT / "results" / f"B-main-{traffic}-{policy}-{repeat}" / "summary.json"
        for repeat in range(1, 4)
        for traffic in ("uniform", "skew")
        for policy in ("sticky", "normal")
    ]
    while not all(path.exists() for path in expected):
        if time.monotonic() > deadline:
            raise SystemExit("B matrix incomplete")
        time.sleep(10)
    results = []
    commands = [
        ("warmup", ["scripts.scenarios_a", "warmup"]),
        ("rollout", ["scripts.scenarios_a", "rollout"]),
        ("rollback", ["scripts.scenarios_a", "rollback"]),
        ("recovery", ["scripts.scenarios_a", "recovery"]),
        ("failure", ["scripts.scenarios_a", "failure"]),
        ("hpa", ["scripts.scenario_c"]),
        ("hpa-sticky", ["scripts.scenario_c", "--sticky"]),
    ]
    for name, args in commands:
        path = ROOT / "results" / f"scenario-{name}.txt"
        with path.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                [sys.executable, "-m", *args],
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        results.append(
            {
                "scenario": name,
                "exit_code": result.returncode,
                "finished_utc": time.time(),
            }
        )
        (ROOT / "results" / "integration-checks.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        print(f"{name}: exit={result.returncode}", flush=True)
    for module in ("scripts.export_metrics", "scripts.report"):
        subprocess.run([sys.executable, "-m", module], cwd=ROOT, check=True)
    if any(row["exit_code"] != 0 for row in results):
        raise SystemExit(
            "Some integration scenarios failed; see integration-checks.json"
        )


if __name__ == "__main__":
    main()
