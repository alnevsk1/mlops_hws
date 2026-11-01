"""Локальный FastAPI-сервис принятой версии с внедряемым backend."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Response

from release import validate_manifest, verify_files

LOGGER = logging.getLogger(__name__)
SETTINGS = yaml.safe_load(
    Path(__file__).with_name("params.yaml").read_text(encoding="utf-8")
)["api"]


class StubBackend:
    """Лёгкий backend для проверки процесса без модели."""

    def __init__(self, manifest: dict):
        self.loaded_id = manifest["model_id"]
        self.runtime_sha256 = manifest["runtime_sha256"]
        self.calls = 0

    def generate(self, prompt: str, limit: int) -> dict:
        self.calls += 1
        time.sleep(0.01)
        return {"answer": f"STUB ONLY: {self.loaded_id}", "output_tokens": 1}


class ServiceState:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.manifest: dict | None = None
        self.backend: object | None = None
        self.ready = threading.Event()
        self.warmup_finished = threading.Event()
        self.generation_lock = threading.BoundedSemaphore(SETTINGS["max_concurrency"])


def artifact_root(workspace: Path, manifest: dict) -> Path:
    if manifest["artifact_uri"].startswith("hf://"):
        from huggingface_hub import snapshot_download

        runtime = manifest["runtime"]
        expected = f"hf://{runtime['base_model']}@{runtime['base_revision']}"
        if manifest["artifact_uri"] != expected:
            raise ValueError("URI базовой модели не совпадает с runtime")
        return Path(
            snapshot_download(
                runtime["base_model"],
                revision=runtime["base_revision"],
                local_files_only=True,
            )
        )
    uri = Path(manifest["artifact_uri"])
    location = uri if uri.is_absolute() else workspace / uri
    return location if location.is_dir() else location.parent


def create_app(
    workspace: Path,
    warmup_seconds: float = 0,
    backend_factory: Callable[[dict], object] | None = None,
) -> FastAPI:
    """Создать сервис; фабрика backend позволяет тестировать без весов."""
    state = ServiceState(workspace)
    if backend_factory is None:
        backend_factory = StubBackend

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manifest = json.loads((workspace / "accepted.json").read_text(encoding="utf-8"))
        validate_manifest(manifest)
        verify_files(manifest, artifact_root(workspace, manifest))
        backend = backend_factory(manifest)
        state.manifest = manifest
        state.backend = backend
        if warmup_seconds <= 0:
            if hasattr(backend, "warmup"):
                await asyncio.to_thread(backend.warmup)
            state.warmup_finished.set()
            state.ready.set()
            task = None
        else:

            async def finish_warmup():
                await asyncio.sleep(warmup_seconds)
                if hasattr(backend, "warmup"):
                    await asyncio.to_thread(backend.warmup)
                state.warmup_finished.set()
                state.ready.set()

            task = asyncio.create_task(finish_warmup())
        try:
            yield
        finally:
            state.ready.clear()
            if task is not None:
                task.cancel()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"alive": True}

    @app.get("/ready")
    def ready(response: Response) -> dict:
        available = state.ready.is_set()
        if not available:
            response.status_code = 503
        return {"ready": available}

    @app.get("/debug")
    def debug() -> dict:
        return {
            "loaded_id": getattr(state.backend, "loaded_id", None),
            "calls": getattr(state.backend, "calls", 0),
            "warmup_finished": state.warmup_finished.is_set(),
        }

    @app.post("/generate")
    def generate(payload: dict) -> dict:
        if not state.ready.is_set():
            raise HTTPException(status_code=503, detail="Модель не готова")
        assert state.manifest is not None and state.backend is not None
        prompt = payload.get("prompt")
        limit = payload.get(
            "max_new_tokens", state.manifest["runtime"]["generation"]["max_new_tokens"]
        )
        maximum = state.manifest["runtime"]["generation"]["max_new_tokens"]
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt) > SETTINGS["max_prompt_chars"]
        ):
            raise HTTPException(status_code=422, detail="Некорректный prompt")
        if type(limit) is not int or not 1 <= limit <= maximum:
            raise HTTPException(status_code=422, detail="Некорректный max_new_tokens")
        started = time.perf_counter()
        with state.generation_lock:
            generation_started = time.perf_counter()
            result = state.backend.generate(prompt, limit)
            generation_seconds = time.perf_counter() - generation_started
        return {
            **result,
            "model_id": state.manifest["model_id"],
            "runtime_sha256": state.manifest["runtime_sha256"],
            "request_id": str(uuid.uuid4()),
            "generation_seconds": generation_seconds,
            "total_seconds": time.perf_counter() - started,
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(".lab"))
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--warmup-seconds", type=float, default=5)
    parser.add_argument("--backend", choices=("stub", "real"), default="stub")
    args = parser.parse_args()
    backend_factory = StubBackend
    if args.backend == "real":
        from real_backend import RealBackend

        backend_factory = RealBackend
    app = create_app(args.workspace, args.warmup_seconds, backend_factory)
    LOGGER.info("Запуск сервиса на 127.0.0.1:%d", args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
