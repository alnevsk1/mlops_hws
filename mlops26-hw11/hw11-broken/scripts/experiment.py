"""Один воспроизводимый прогон через четыре клиентских Pod."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from scripts.loadgen import schedule

ROOT = Path(__file__).resolve().parents[1]


def command(args: list[str]) -> str:
    result = subprocess.run(
        args, cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--service", choices=("normal", "sticky"), default="normal")
    parser.add_argument("--traffic", choices=("uniform", "skew"), default="uniform")
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--rate", type=float, default=1.6)
    parser.add_argument("--chunks", type=int, default=8)
    args = parser.parse_args()
    directory = ROOT / "results" / args.run
    directory.mkdir(parents=True, exist_ok=False)
    start = time.time() + 20
    (directory / "schedule.json").write_text(
        json.dumps(
            {
                **vars(args),
                "start_utc": start,
                "seed": 42,
                "keepalive": False,
                "requests": schedule(
                    args.duration, args.rate, args.traffic, 42, args.chunks
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    kube = ["kubectl", "--context", "kind-mlops-hw11", "-n", "hw11"]
    (directory / "pods-before.json").write_text(
        command(kube + ["get", "pods", "-o", "json"]), encoding="utf-8"
    )
    observer = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.observe",
            "--output",
            str(directory / "timeline.jsonl"),
            "--duration",
            str(args.duration + 60),
        ],
        cwd=ROOT,
    )
    processes = []
    handles = []
    for index in range(4):
        output = (directory / f"client-{index}.jsonl").open("w", encoding="utf-8")
        error = (directory / f"client-{index}.stderr.txt").open("w", encoding="utf-8")
        handles.extend([output, error])
        process = subprocess.Popen(
            kube
            + [
                "exec",
                f"client-{index}",
                "--",
                "/app/.venv/bin/python",
                "-m",
                "scripts.loadgen",
                "--url",
                f"http://hw11-{args.service}:8080/stream",
                "--client",
                str(index),
                "--start",
                str(start),
                "--duration",
                str(args.duration),
                "--rate",
                str(args.rate),
                "--traffic",
                args.traffic,
                "--chunks",
                str(args.chunks),
                "--run",
                args.run,
            ],
            stdout=output,
            stderr=error,
            cwd=ROOT,
        )
        processes.append(process)
    codes = [process.wait() for process in processes]
    for handle in handles:
        handle.close()
    observer.wait()
    (directory / "api-logs.txt").write_text(
        command(kube + ["logs", "-l", "app=hw11-api", "--tail=-1", "--prefix=true"]),
        encoding="utf-8",
    )

    rows = []
    for index in range(4):
        rows.extend(
            json.loads(line)
            for line in (directory / f"client-{index}.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        )
    (directory / "requests.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    summary = {
        "planned": int(args.duration * args.rate),
        "sent": len(rows),
        "exit_codes": codes,
        "outcomes": {
            key: sum(row["outcome"] == key for row in rows)
            for key in ("completed", "rejected", "aborted", "timeout")
        },
    }
    (directory / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary))
    if any(codes) or len(rows) != summary["planned"]:
        raise SystemExit("Incomplete generator output")


if __name__ == "__main__":
    main()
