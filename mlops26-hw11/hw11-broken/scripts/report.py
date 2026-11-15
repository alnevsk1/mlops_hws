"""Пересчитать таблицы и графики из сохранённых сырых данных."""

from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scripts.export_metrics import percentile

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGURES = ROOT / "reports" / "figures"


def read_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def summarize(name: str, rows: list[dict], planned: int) -> dict:
    success = [row for row in rows if row["outcome"] == "completed"]
    counts = Counter(row["outcome"] for row in rows)
    intervals = [value for row in success for value in row["inter_chunk_seconds"]]
    return {
        "run": name,
        "planned": planned,
        "sent": len(rows),
        "completed": counts["completed"],
        "rejected": counts["rejected"],
        "aborted": counts["aborted"],
        "aborted_streams": sum(
            row["outcome"] == "aborted" and row["status"] == 200 for row in rows
        ),
        "transport_errors": sum(
            row["outcome"] == "aborted" and row["status"] is None for row in rows
        ),
        "timeout": counts["timeout"],
        "not_sent": planned - len(rows),
        "p50_ttfc_s": percentile([row["ttfc_seconds"] for row in success], 0.5),
        "p95_ttfc_s": percentile([row["ttfc_seconds"] for row in success], 0.95),
        "p50_duration_s": percentile([row["duration_seconds"] for row in success], 0.5),
        "inter_chunk_n": len(intervals),
        "p50_inter_chunk_s": percentile(intervals, 0.5),
        "p95_inter_chunk_s": percentile(intervals, 0.95),
        "p95_duration_s": percentile(
            [row["duration_seconds"] for row in success], 0.95
        ),
        "p95_send_lag_s": percentile([row["send_lag_seconds"] for row in rows], 0.95),
        "error_fraction": 1 - len(success) / len(rows) if rows else None,
        "pods": dict(Counter(row["pod"] for row in rows)),
    }


def ready_count(row: dict) -> int:
    return sum(
        item["kind"] == "Pod"
        and item["metadata"].get("labels", {}).get("app") == "hw11-api"
        and any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in item.get("status", {}).get("conditions", [])
        )
        for item in row["kubernetes"].get("items", [])
    )


def endpoint_count(row: dict) -> int:
    return sum(
        endpoint.get("conditions", {}).get("ready") is True
        for item in row["kubernetes"].get("items", [])
        if item["kind"] == "EndpointSlice"
        and item["metadata"].get("labels", {}).get("kubernetes.io/service-name")
        == "hw11-normal"
        for endpoint in item.get("endpoints", [])
    )


def main() -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    datasets = {}
    summary = []
    all_rows = []
    for path in sorted(RESULTS.glob("*/requests.jsonl")):
        rows = read_rows(path)
        schedule = json.loads((path.parent / "schedule.json").read_text())
        datasets[path.parent.name] = rows
        summary.append(summarize(path.parent.name, rows, len(schedule["requests"])))
        all_rows.extend(
            {
                **row,
                "session_id": f"{row['run']}:client-{row['client']}",
                "session_id_source": "one-logical-session-per-load-client",
            }
            for row in rows
        )
    if not summary:
        raise SystemExit("No completed runs")
    (RESULTS / "requests.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in all_rows), encoding="utf-8"
    )
    with (RESULTS / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    (RESULTS / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    b = [row for row in summary if row["run"].startswith("B-main-")]
    if b:
        fig, ax = plt.subplots(figsize=(10, 5))
        groups = ["uniform-sticky", "uniform-normal", "skew-sticky", "skew-normal"]
        for i, group in enumerate(groups):
            points = [row for row in b if group in row["run"]]
            ax.scatter(
                [i] * len(points),
                [row["p95_ttfc_s"] for row in points],
                label=f"{group}, n_runs={len(points)}",
                s=65,
            )
        ax.set_xticks(range(4), groups)
        ax.set_ylabel("Client p95 TTFC (s), completed responses")
        ax.set_title("CPU simulated; 120 s; 1.5 requests/s; 8 chunks; cold cache")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(FIGURES / "03_policy_comparison.png", dpi=150)
        plt.close(fig)
    skew = [name for name in datasets if name.startswith("B-main-skew-sticky")]
    if skew:
        name = skew[-1]
        rows = datasets[name]
        start = min(row["sent_utc"] for row in rows)
        fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        for pod in sorted({row["pod"] for row in rows if row["pod"]}):
            data = [
                row
                for row in rows
                if row["pod"] == pod and row["outcome"] == "completed"
            ]
            axes[0].scatter(
                [row["sent_utc"] - start for row in data],
                [row["ttfc_seconds"] for row in data],
                s=12,
                label=pod,
            )
            stamps, waits = [], []
            for sample in read_rows(RESULTS / name / "timeline.jsonl"):
                for metric in (
                    sample.get("prometheus", {}).get("data", {}).get("result", [])
                ):
                    if (
                        metric["metric"].get("pod") == pod
                        and metric["metric"].get("__name__") == "hw11_waiting"
                    ):
                        stamps.append(sample["utc"] - start)
                        waits.append(float(metric["value"][1]))
            axes[1].plot(stamps, waits, label=pod)
        axes[0].set_ylabel("Client TTFC (s)")
        axes[1].set_ylabel("Waiting requests")
        axes[1].set_xlabel("Seconds from first send")
        axes[0].set_title(f"{name}: CPU simulated, 80% from client-0")
        for ax in axes:
            ax.legend(fontsize=8)
            ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(FIGURES / "02_hot_replica.png", dpi=150)
        plt.close(fig)
    a = [name for name in datasets if name.startswith("A-")]
    if a:
        fig, axes = plt.subplots(len(a), 1, figsize=(11, 3 * len(a)), squeeze=False)
        for ax, name in zip(axes[:, 0], a):
            rows = datasets[name]
            start = min(row["sent_utc"] for row in rows)
            timeline = read_rows(RESULTS / name / "timeline.jsonl")
            ax.step(
                [row["utc"] - start for row in timeline],
                [ready_count(row) for row in timeline],
                where="post",
                label="Ready replicas",
            )
            ax.step(
                [row["utc"] - start for row in timeline],
                [endpoint_count(row) for row in timeline],
                where="post",
                linestyle="--",
                label="Ready Service endpoints",
            )
            failed = [row for row in rows if row["outcome"] != "completed"]
            ax.scatter(
                [row["sent_utc"] - start for row in failed],
                [0] * len(failed),
                marker="x",
                color="red",
                label="Failed attempts",
            )
            action = RESULTS / name / "action.json"
            if action.exists():
                stamp = json.loads(action.read_text())["utc"]
                ax.axvline(
                    stamp - start,
                    color="black",
                    linestyle=":",
                    label="Update / failure",
                )
            ax.set_ylim(-0.15, 3.4)
            ax.set_yticks([0, 1, 2, 3])
            ax.set_title(name)
            ax.set_xlabel("Seconds from first send")
            ax.set_ylabel("Ready replicas")
            ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "01_rollout_readiness.png", dpi=150)
        plt.close(fig)
    if (RESULTS / "C-hpa" / "timeline.jsonl").exists():
        timeline = read_rows(RESULTS / "C-hpa" / "timeline.jsonl")
        start = timeline[0]["utc"]
        x, current, desired = [], [], []
        for row in timeline:
            for item in row["kubernetes"].get("items", []):
                if item["kind"] == "HorizontalPodAutoscaler":
                    x.append(row["utc"] - start)
                    current.append(item.get("status", {}).get("currentReplicas", 0))
                    desired.append(item.get("status", {}).get("desiredReplicas", 0))
        down = RESULTS / "C-hpa" / "downscale.json"
        if down.exists():
            for row in json.loads(down.read_text()):
                x.append(row["utc"] - start)
                current.append(row["hpa"]["status"].get("currentReplicas", 0))
                desired.append(row["hpa"]["status"].get("desiredReplicas", 0))
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.step(x, current, where="post", label="Current replicas")
        ax.step(x, desired, where="post", label="Desired replicas", linestyle="--")
        ax.step(
            [row["utc"] - start for row in timeline],
            [ready_count(row) for row in timeline],
            where="post",
            linestyle=":",
            label="Ready Pods",
        )
        ax.set_ylabel("Replicas")
        ax.set_xlabel("Seconds from observation start")
        ax.set_title("HPA: CPU simulated, requests=200m, target=60%")
        if "C-hpa" in datasets:
            stamps = [row["sent_utc"] - start for row in datasets["C-hpa"]]
            twin = ax.twinx()
            twin.hist(
                stamps,
                bins=range(0, int(max(x)) + 10, 10),
                weights=[0.1] * len(stamps),
                alpha=0.15,
                label="Actual arrival rate",
            )
            twin.set_ylabel("Requests/s (10 s bins)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "04_hpa.png", dpi=150)
        plt.close(fig)

    columns = [
        "run",
        "planned",
        "sent",
        "completed",
        "rejected",
        "aborted",
        "timeout",
        "p50_ttfc_s",
        "p95_ttfc_s",
        "p95_duration_s",
        "p95_send_lag_s",
        "error_fraction",
        "p95_inter_chunk_s",
    ]
    table = "<tr>" + "".join(f"<th>{col}</th>" for col in columns) + "</tr>"
    for row in summary:
        table += (
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(round(row[col], 4) if isinstance(row[col], float) else row[col]))}</td>"
                for col in columns
            )
            + "</tr>"
        )
    figures = "".join(
        f'<figure><img src="reports/figures/{path.name}"><figcaption>{path.name}</figcaption></figure>'
        for path in sorted(FIGURES.glob("*.png"))
    )
    validation = RESULTS / "final-validation.json"
    passed = (
        validation.exists()
        and json.loads(validation.read_text()).get("evidence") == "passed"
    )
    status = (
        "Все обязательные опыты завершены; проверки кода и сырых доказательств пройдены."
        if passed
        else "Промежуточный отчёт: полнота опытов проверяется по results/ и docs/status.md."
    )
    intro = (
        (ROOT / "docs" / "report-narrative.html").read_text(encoding="utf-8")
        if (ROOT / "docs" / "report-narrative.html").exists()
        else ""
    )
    content = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><title>ДЗ 11</title>
<style>body{{font:16px/1.55 system-ui;max-width:1200px;margin:40px auto;padding:0 20px;color:#18263d}}.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;font-size:12px}}td,th{{padding:7px;border-bottom:1px solid #ccd}}img{{max-width:100%}}pre{{white-space:pre-wrap}}h1,h2{{color:#244d7a}}</style>
<h1>ДЗ 11. Kubernetes под нагрузкой</h1><p>{status}</p>
<p>backend_mode=simulated. TTFC и inter-chunk latency относятся к частям ответа CPU-имитатора, а не токенам модели.</p>
{intro}<h2>Пересчёт сырых клиентских результатов</h2><div class="table-scroll"><table>{table}</table></div>
<p>Перцентили: линейная интерполяция отсортированных успешных ответов. Число наблюдений = completed.
Inter-chunk latency: разности последовательных частей; для N частей N−1 интервалов, при N=1 отсутствует.
В CSV сохранены также p50/p95 полной длительности и интервалов с их числом наблюдений.
HTTP 200 без терминального done — оборванный поток. Повторы не выполняются. planned − sent — неотправленные запросы.</p>
{figures}<h2>Ревью</h2><pre>{html.escape((ROOT / "docs" / "review.md").read_text(encoding="utf-8"))}</pre>
<p>Источники: <a href="https://cloud.google.com/blog/products/containers-kubernetes/how-gke-inference-gateway-improved-latency-for-vertex-ai">Google / Vertex AI</a>;
<a href="https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/">Kubernetes probes</a>;
<a href="https://prometheus.io/docs/practices/histograms/">Prometheus histograms</a>.</p></html>"""
    (ROOT / "report.html").write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
