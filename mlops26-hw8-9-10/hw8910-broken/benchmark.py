"""Небольшой клиентский замер одного принятого API после прогрева."""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

PROJECT = Path(__file__).resolve().parent


def send(url: str, prompt: str, limit: int, mode: int, number: int) -> dict:
    started = time.perf_counter()
    body = json.dumps({"prompt": prompt, "max_new_tokens": limit}).encode("utf-8")
    request = Request(
        f"{url}/generate",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=180) as response:
            result = json.load(response)
            status = response.status
        error = None
    except HTTPError as exc:
        status = exc.code
        result = {}
        error = exc.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError) as exc:
        status = None
        result = {}
        error = str(exc)
    return {
        "id": f"{mode}-{number}",
        "concurrency": mode,
        "status": status,
        "error": error,
        "client_seconds": time.perf_counter() - started,
        "generation_seconds": result.get("generation_seconds"),
        "model_id": result.get("model_id"),
        "request_id": result.get("request_id"),
        "output_tokens": result.get("output_tokens"),
    }


def run_batch(
    url: str, prompt: str, limit: int, n: int, concurrency: int
) -> tuple[list[dict], dict]:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(send, url, prompt, limit, concurrency, index)
            for index in range(n)
        ]
        rows = [future.result() for future in as_completed(futures)]
    elapsed = time.perf_counter() - started
    rows.sort(key=lambda row: row["id"])
    successes = [row for row in rows if row["status"] == 200]
    summary = {
        "concurrency": concurrency,
        "n": n,
        "successes": len(successes),
        "errors": n - len(successes),
        "batch_seconds": elapsed,
        "successful_requests_per_second": len(successes) / elapsed,
        "mean_client_seconds": mean(row["client_seconds"] for row in successes)
        if successes
        else None,
        "max_client_seconds": max(
            (row["client_seconds"] for row in successes), default=None
        ),
    }
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--limit", type=int, default=32)
    parser.add_argument("--output", type=Path, default=PROJECT / "reports")
    args = parser.parse_args()
    params = yaml.safe_load((PROJECT / "params.yaml").read_text(encoding="utf-8"))
    previous = (PROJECT / params["previous_project"]).resolve()
    golden = previous / params["evaluation"]["golden"]
    prompt = json.loads(golden.read_text(encoding="utf-8").splitlines()[0])["input"]
    with urlopen(f"{args.url}/ready", timeout=10) as response:
        if not json.load(response)["ready"]:
            raise RuntimeError("Сервис не готов")
    rows = []
    summaries = []
    for concurrency in (1, 2):
        batch_rows, summary = run_batch(
            args.url, prompt, args.limit, args.n, concurrency
        )
        rows.extend(batch_rows)
        summaries.append(summary)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "bench-raw.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (args.output / "bench-summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summaries, ensure_ascii=False))


if __name__ == "__main__":
    main()
