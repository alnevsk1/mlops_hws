"""Проверить сдаваемые доказательства, не подменяя их локальными unit-тестами."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def logs(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "{" in line:
            try:
                rows.append(json.loads(line[line.index("{") :]))
            except ValueError:
                pass
    return rows


def main() -> None:
    integrations = load(RESULTS / "integration-checks.json")
    assert len(integrations) == 7 and all(row["exit_code"] == 0 for row in integrations)
    for repeat in range(1, 4):
        for traffic in ("uniform", "skew"):
            reference = None
            for policy in ("sticky", "normal"):
                directory = RESULTS / f"B-main-{traffic}-{policy}-{repeat}"
                schedule = load(directory / "schedule.json")
                rows = records(directory / "requests.jsonl")
                assert schedule["duration"] >= 120
                assert len(rows) == len(schedule["requests"]) == 180
                assert len({row["index"] for row in rows}) == 180
                assert {row["client"] for row in rows} == {0, 1, 2, 3}
                assert load(directory / "summary.json")["exit_codes"] == [0, 0, 0, 0]
                if traffic == "skew":
                    assert sum(row["client"] == 0 for row in rows) == 144
                if reference is None:
                    reference = schedule["requests"]
                else:
                    assert reference == schedule["requests"]
                pods = load(directory / "pods-before.json")["items"]
                ips = {
                    p["status"]["podIP"]
                    for p in pods
                    if p["metadata"]["name"] in {f"client-{i}" for i in range(4)}
                }
                assert len(ips) == 4
                comparison = load(directory / "prometheus-comparison.json")
                assert (
                    comparison["ttfc"]["client_n"] == comparison["ttfc"]["histogram_n"]
                )
    broken = logs(RESULTS / "A-warmup-broken" / "api-logs.txt")
    fixed = logs(RESULTS / "A-warmup-fixed" / "api-logs.txt")
    assert any(
        row.get("event") == "received" and row.get("warmed") is False for row in broken
    )
    assert not any(
        row.get("event") == "received" and row.get("warmed") is False for row in fixed
    )
    assert any(
        row.get("event") == "received" and row.get("model_id") == "sim-B"
        for row in fixed
    )
    rollout = load(RESULTS / "A-rollout" / "summary.json")
    assert rollout["outcomes"]["completed"] == rollout["planned"]
    broken_rollout = load(RESULTS / "A-rollout-broken" / "summary.json")
    assert (
        broken_rollout["outcomes"]["aborted"] + broken_rollout["outcomes"]["timeout"]
        > 0
    )
    assert {
        row["model_id"] for row in records(RESULTS / "A-rollback" / "requests.jsonl")
    } == {"sim-A"}
    snapshot = load(RESULTS / "A-rollback" / "unready.json")["items"]
    excluded = {
        endpoint["targetRef"]["name"]
        for item in snapshot
        if item["kind"] == "EndpointSlice"
        and item["metadata"].get("labels", {}).get("kubernetes.io/service-name")
        == "hw11-normal"
        for endpoint in item.get("endpoints", [])
        if endpoint["conditions"].get("ready") is False
    }
    assert excluded
    assert excluded.isdisjoint(
        {row["pod"] for row in records(RESULTS / "A-rollback" / "requests.jsonl")}
    )
    recovery = load(RESULTS / "A-recovery" / "verification.json")
    assert recovery["before"]["body"]["pod"] != recovery["after"]["body"]["pod"]
    assert (
        recovery["before"]["body"]["artifact_sha256"]
        == recovery["after"]["body"]["artifact_sha256"]
    )
    assert "ALPHA-HISTORY" in recovery["after"]["body"]["answer"]
    assert "ALPHA-HISTORY" not in recovery["other"]["body"]["answer"]
    failed = load(RESULTS / "A-forced-failure" / "summary.json")
    assert failed["outcomes"]["aborted"] + failed["outcomes"]["timeout"] > 0
    assert any(
        row["outcome"] == "aborted" and row["status"] == 200
        for row in records(RESULTS / "A-forced-failure" / "requests.jsonl")
    )
    assert load(RESULTS / "A-forced-failure" / "recovered.json")["status"] == 200
    for name in ("C-hpa", "C-hpa-sticky"):
        samples = records(RESULTS / name / "timeline.jsonl")
        counts = [
            item["status"].get("currentReplicas")
            for row in samples
            for item in row["kubernetes"]["items"]
            if item["kind"] == "HorizontalPodAutoscaler"
        ]
        assert 2 in counts and 3 in counts
        assert (
            load(RESULTS / name / "downscale.json")[-1]["hpa"]["status"][
                "currentReplicas"
            ]
            == 2
        )
    states = {}
    for path in RESULTS.glob("*/timeline.jsonl"):
        for row in records(path):
            for metric in row.get("prometheus", {}).get("data", {}).get("result", []):
                labels = metric["metric"]
                if labels.get("__name__") == "ALERTS":
                    states.setdefault(labels["alertname"], set()).add(
                        labels["alertstate"]
                    )
    for name in ("HighTTFC", "HighRejections"):
        assert {"pending", "firing"} <= states.get(name, set())
        transitions = load(RESULTS / "alert-transitions.json")[name]
        assert any(
            row["pending_utc"] < row["firing_utc"] < row["normal_utc"]
            for row in transitions
        )
    for name in (
        "01_rollout_readiness.png",
        "02_hot_replica.png",
        "03_policy_comparison.png",
        "04_hpa.png",
    ):
        assert (ROOT / "reports" / "figures" / name).exists()
    print(
        "Evidence checks passed: matrix, warmup, rollout, rollback, recovery, failure, HPA, alerts, figures"
    )


if __name__ == "__main__":
    main()
