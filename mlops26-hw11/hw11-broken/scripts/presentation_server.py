"""Локальная презентация с запуском заранее заданных демонстрационных команд."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
JOBS: dict[str, dict[str, object]] = {}
KUBE = [
    "kubectl",
    "--context",
    "kind-mlops-hw11",
    "-n",
    "hw11",
    "--request-timeout=10s",
]
COMMANDS = {
    "cluster": KUBE + ["get", "pods,deployments,services", "-o", "wide"],
    "tests": ["make", "check"],
    "evidence": ["uv", "run", "--no-sync", "python", "-m", "scripts.check_evidence"],
    "api": KUBE
    + [
        "exec",
        "client-0",
        "--",
        "/app/.venv/bin/python",
        "-c",
        "import httpx,json; r=httpx.post('http://hw11-normal:8080/generate',json={'prompt':'continue','history':['VIDEO-DEMO'],'max_chunks':1},timeout=20); print(f'HTTP {r.status_code}'); print(json.dumps(r.json(),ensure_ascii=False,indent=2)); r.raise_for_status()",
    ],
}


def run_job(job_id: str, command: list[str]) -> None:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            output, _ = process.communicate(timeout=120)
            result = {
                "status": "done",
                "exit_code": process.returncode,
                "output": output[-80000:],
            }
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            result = {
                "status": "error",
                "exit_code": None,
                "output": output[-80000:] + "\nПревышено время ожидания (120 с).",
            }
    except OSError as exc:
        result = {"status": "error", "exit_code": None, "output": str(exc)}
    with LOCK:
        JOBS[job_id].update(result)


class PresentationServer(ThreadingHTTPServer):
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value: object, status: int = 200) -> None:
        self.send_bytes(
            json.dumps(value, ensure_ascii=False).encode(),
            "application/json; charset=utf-8",
            status,
        )

    def valid_host(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self) -> None:
        if not self.valid_host():
            self.send_json({"error": "Invalid host"}, 403)
            return
        path = urlparse(self.path).path
        if path in ("/", "/presentation.html"):
            html = (
                (ROOT / "presentation.html")
                .read_text(encoding="utf-8")
                .replace("__DEMO_TOKEN__", TOKEN)
            )
            self.send_bytes(html.encode(), "text/html; charset=utf-8")
        elif path == "/presentation-health":
            self.send_json({"app": "mlops-hw11-presentation", "version": 1})
        elif path.startswith("/job/"):
            with LOCK:
                job = dict(JOBS.get(path.removeprefix("/job/"), {}))
            self.send_json(job, 200 if job else 404)
        elif path in {
            "/reports/figures/01_rollout_readiness.png",
            "/reports/figures/02_hot_replica.png",
            "/reports/figures/03_policy_comparison.png",
            "/reports/figures/04_hpa.png",
        }:
            self.send_bytes((ROOT / path.lstrip("/")).read_bytes(), "image/png")
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        if not self.valid_host():
            self.send_json({"error": "Invalid host"}, 403)
            return
        port = self.server.server_address[1]
        origins = (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
        if (
            self.headers.get("Origin") not in origins
            or self.headers.get("X-Demo-Token") != TOKEN
        ):
            self.send_json(
                {"error": "Откройте презентацию через локальный сервер."}, 403
            )
            return
        if self.path != "/run":
            self.send_json({"error": "Not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 512:
                raise ValueError("Bad length")
            body = json.loads(self.rfile.read(length))
            command_id = body.get("id") if isinstance(body, dict) else None
            if not isinstance(command_id, str) or command_id not in COMMANDS:
                raise ValueError("Unknown command")
        except (ValueError, UnicodeError):
            self.send_json({"error": "Неизвестная команда."}, 400)
            return
        with LOCK:
            if any(job["status"] == "running" for job in JOBS.values()):
                self.send_json({"error": "Дождитесь завершения текущей команды."}, 409)
                return
            job_id = secrets.token_hex(12)
            JOBS[job_id] = {
                "status": "running",
                "output": "",
                "exit_code": None,
                "id": command_id,
            }
        threading.Thread(
            target=run_job, args=(job_id, COMMANDS[command_id]), daemon=True
        ).start()
        self.send_json({"job": job_id}, 202)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8761)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if args.open:
        try:
            with urlopen(f"{url}/presentation-health", timeout=2) as response:
                existing = json.load(response)
            if existing.get("app") == "mlops-hw11-presentation":
                webbrowser.open(url)
                return
        except (OSError, ValueError):
            pass
    try:
        server = PresentationServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        raise SystemExit(f"Порт {args.port} недоступен: {exc}") from exc
    print(f"Презентация: {url}\nОстановка сервера: Ctrl+C", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
