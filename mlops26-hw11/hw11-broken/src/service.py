"""Продолжение фабрики API ДЗ 8–10: управляемый CPU backend и streaming."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

LOGGER = logging.getLogger(__name__)


def cpu_work(seconds: float) -> None:
    """Потребить CPU-время текущего потока, не блокируя event loop."""
    until = time.thread_time() + seconds
    block = b"hw11-controlled-cpu" * 4096
    while time.thread_time() < until:
        hashlib.sha256(block).digest()


class SimulatedBackend:
    def __init__(self, config: dict):
        self.config = config
        self.cache: OrderedDict[str, None] = OrderedDict()
        data = Path(config["artifact"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != config["artifact_sha256"]:
            raise ValueError("Artifact SHA-256 mismatch")
        self.loaded_id: str = json.loads(data)["version"]
        self.artifact_sha256: str = hashlib.sha256(data).hexdigest()

    async def prefill(self, prefix: str) -> bool:
        key = hashlib.sha256(prefix.encode()).hexdigest()
        hit = key in self.cache
        cost = self.config["hit_cpu_seconds" if hit else "miss_cpu_seconds"]
        await asyncio.to_thread(cpu_work, cost)
        self.cache[key] = None
        self.cache.move_to_end(key)
        while len(self.cache) > self.config["cache_entries"]:
            self.cache.popitem(last=False)
        return hit


class ServiceState:
    def __init__(self, config: dict):
        self.config = config
        self.backend: SimulatedBackend | None = None
        self.pod: str = os.environ.get("POD_NAME", "local")
        self.warmed: bool = False
        self.draining: bool = False
        self.running: int = 0
        self.waiting: int = 0
        self.semaphore = asyncio.Semaphore(config["backend"]["max_running"])
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "hw11_requests", "Terminal outcomes", ["outcome"], registry=self.registry
        )
        self.active = Gauge("hw11_running", "Running requests", registry=self.registry)
        self.queued = Gauge("hw11_waiting", "Waiting requests", registry=self.registry)
        self.ready_metric = Gauge(
            "hw11_ready", "Warm and accepting", registry=self.registry
        )
        self.ttfc = Histogram(
            "hw11_ttfc_seconds",
            "TTFC of completed responses",
            buckets=config["metrics"]["buckets"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "hw11_duration_seconds",
            "Completed response duration",
            buckets=config["metrics"]["buckets"],
            registry=self.registry,
        )
        self.cache_hits = Counter(
            "hw11_cache",
            "Local simulated prefix cache",
            ["result"],
            registry=self.registry,
        )

    def identity(self, request_id: str) -> dict:
        return {
            "request_id": request_id,
            "pod": self.pod,
            "model_id": self.backend.loaded_id if self.backend else None,
            "artifact_sha256": self.backend.artifact_sha256 if self.backend else None,
            "backend_mode": self.config["backend"]["mode"],
        }

    def available(self) -> bool:
        return not self.draining and (
            self.warmed or self.config["backend"]["early_ready"]
        )

    def log(self, event: str, request_id: str, **values: object) -> None:
        LOGGER.info(
            json.dumps(
                {
                    "utc": time.time(),
                    "event": event,
                    **self.identity(request_id),
                    **values,
                },
                ensure_ascii=False,
            )
        )


def create_app(config: dict) -> FastAPI:
    state = ServiceState(config)
    backend_config = config["backend"]
    api = config["api"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.backend = SimulatedBackend(backend_config)

        async def warmup() -> None:
            await asyncio.sleep(backend_config["warmup_seconds"])
            if not backend_config["never_ready"]:
                state.warmed = True
                state.ready_metric.set(1)
                state.log("warmup_finished", "")

        task = asyncio.create_task(warmup())
        state.log("loaded", "")
        try:
            yield
        finally:
            state.draining = True
            state.ready_metric.set(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.state.service = state

    @app.get("/health")
    async def health() -> dict:
        return {"alive": True}

    @app.get("/startup")
    async def startup() -> Response:
        ok = state.warmed or backend_config["early_ready"]
        return JSONResponse({"started": ok}, status_code=200 if ok else 503)

    @app.get("/ready")
    async def ready() -> Response:
        return JSONResponse(
            {"ready": state.available(), "warmed": state.warmed},
            status_code=200 if state.available() else 503,
        )

    @app.get("/debug")
    async def debug() -> dict:
        return {
            **state.identity(""),
            "warmed": state.warmed,
            "running": state.running,
            "waiting": state.waiting,
            "draining": state.draining,
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            generate_latest(state.registry), media_type="text/plain; version=0.0.4"
        )

    @app.get("/drain")
    async def drain() -> dict:
        state.draining = True
        state.ready_metric.set(0)
        await asyncio.sleep(api["drain_seconds"])
        return {"draining": True}

    async def handle(request: Request, streaming: bool) -> Response:
        started = time.perf_counter()
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:128]
        identity = state.identity(request_id)

        def reject(status: int, reason: str) -> Response:
            state.requests.labels("rejected").inc()
            state.log("rejected", request_id, status=status, reason=reason)
            return JSONResponse({**identity, "detail": reason}, status_code=status)

        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            return reject(422, "Invalid JSON")
        if not isinstance(payload, dict):
            return reject(422, "Object required")
        prompt = payload.get("prompt")
        history = payload.get("history", [])
        chunks = payload.get(
            "max_chunks", payload.get("max_new_tokens", api["default_chunks"])
        )
        prefix = payload.get("prefix", "")
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or type(chunks) is not int
            or not 1 <= chunks <= api["max_chunks"]
            or not isinstance(prefix, str)
            or not isinstance(history, list)
            or any(not isinstance(item, str) for item in history)
        ):
            return reject(422, "Invalid prompt/history/chunk limit")
        answer = json.dumps({"history": history, "prompt": prompt}, ensure_ascii=False)
        if len(answer) + len(prefix) > api["max_prompt_chars"]:
            return reject(422, "Input too long")
        if not state.available():
            return reject(503, "Not ready")
        state.log(
            "received",
            request_id,
            warmed=state.warmed,
            client_ip=request.client.host if request.client else None,
        )
        if not state.warmed:
            return reject(503, "Cold backend (intentional readiness defect)")
        if (
            state.running + state.waiting
            >= backend_config["max_running"] + backend_config["max_waiting"]
        ):
            return reject(429, "Queue full")
        state.waiting += 1
        state.queued.set(state.waiting)
        try:
            await asyncio.wait_for(
                state.semaphore.acquire(), backend_config["queue_timeout_seconds"]
            )
        except TimeoutError:
            return reject(429, "Queue timeout")
        finally:
            state.waiting -= 1
            state.queued.set(state.waiting)
        state.running += 1
        state.active.set(state.running)

        async def events() -> AsyncIterator[dict]:
            completed = False
            first = None
            try:
                assert state.backend is not None
                hit = await state.backend.prefill(prefix)
                state.cache_hits.labels("hit" if hit else "miss").inc()
                for index in range(chunks):
                    await asyncio.to_thread(
                        cpu_work, backend_config["chunk_cpu_seconds"]
                    )
                    await asyncio.sleep(backend_config["chunk_delay_seconds"])
                    now = time.perf_counter()
                    if first is None:
                        first = now
                    yield {
                        **identity,
                        "event": "chunk",
                        "index": index,
                        "text": answer if index == 0 else ".",
                        "cache_hit": hit,
                    }
                yield {**identity, "event": "done", "chunks": chunks}
                completed = True
            finally:
                elapsed = time.perf_counter() - started
                outcome = "completed" if completed else "aborted"
                state.requests.labels(outcome).inc()
                if completed and first is not None:
                    state.ttfc.observe(first - started)
                    state.duration.observe(elapsed)
                state.running -= 1
                state.active.set(state.running)
                state.semaphore.release()
                state.log(
                    outcome,
                    request_id,
                    duration_seconds=elapsed,
                    ttfc_seconds=None if first is None else first - started,
                )

        if streaming:

            async def encoded() -> AsyncIterator[bytes]:
                async for item in events():
                    yield (json.dumps(item, ensure_ascii=False) + "\n").encode()

            return StreamingResponse(
                encoded(),
                media_type="application/x-ndjson",
                headers={
                    "X-Pod-Name": state.pod,
                    "X-Model-ID": str(identity["model_id"]),
                },
            )
        output = []
        async for item in events():
            if item["event"] == "chunk":
                output.append(item["text"])
        return JSONResponse(
            {
                **identity,
                "answer": "".join(output),
                "output_chunks": chunks,
                "total_seconds": time.perf_counter() - started,
            }
        )

    @app.post("/stream")
    async def stream(request: Request) -> Response:
        return await handle(request, True)

    @app.post("/generate")
    async def generate(request: Request) -> Response:
        return await handle(request, False)

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    uvicorn.run(
        create_app(config),
        host=config["api"]["host"],
        port=config["api"]["port"],
        timeout_graceful_shutdown=config["api"]["graceful_timeout_seconds"],
        access_log=False,
    )


if __name__ == "__main__":
    main()
