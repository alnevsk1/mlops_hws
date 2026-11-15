"""HPA 2→3→2 и алерты на управляемой вычислительной нагрузке."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from scripts.scenarios_a import experiment, finish, kube, upgrade

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sticky", action="store_true")
    args = parser.parse_args()
    upgrade(
        "model=A",
        "hpa.enabled=true",
        "backend.hit_cpu_seconds=0.3",
        "backend.miss_cpu_seconds=0.4",
        "backend.chunk_cpu_seconds=0.01",
        "backend.chunk_delay_seconds=0.02",
    )
    name = "C-hpa-sticky" if args.sticky else "C-hpa"
    process = experiment(
        name,
        180,
        5,
        8,
        service="sticky" if args.sticky else "normal",
        traffic="skew" if args.sticky else "uniform",
    )
    summary = finish(process, name)
    directory = ROOT / "results" / name
    samples = []
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        hpa = json.loads(kube("get", "hpa", "hw11-api", "-o", "json"))
        samples.append(
            {
                "utc": time.time(),
                "hpa": hpa,
                "pods": json.loads(
                    kube("get", "pods", "-l", "app=hw11-api", "-o", "json")
                ),
            }
        )
        if hpa.get("status", {}).get("currentReplicas") == 2:
            break
        time.sleep(5)
    (directory / "downscale.json").write_text(
        json.dumps(samples, indent=2), encoding="utf-8"
    )
    assert samples[-1]["hpa"]["status"]["currentReplicas"] == 2
    rows = [
        json.loads(line)
        for line in (directory / "timeline.jsonl").read_text().splitlines()
    ]
    assert any(
        item.get("status", {}).get("currentReplicas") == 3
        for row in rows
        for item in row["kubernetes"].get("items", [])
        if item["kind"] == "HorizontalPodAutoscaler"
    ), "HPA never reached 3"
    print(json.dumps(summary))
    subprocess.run(
        ["kubectl", "--context", "kind-mlops-hw11", "top", "pods", "-n", "hw11"],
        check=True,
    )


if __name__ == "__main__":
    main()
