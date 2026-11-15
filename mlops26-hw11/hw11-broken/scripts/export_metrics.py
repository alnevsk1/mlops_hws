"""Сохранить Prometheus до/после и сравнить одну популяцию ответов с клиентом."""

from __future__ import annotations

import json
import math
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    data = sorted(values)
    rank = (len(data) - 1) * q
    lo = math.floor(rank)
    hi = math.ceil(rank)
    return data[lo] + (data[hi] - data[lo]) * (rank - lo)


def histogram_quantile(buckets: list[tuple[float, float]], q: float) -> float | None:
    if not buckets or buckets[-1][1] <= 0:
        return None
    target = q * buckets[-1][1]
    low_bound = low_count = 0.0
    for bound, count in buckets:
        if count >= target:
            if math.isinf(bound):
                return low_bound
            if count == low_count:
                return bound
            return low_bound + (bound - low_bound) * (target - low_count) / (
                count - low_count
            )
        low_bound, low_count = bound, count
    return None


def query(client: httpx.Client, expression: str, stamp: float) -> dict:
    response = client.get("/api/v1/query", params={"query": expression, "time": stamp})
    response.raise_for_status()
    body = response.json()
    if body["status"] != "success":
        raise RuntimeError(body)
    return body


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:9091", timeout=30) as client:
        for path in sorted((ROOT / "results").glob("*/requests.jsonl")):
            directory = path.parent
            if (directory / "prometheus-comparison.json").exists():
                continue
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            if not rows:
                continue
            # Выбираем только Pod, действительно обслуживавшие этот прогон.
            pods = "|".join(sorted({row["pod"] for row in rows if row["pod"]}))
            start = min(row["sent_utc"] for row in rows) - 1
            end = max(row["end_utc"] for row in rows) + 10
            results = {}
            for metric, field in (
                ("ttfc", "ttfc_seconds"),
                ("duration", "duration_seconds"),
            ):
                expression = (
                    f'sum by(le) (hw11_{metric}_seconds_bucket{{pod=~"{pods}"}})'
                )
                before = query(client, expression, start)
                after = query(client, expression, end)

                def unpack(body: dict) -> dict[float, float]:
                    return {
                        float(item["metric"]["le"]): float(item["value"][1])
                        for item in body["data"]["result"]
                    }

                first, last = unpack(before), unpack(after)
                buckets = sorted(
                    (bound, count - first.get(bound, 0))
                    for bound, count in last.items()
                )
                successful = [
                    row[field] for row in rows if row["outcome"] == "completed"
                ]
                results[metric] = {
                    "query": expression,
                    "start_utc": start,
                    "end_utc": end,
                    "before": before,
                    "after": after,
                    "client_n": len(successful),
                    "histogram_n": buckets[-1][1] if buckets else 0,
                    "population_matches": bool(buckets)
                    and buckets[-1][1] == len(successful),
                    "scope": "Compare stable B runs; restarts can reset counters in A/C",
                    "client_p50": percentile(successful, 0.5),
                    "client_p95": percentile(successful, 0.95),
                    "histogram_p50": histogram_quantile(buckets, 0.5),
                    "histogram_p95": histogram_quantile(buckets, 0.95),
                    "promql_example": f"histogram_quantile(0.95, sum by(le,pod)(rate(hw11_{metric}_seconds_bucket[2m])))",
                }
            (directory / "prometheus-comparison.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )
            range_query = (
                '{__name__=~"hw11_running|hw11_waiting|hw11_cache_total|ALERTS"}'
            )
            response = client.get(
                "/api/v1/query_range",
                params={"query": range_query, "start": start, "end": end, "step": "5"},
            )
            response.raise_for_status()
            (directory / "prometheus-range.json").write_text(
                json.dumps(response.json()), encoding="utf-8"
            )
            print(
                f"{directory.name}: client n={results['ttfc']['client_n']}, histogram n={results['ttfc']['histogram_n']}"
            )


if __name__ == "__main__":
    main()
