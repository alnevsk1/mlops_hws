"""Опыты A и интеграционные проверки на действующем Kubernetes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KUBE = ["kubectl", "--context", "kind-mlops-hw11", "-n", "hw11"]


def kube(*args: str) -> str:
    return subprocess.run(
        [*KUBE, *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def upgrade(*values: str, wait: bool = True) -> None:
    cmd = [
        "helm",
        "upgrade",
        "hw11",
        "chart",
        "--kube-context",
        "kind-mlops-hw11",
        "-n",
        "hw11",
        "--reset-values",
    ]
    for value in values:
        try:
            json.loads(value.split("=", 1)[1])
            flag = "--set-json"
        except ValueError:
            flag = "--set"
        cmd.extend([flag, value])
    if wait:
        cmd.extend(["--wait", "--timeout", "180s"])
    subprocess.run(cmd, cwd=ROOT, check=True)


def remote(code: str) -> str:
    return subprocess.run(
        [*KUBE, "exec", "-i", "client-0", "--", "/app/.venv/bin/python", "-"],
        input=code,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def probe(history: str = "ALPHA-HISTORY") -> dict:
    code = f"""import httpx,json
r=httpx.post('http://hw11-normal:8080/generate',json={{'prompt':'continue','history':[{history!r}],'max_chunks':1}},timeout=30)
print(json.dumps({{'status':r.status_code,'body':r.json()}}))
"""
    return json.loads(remote(code))


def experiment(
    name: str,
    duration: int,
    rate: float,
    chunks: int,
    service: str = "normal",
    traffic: str = "uniform",
) -> subprocess.Popen:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.experiment",
            "--run",
            name,
            "--duration",
            str(duration),
            "--rate",
            str(rate),
            "--chunks",
            str(chunks),
            "--service",
            service,
            "--traffic",
            traffic,
        ],
        cwd=ROOT,
    )
    path = ROOT / "results" / name / "schedule.json"
    until = time.monotonic() + 30
    while not path.exists():
        if process.poll() is not None or time.monotonic() > until:
            raise RuntimeError("Generator did not start")
        time.sleep(0.2)
    start = json.loads(path.read_text(encoding="utf-8"))["start_utc"]
    time.sleep(max(0, start + 4 - time.time()))
    return process


def finish(process: subprocess.Popen, name: str) -> dict:
    if process.wait() != 0:
        raise RuntimeError(f"Experiment failed: {name}")
    path = ROOT / "results" / name
    (path / "api-logs.txt").write_text(
        kube("logs", "-l", "app=hw11-api", "--tail=-1", "--prefix=true"),
        encoding="utf-8",
    )
    return json.loads((path / "summary.json").read_text(encoding="utf-8"))


def warmup() -> None:
    for defect, name in ((True, "A-warmup-broken"), (False, "A-warmup-fixed")):
        upgrade("model=A", "backend.early_ready=false")
        process = experiment(name, 70, 2, 1)
        stamp = time.time()
        upgrade("model=B", f"backend.early_ready={str(defect).lower()}")
        path = ROOT / "results" / name
        (path / "action.json").write_text(
            json.dumps({"utc": stamp, "action": "upgrade B", "early_ready": defect}),
            encoding="utf-8",
        )
        summary = finish(process, name)
        if not defect:
            assert summary["outcomes"]["completed"] == summary["planned"], summary


def rollout() -> None:
    for broken in (True, False):
        grace = "gracePeriod=1" if broken else "gracePeriod=60"
        upgrade("model=A", grace)
        name = "A-rollout-broken" if broken else "A-rollout"
        process = experiment(name, 90, 0.1, 100)
        stamp = time.time()
        upgrade("model=B", grace)
        summary = finish(process, name)
        directory = ROOT / "results" / name
        (directory / "action.json").write_text(
            json.dumps({"utc": stamp, "action": "A to B", "grace": grace}),
            encoding="utf-8",
        )
        if not broken:
            assert summary["outcomes"]["completed"] == summary["planned"], summary
        rows = [
            json.loads(line)
            for line in (directory / "requests.jsonl").read_text().splitlines()
        ]
        assert any(row["sent_utc"] < stamp < row["end_utc"] for row in rows)


def rollback() -> None:
    upgrade("model=A")
    history = json.loads(
        subprocess.run(
            [
                "helm",
                "history",
                "hw11",
                "-n",
                "hw11",
                "--kube-context",
                "kind-mlops-hw11",
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    revision = str(history[-1]["revision"])
    name = "A-rollback"
    process = experiment(name, 90, 1, 1)
    stamp = time.time()
    upgrade("model=B", "backend.never_ready=true", wait=False)
    time.sleep(30)
    path = ROOT / "results" / name
    (path / "unready.json").write_text(
        kube("get", "pods,endpointslices", "-o", "json"), encoding="utf-8"
    )
    subprocess.run(
        [
            "helm",
            "rollback",
            "hw11",
            revision,
            "-n",
            "hw11",
            "--kube-context",
            "kind-mlops-hw11",
            "--wait",
            "--timeout",
            "180s",
        ],
        check=True,
    )
    result = probe()
    (path / "verification.json").write_text(
        json.dumps({"action_utc": stamp, "after_rollback": result}), encoding="utf-8"
    )
    assert result["body"]["model_id"] == "sim-A"
    summary = finish(process, name)
    assert summary["outcomes"]["completed"] == summary["planned"], summary


def recovery() -> None:
    upgrade("model=A")
    directory = ROOT / "results" / "A-recovery"
    directory.mkdir(parents=True, exist_ok=False)
    before = probe()
    kube("rollout", "restart", "deployment/hw11-api")
    kube("rollout", "status", "deployment/hw11-api", "--timeout=180s")
    after = probe()
    other = probe("BETA-HISTORY")
    assert before["body"]["pod"] != after["body"]["pod"]
    assert before["body"]["artifact_sha256"] == after["body"]["artifact_sha256"]
    assert "ALPHA-HISTORY" in after["body"]["answer"]
    assert "ALPHA-HISTORY" not in other["body"]["answer"]
    assert "BETA-HISTORY" not in after["body"]["answer"]
    (directory / "verification.json").write_text(
        json.dumps({"before": before, "after": after, "other": other}, indent=2),
        encoding="utf-8",
    )


def failure() -> None:
    upgrade("model=A")
    name = "A-forced-failure"
    process = experiment(name, 90, 0.4, 100)
    # Найти Pod с активным ответом через Prometheus до убийства процесса.
    import httpx

    response = httpx.get(
        "http://127.0.0.1:9091/api/v1/query", params={"query": "hw11_running > 0"}
    ).json()
    targets = response["data"]["result"]
    if not targets:
        time.sleep(4)
        targets = httpx.get(
            "http://127.0.0.1:9091/api/v1/query", params={"query": "hw11_running > 0"}
        ).json()["data"]["result"]
    if not targets:
        raise RuntimeError("No active stream found")
    pod = targets[0]["metric"]["pod"]
    stamp = time.time()
    code = """from pathlib import Path
import os,signal
for path in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        args=path.read_bytes().split(bytes([0]))
        if (int(path.parent.name) > 1
                and Path(args[0].decode()).name.startswith('python')
                and b'src.service' in args and b'-m' in args):
            print(f'Killing service PID {path.parent.name}', flush=True)
            os.kill(int(path.parent.name),signal.SIGKILL)
            break
    except (FileNotFoundError,ProcessLookupError,PermissionError):
        pass
"""
    # Контейнер может завершить exec вместе с убитым процессом: сохраняем код,
    # а успех вмешательства проверяем по реальному обрыву и восстановлению API.
    killed = subprocess.run(
        [*KUBE, "exec", "-i", pod, "--", "/app/.venv/bin/python", "-"],
        input=code,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    path = ROOT / "results" / name
    (path / "action.json").write_text(
        json.dumps(
            {
                "utc": stamp,
                "pod": pod,
                "action": "SIGKILL",
                "exec_exit_code": killed.returncode,
                "stdout": killed.stdout,
                "stderr": killed.stderr,
            }
        ),
        encoding="utf-8",
    )
    summary = finish(process, name)
    result = probe()
    (path / "recovered.json").write_text(json.dumps(result), encoding="utf-8")
    assert result["status"] == 200
    assert summary["outcomes"]["aborted"] + summary["outcomes"]["timeout"] > 0, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario", choices=("warmup", "rollout", "rollback", "recovery", "failure")
    )
    args = parser.parse_args()
    globals()[args.scenario]()


if __name__ == "__main__":
    main()
