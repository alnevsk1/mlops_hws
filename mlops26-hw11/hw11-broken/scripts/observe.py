"""Снимки Kubernetes и Prometheus с UTC; внешний процесс не создаёт нагрузку в API."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--prometheus", default="http://127.0.0.1:9091")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    end = time.monotonic() + args.duration
    with (
        args.output.open("w", encoding="utf-8") as output,
        httpx.Client(timeout=5) as client,
    ):
        while time.monotonic() < end:
            stamp = time.time()
            result = subprocess.run(
                [
                    "kubectl",
                    "--context",
                    "kind-mlops-hw11",
                    "-n",
                    "hw11",
                    "get",
                    "pods,endpointslices,hpa",
                    "-o",
                    "json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            row = {
                "utc": stamp,
                "kubernetes": json.loads(result.stdout)
                if result.returncode == 0
                else result.stderr,
            }
            try:
                row["prometheus"] = client.get(
                    f"{args.prometheus}/api/v1/query",
                    params={
                        "query": (
                            '{__name__=~"hw11_running|hw11_waiting|hw11_ready|ALERTS"}'
                        )
                    },
                ).json()
            except httpx.HTTPError as exc:
                row["prometheus_error"] = str(exc)
            output.write(json.dumps(row) + "\n")
            output.flush()
            time.sleep(2)


if __name__ == "__main__":
    main()
