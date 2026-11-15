"""Выделить наблюдения «очередь на одном Pod, свободный слот на другом»."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    output = {}
    for path in sorted((ROOT / "results").glob("B-main-*/timeline.jsonl")):
        if not (path.parent / "summary.json").exists():
            continue
        config = json.loads((path.parent / "schedule.json").read_text())
        begin = config["start_utc"]
        end = begin + config["duration"]
        observed = hot_idle = 0
        maximum = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if not begin <= row["utc"] < end:
                continue
            states = {}
            for item in row.get("prometheus", {}).get("data", {}).get("result", []):
                labels = item["metric"]
                if labels.get("__name__") not in ("hw11_running", "hw11_waiting"):
                    continue
                states.setdefault(labels["pod"], {})[labels["__name__"]] = float(
                    item["value"][1]
                )
            if len(states) != 2 or any(len(values) != 2 for values in states.values()):
                continue
            observed += 1
            if any(value["hw11_waiting"] > 0 for value in states.values()) and any(
                value["hw11_running"] == 0 and value["hw11_waiting"] == 0
                for value in states.values()
            ):
                hot_idle += 1
            for pod, values in states.items():
                maximum[pod] = max(maximum.get(pod, 0), values["hw11_waiting"])
        output[path.parent.name] = {
            "measurement_samples": observed,
            "queued_and_other_idle_samples": hot_idle,
            "queued_and_other_idle_fraction": hot_idle / observed if observed else None,
            "max_waiting_by_pod": maximum,
        }
    (ROOT / "results" / "queue-diagnostics.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
