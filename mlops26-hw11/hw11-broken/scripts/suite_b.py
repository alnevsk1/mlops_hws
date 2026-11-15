"""12 прогонов B: 2 политики x 2 профиля x 3 повтора, по 120 секунд."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    pilot = ROOT / "results" / "B-skew-sticky-1"
    deadline = time.monotonic() + 300
    while pilot.exists() and not (pilot / "summary.json").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Pilot has not finished")
        time.sleep(5)
    kube = ["kubectl", "--context", "kind-mlops-hw11", "-n", "hw11"]
    subprocess.run(
        kube
        + [
            "patch",
            "deployment",
            "hw11-api",
            "--patch-file",
            "experiments/b-reset.yaml",
        ],
        cwd=ROOT,
        check=True,
    )
    for repeat in range(1, 4):
        for traffic in ("uniform", "skew"):
            for service in ("sticky", "normal"):
                name = f"B-main-{traffic}-{service}-{repeat}"
                if (ROOT / "results" / name / "summary.json").exists():
                    print(f"Already recorded: {name}", flush=True)
                    continue
                subprocess.run(
                    kube + ["rollout", "restart", "deployment/hw11-api"], check=True
                )
                subprocess.run(
                    kube
                    + ["rollout", "status", "deployment/hw11-api", "--timeout=180s"],
                    check=True,
                )
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.experiment",
                        "--run",
                        name,
                        "--service",
                        service,
                        "--traffic",
                        traffic,
                        "--duration",
                        "120",
                        "--rate",
                        "1.5",
                    ],
                    cwd=ROOT,
                    check=True,
                )
                print(f"Completed {name}", flush=True)

    subprocess.run(
        kube
        + [
            "patch",
            "deployment",
            "hw11-api",
            "--type=merge",
            "-p",
            json.dumps({"spec": {"strategy": {"rollingUpdate": {"maxSurge": 1}}}}),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
