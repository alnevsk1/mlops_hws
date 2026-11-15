"""Open-loop нагрузка; запускается в четырёх Pod с разными IP."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

import httpx
import yaml


def schedule(
    duration: float, rate: float, traffic: str, seed: int, chunks: int
) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for index in range(int(duration * rate)):
        client = (
            index % 4
            if traffic == "uniform"
            else (0 if index % 20 < 16 else 1 + index % 3)
        )
        rows.append(
            {
                "index": index,
                "offset": index / rate,
                "client": client,
                "prompt": f"request-{index} " + "x" * rng.randrange(16, 160),
                "prefix": f"prefix-client-{client}",
                "max_chunks": chunks,
            }
        )
    return rows


async def attempt(
    client: httpx.AsyncClient,
    url: str,
    item: dict,
    start: float,
    timeout: float,
    run: str,
) -> dict:
    target = start + item["offset"]
    await asyncio.sleep(max(0, target - time.time()))
    sent = time.perf_counter()
    sent_utc = time.time()
    row = {
        **item,
        "run": run,
        "request_id": f"{run}-{item['index']}",
        "planned_utc": target,
        "sent_utc": sent_utc,
        "sent_mono": sent,
        "first_mono": None,
        "end_mono": None,
        "status": None,
        "outcome": "aborted",
        "chunks_received": 0,
        "pod": None,
        "model_id": None,
        "ttfc_seconds": None,
        "inter_chunk_seconds": [],
        "send_lag_seconds": sent_utc - target,
        "attempt": 1,
    }
    previous = None
    done = False
    try:
        async with asyncio.timeout(timeout):
            async with client.stream(
                "POST",
                url,
                json={key: item[key] for key in ("prompt", "prefix", "max_chunks")},
                headers={"x-request-id": row["request_id"]},
            ) as response:
                row["status"] = response.status_code
                row["pod"] = response.headers.get("x-pod-name")
                row["model_id"] = response.headers.get("x-model-id")
                if response.status_code != 200:
                    body = await response.aread()
                    try:
                        identity = json.loads(body)
                        row["pod"] = identity.get("pod")
                        row["model_id"] = identity.get("model_id")
                    except ValueError:
                        pass
                    row["outcome"] = "rejected"
                else:
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if event.get("request_id") != row["request_id"]:
                            raise ValueError("Mismatched request ID")
                        if event["event"] == "chunk":
                            now = time.perf_counter()
                            if row["first_mono"] is None:
                                row["first_mono"] = now
                                row["ttfc_seconds"] = now - sent
                            if previous is not None:
                                row["inter_chunk_seconds"].append(now - previous)
                            previous = now
                            row["chunks_received"] += 1
                        elif event["event"] == "done":
                            done = (
                                event["chunks"]
                                == row["chunks_received"]
                                == item["max_chunks"]
                            )
                    row["outcome"] = "completed" if done else "aborted"
    except (TimeoutError, httpx.TimeoutException) as exc:
        row["outcome"] = "timeout"
        row["error"] = type(exc).__name__
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        row["outcome"] = "aborted"
        row["error"] = type(exc).__name__
    row["end_mono"] = time.perf_counter()
    row["end_utc"] = time.time()
    row["duration_seconds"] = row["end_mono"] - sent
    return row


async def run(args: argparse.Namespace) -> None:
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))["load"]
    rows = schedule(args.duration, args.rate, args.traffic, config["seed"], args.chunks)
    selected = [row for row in rows if row["client"] == args.client]
    # Нулевой keepalive: каждый запрос заново проходит выбор backend в kube-proxy.
    limits = httpx.Limits(max_connections=128, max_keepalive_connections=0)
    async with httpx.AsyncClient(
        limits=limits, timeout=config["timeout_seconds"], trust_env=False
    ) as client:
        tasks = [
            asyncio.create_task(
                attempt(
                    client,
                    args.url,
                    row,
                    args.start,
                    config["timeout_seconds"],
                    args.run,
                )
            )
            for row in selected
        ]
        for task in asyncio.as_completed(tasks):
            print(json.dumps(await task, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("params.yaml"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--client", type=int, choices=range(4), required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--rate", type=float, default=1.6)
    parser.add_argument("--traffic", choices=("uniform", "skew"), default="uniform")
    parser.add_argument("--chunks", type=int, default=8)
    parser.add_argument("--run", required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
