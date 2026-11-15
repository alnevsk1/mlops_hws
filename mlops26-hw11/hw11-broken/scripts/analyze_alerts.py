"""Сохранить переходы алертов pending → firing → normal из сырых снимков."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    output = {}
    for name in ("HighTTFC", "HighRejections"):
        examples = []
        for path in sorted((ROOT / "results").glob("*/timeline.jsonl")):
            pending = firing = normal = None
            for line in path.read_text(encoding="utf-8").splitlines():
                sample = json.loads(line)
                body = sample.get("prometheus", {})
                if body.get("status") != "success":
                    continue
                metrics = body.get("data", {}).get("result", [])
                states = {
                    item["metric"].get("alertstate")
                    for item in metrics
                    if item["metric"].get("__name__") == "ALERTS"
                    and item["metric"].get("alertname") == name
                }
                if not states and firing is None:
                    pending = None
                if "pending" in states and pending is None:
                    pending = sample["utc"]
                if "firing" in states and pending is not None and firing is None:
                    firing = sample["utc"]
                ready = any(
                    item["metric"].get("__name__") == "hw11_ready"
                    and float(item["value"][1]) == 1
                    for item in metrics
                )
                if firing is not None and not states and ready:
                    normal = sample["utc"]
                    break
            if normal is not None:
                examples.append(
                    {
                        "run": path.parent.name,
                        "pending_utc": pending,
                        "firing_utc": firing,
                        "normal_utc": normal,
                        "observed_hold_s": firing - pending,
                    }
                )
        output[name] = examples
        assert examples, f"No complete transition recorded for {name}"
    (ROOT / "results" / "alert-transitions.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
