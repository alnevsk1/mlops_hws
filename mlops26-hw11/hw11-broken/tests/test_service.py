from __future__ import annotations

import asyncio
import copy
import unittest
from pathlib import Path

import httpx
import yaml

from src.service import SimulatedBackend, create_app

ROOT = Path(__file__).resolve().parents[1]


def settings() -> dict:
    config = yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))
    config["backend"]["artifact"] = str(ROOT / config["backend"]["artifact"])
    config["backend"].update(
        warmup_seconds=0.02,
        hit_cpu_seconds=0,
        miss_cpu_seconds=0,
        chunk_cpu_seconds=0,
        chunk_delay_seconds=0.002,
    )
    return config


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_warmup_and_intentional_defect(self) -> None:
        for defect in (False, True):
            config = settings()
            config["backend"]["early_ready"] = defect
            app = create_app(config)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://test"
                ) as client:
                    ready = await client.get("/ready")
                    self.assertEqual(ready.status_code, 200 if defect else 503)
                    self.assertEqual(
                        (
                            await client.post("/stream", json={"prompt": "test"})
                        ).status_code,
                        503,
                    )
                    await asyncio.sleep(0.04)
                    self.assertEqual((await client.get("/ready")).status_code, 200)

    async def test_artifact_hash_rejected(self) -> None:
        config = settings()
        config["backend"]["artifact_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            SimulatedBackend(config["backend"])

    async def test_dialogue_isolation_and_loaded_identity(self) -> None:
        app = create_app(settings())
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.04)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                for marker, other in (
                    ("ALPHA-SECRET", "BETA-SECRET"),
                    ("BETA-SECRET", "ALPHA-SECRET"),
                ):
                    response = await client.post(
                        "/generate",
                        json={
                            "prompt": "continue",
                            "history": [marker],
                            "max_chunks": 1,
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(marker, response.json()["answer"])
                    self.assertNotIn(other, response.text)
                    self.assertEqual(response.json()["model_id"], "sim-A")
                    self.assertEqual(response.json()["backend_mode"], "simulated")

    async def test_queue_rejection_and_slot_cleanup(self) -> None:
        config = settings()
        config["backend"].update(max_waiting=0, chunk_delay_seconds=0.03)
        app = create_app(config)
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.04)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                task = asyncio.create_task(
                    client.post("/generate", json={"prompt": "long", "max_chunks": 4})
                )
                await asyncio.sleep(0.015)
                response = await client.post("/generate", json={"prompt": "busy"})
                self.assertEqual(response.status_code, 429)
                await task
                self.assertEqual(app.state.service.running, 0)
                self.assertEqual(app.state.service.waiting, 0)
                metrics = (await client.get("/metrics")).text
                self.assertIn('hw11_requests_total{outcome="rejected"} 1.0', metrics)
                self.assertIn('hw11_requests_total{outcome="completed"} 1.0', metrics)

    async def test_bad_payload_never_runs_backend(self) -> None:
        app = create_app(settings())
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.04)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                for payload in (
                    [],
                    {"prompt": "x", "max_chunks": True},
                    {"prompt": "x", "history": [9]},
                ):
                    self.assertEqual(
                        (await client.post("/generate", json=payload)).status_code, 422
                    )
                self.assertEqual(app.state.service.running, 0)
                self.assertEqual(len(app.state.service.backend.cache), 0)

    async def test_cache_eviction(self) -> None:
        config = copy.deepcopy(settings()["backend"])
        config["cache_entries"] = 1
        backend = SimulatedBackend(config)
        self.assertFalse(await backend.prefill("one"))
        self.assertTrue(await backend.prefill("one"))
        self.assertFalse(await backend.prefill("two"))
        self.assertFalse(await backend.prefill("one"))


if __name__ == "__main__":
    unittest.main()
