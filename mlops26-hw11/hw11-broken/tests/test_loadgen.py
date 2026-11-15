from __future__ import annotations

import time
import unittest

import httpx

from scripts.loadgen import attempt, schedule


class AccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_200_without_done_is_aborted(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text='{"request_id":"unit-0","event":"chunk","text":"x"}\n'
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            row = await attempt(
                client,
                "http://test/stream",
                schedule(1, 1, "uniform", 42, 1)[0],
                time.time(),
                1,
                "unit",
            )
        self.assertEqual(row["outcome"], "aborted")
        self.assertEqual(row["chunks_received"], 1)

    async def test_http_rejection_is_not_completion(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"pod": "p", "model_id": "sim-A"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            row = await attempt(
                client,
                "http://test/stream",
                schedule(1, 1, "uniform", 42, 1)[0],
                time.time(),
                1,
                "unit",
            )
        self.assertEqual(row["outcome"], "rejected")
        self.assertEqual(row["status"], 429)
        self.assertIsNone(row["ttfc_seconds"])

    async def test_schedule_reproducible_and_80_percent_hot(self) -> None:
        first = schedule(120, 2, "skew", 42, 8)
        self.assertEqual(first, schedule(120, 2, "skew", 42, 8))
        self.assertEqual(sum(item["client"] == 0 for item in first), 192)
        self.assertEqual({item["client"] for item in first}, {0, 1, 2, 3})


if __name__ == "__main__":
    unittest.main()
