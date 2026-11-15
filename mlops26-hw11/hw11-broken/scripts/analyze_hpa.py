"""Численные доказательства масштабирования из сохранённых наблюдений."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def main() -> None:
    output = {}
    for name in ("C-hpa", "C-hpa-sticky"):
        directory = ROOT / "results" / name
        if not (directory / "downscale.json").exists():
            continue
        rows = read_rows(directory / "requests.jsonl")
        timeline = read_rows(directory / "timeline.jsonl")
        start = min(row["sent_utc"] for row in rows)
        last_send = max(row["sent_utc"] for row in rows)
        initial = json.loads((directory / "pods-before.json").read_text())["items"]
        initial_names = {
            item["metadata"]["name"]
            for item in initial
            if item["metadata"].get("labels", {}).get("app") == "hw11-api"
        }
        pods = {}
        hpa = []
        for sample in timeline:
            for item in sample["kubernetes"].get("items", []):
                if item["kind"] == "HorizontalPodAutoscaler":
                    hpa.append({"utc": sample["utc"], **item.get("status", {})})
                if (
                    item["kind"] != "Pod"
                    or item["metadata"].get("labels", {}).get("app") != "hw11-api"
                ):
                    continue
                pod = item["metadata"]["name"]
                created = datetime.fromisoformat(
                    item["metadata"]["creationTimestamp"]
                ).timestamp()
                entry = pods.setdefault(
                    pod,
                    {
                        "initial": pod in initial_names,
                        "created_after_load_s": created - start,
                        "ready_after_load_s": None,
                    },
                )
                for condition in item.get("status", {}).get("conditions", []):
                    if (
                        condition["type"] == "Ready"
                        and condition["status"] == "True"
                        and entry["ready_after_load_s"] is None
                    ):
                        ready = datetime.fromisoformat(
                            condition["lastTransitionTime"]
                        ).timestamp()
                        entry["ready_after_load_s"] = ready - start
                        entry["creation_to_ready_s"] = ready - created
        distribution = Counter(row["pod"] for row in rows)
        for pod, entry in pods.items():
            entry["requests"] = distribution[pod]
            matches = [row["sent_utc"] for row in rows if row["pod"] == pod]
            entry["first_request_after_load_s"] = (
                min(matches) - start if matches else None
            )
        for sample in json.loads((directory / "downscale.json").read_text()):
            hpa.append({"utc": sample["utc"], **sample["hpa"].get("status", {})})
        first_three = next(
            (item["utc"] for item in hpa if item.get("currentReplicas") == 3), None
        )
        back_two = next(
            (
                item["utc"]
                for item in hpa
                if first_three is not None
                and item["utc"] > first_three
                and item.get("currentReplicas") == 2
            ),
            None,
        )
        output[name] = {
            "current_three_after_load_s": None
            if first_three is None
            else first_three - start,
            "back_two_after_last_send_s": None
            if back_two is None
            else back_two - last_send,
            "observation_note": "HPA times are first observed samples; Pod times use Kubernetes timestamps.",
            "pods": pods,
        }
    (ROOT / "results" / "hpa-analysis.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
