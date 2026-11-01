"""Интеграционные проверки файлов, процессов и HTTP на синтетическом стенде."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        check=False,
        cwd=PROJECT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def request(
    port: int, method: str, route: str, payload: object | None = None
) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(
        f"http://127.0.0.1:{port}{route}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


@contextmanager
def server(workspace: Path, warmup: float = 0):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "service.py",
            "--workspace",
            str(workspace),
            "--port",
            str(port),
            "--warmup-seconds",
            str(warmup),
        ],
        cwd=PROJECT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        for _ in range(100):
            try:
                request(port, "GET", "/health")
                break
            except OSError:
                if process.poll() is not None:
                    raise AssertionError("Сервис завершился до старта")
                time.sleep(0.05)
        else:
            raise AssertionError("Сервис не стал доступен")
        yield port
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


class ReleaseIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "workspace"
        result = command("lab.py", "--workspace", str(self.root), "init")
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_lab(self, *args: str) -> subprocess.CompletedProcess[str]:
        return command("lab.py", "--workspace", str(self.root), "run", *args)

    def test_recovery_and_content_cache_in_new_process(self) -> None:
        failed = self.run_lab("--fail-after-prepare")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("PREPARED", failed.stdout)
        resumed = self.run_lab()
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn("CACHE_HIT", resumed.stdout)
        (self.root / "input.txt").write_text("Изменённый пример\n", encoding="utf-8")
        changed = self.run_lab()
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertIn("PREPARED", changed.stdout)
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config["max_length"] += 1
        (self.root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        self.assertIn("PREPARED", self.run_lab().stdout)

    def test_incomplete_rejected_and_foreign_report_rejected(self) -> None:
        accepted = self.root / "accepted.json"
        before = digest(accepted)
        incomplete = self.run_lab("--report", "report-incomplete.json")
        self.assertNotEqual(incomplete.returncode, 0)
        self.assertEqual(digest(accepted), before)
        foreign = self.run_lab("--report", "report-A.json")
        self.assertNotEqual(foreign.returncode, 0)
        self.assertEqual(digest(accepted), before)

    def test_rejected_report_and_repeated_publication(self) -> None:
        accepted = self.root / "accepted.json"
        before = digest(accepted)
        rejected = self.run_lab("--report", "report-reject.json")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(digest(accepted), before)
        for _ in range(2):
            result = self.run_lab()
            self.assertEqual(result.returncode, 0, result.stderr)
        events = (
            (self.root / "publications.jsonl").read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual(len(events), 1)

    def test_pointer_crash_reconciles_one_event(self) -> None:
        crashed = self.run_lab("--fail-after-publish")
        self.assertNotEqual(crashed.returncode, 0)
        self.assertEqual(
            json.loads((self.root / "accepted.json").read_text(encoding="utf-8"))[
                "model_id"
            ],
            "fixture-B",
        )
        self.assertFalse((self.root / "publications.jsonl").exists())
        self.assertEqual(self.run_lab().returncode, 0)
        self.assertEqual(self.run_lab().returncode, 0)
        events = (
            (self.root / "publications.jsonl").read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual(len(events), 1)
        report_path = self.root / "report-B.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["evaluation_run_id"] = "another-bound-eval"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        self.assertEqual(self.run_lab().returncode, 0)
        self.assertEqual(
            len(
                (self.root / "publications.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ),
            2,
        )

    def test_tampered_artifact_never_publishes(self) -> None:
        before = digest(self.root / "accepted.json")
        (self.root / "model-B.txt").write_text("tampered", encoding="utf-8")
        result = self.run_lab()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(digest(self.root / "accepted.json"), before)

    def test_warmup_blocks_ready_and_generate(self) -> None:
        with server(self.root, warmup=1.5) as port:
            self.assertEqual(request(port, "GET", "/health")[0], 200)
            self.assertEqual(request(port, "GET", "/ready")[0], 503)
            self.assertEqual(
                request(port, "POST", "/generate", {"prompt": "test"})[0], 503
            )
            self.assertEqual(request(port, "GET", "/debug")[1]["calls"], 0)
            time.sleep(1.6)
            self.assertEqual(request(port, "GET", "/ready")[0], 200)

    def test_loaded_version_survives_pointer_change_and_rollback(self) -> None:
        with server(self.root) as port:
            status, old = request(port, "POST", "/generate", {"prompt": "test"})
            self.assertEqual(status, 200)
            self.assertEqual(old["model_id"], "fixture-A")
            self.assertEqual(self.run_lab().returncode, 0)
            status, still_old = request(port, "POST", "/generate", {"prompt": "test"})
            self.assertEqual(status, 200)
            self.assertEqual(still_old["model_id"], "fixture-A")
        with server(self.root) as port:
            self.assertEqual(
                request(port, "POST", "/generate", {"prompt": "test"})[1]["model_id"],
                "fixture-B",
            )
        rollback = command(
            "-c",
            "import json,release,sys;from pathlib import Path;p=Path(sys.argv[1]);release.publish(json.loads((p/'manifest-A.json').read_text()),json.loads((p/'report-A.json').read_text()),p/'accepted.json')",
            str(self.root),
        )
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        with server(self.root) as port:
            self.assertEqual(
                request(port, "POST", "/generate", {"prompt": "test"})[1]["model_id"],
                "fixture-A",
            )

    def test_invalid_requests_never_call_backend(self) -> None:
        with server(self.root) as port:
            for payload in [
                {"prompt": ""},
                {"prompt": "x", "max_new_tokens": 0},
                {"prompt": "x", "max_new_tokens": 100000},
                {"prompt": "x", "max_new_tokens": "4"},
            ]:
                with self.subTest(payload=payload):
                    before = request(port, "GET", "/debug")[1]["calls"]
                    status, _ = request(port, "POST", "/generate", payload)
                    self.assertTrue(400 <= status < 500)
                    self.assertEqual(request(port, "GET", "/debug")[1]["calls"], before)
            self.assertEqual(
                request(
                    port, "POST", "/generate", {"prompt": "x", "max_new_tokens": 1}
                )[0],
                200,
            )


if __name__ == "__main__":
    unittest.main()
