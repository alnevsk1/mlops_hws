"""Prefect-процесс: подготовка, артефакт, полный gate и публикация."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path

os.environ["PREFECT_HOME"] = str(Path(__file__).resolve().parent / ".prefect")
os.environ["PREFECT_SERVER_ANALYTICS_ENABLED"] = "false"
from prefect import flow, task
from prefect.logging import get_run_logger

from release import atomic_write, cache_key, file_sha256, publish_once, sha256

PROJECT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n",
    )


def task_cache_key(context: object, parameters: dict) -> str:
    return cache_key(
        Path(parameters["data_path"]),
        {**parameters["config"], "mode": parameters["mode"]},
    )


@task(cache_key_fn=task_cache_key, persist_result=True)
def prepare_data(data_path: str, config: dict, mode: str) -> dict:
    """Проверить вход и фактически построить промпты без загрузки весов."""
    path = Path(data_path)
    raw = path.read_bytes()
    if mode == "stub":
        rows = len(raw.splitlines())
        maximum = 0
    else:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            config["base_model"],
            revision=config["tokenizer_revision"],
            local_files_only=True,
        )
        rows = 0
        maximum = 0
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if (
                not isinstance(item.get("id"), str)
                or not isinstance(item.get("input"), str)
                or not item["input"].strip()
            ):
                raise ValueError(f"Некорректная строка подготовки: {number}")
            text = tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": config["system"]},
                    {"role": "user", "content": item["input"]},
                ],
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=config["enable_thinking"],
            )
            length = len(tokenizer(text)["input_ids"])
            if length > config["max_length"]:
                raise ValueError(
                    f"Строка {number}: {length} > max_length={config['max_length']}"
                )
            maximum = max(maximum, length)
            rows += 1
    get_run_logger().info(
        "PREPARED %s rows=%d max_tokens=%d", sha256(raw), rows, maximum
    )
    return {
        "input_sha256": sha256(raw),
        "rows": rows,
        "max_tokens": maximum,
        "cache_key": cache_key(path, {**config, "mode": mode}),
    }


@task
def obtain_artifact(workspace: str, mode: str, previous_project: str) -> dict:
    """Прочитать готовый run; это получение описания, не повторное обучение."""
    if mode == "stub":
        root = Path(workspace)
        return {
            "manifest": str(root / "manifest-B.json"),
            "report": str(root / "report-B.json"),
        }
    previous = Path(previous_project)
    candidate = read_json(previous / "metrics" / "candidate-model.json")
    baseline = read_json(previous / "evaluation" / "baseline.json")
    if candidate.get("kind") != "adapter" or not candidate.get("run_id"):
        raise ValueError("Нет закреплённого training run кандидата")
    sweep = read_json(previous / "metrics" / "sweep.json")
    if candidate["run_id"] not in {row["run_id"] for row in sweep["rows"]}:
        raise ValueError("Training run кандидата отсутствует в сохранённом sweep")
    if candidate.get("uri") != f"models:/{candidate['name']}/{candidate['version']}":
        raise ValueError("URI версии реестра не совпадает с описанием кандидата")
    get_run_logger().info("REUSED_TRAINING_RUN %s", candidate["run_id"])
    return {"candidate": candidate, "baseline": baseline, "previous": str(previous)}


def load_quality_gate(previous: Path):
    source = previous / "evaluation" / "solution.py"
    spec = importlib.util.spec_from_file_location("previous_quality_gate", source)
    if spec is None or spec.loader is None:
        raise ValueError("Не удалось загрузить quality_gate ДЗ 6+7")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.quality_gate


def prior_fingerprint(value: object) -> str:
    """Повторить канонизацию evaluation.contract.fingerprint из ДЗ 6+7."""
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode(
            "utf-8"
        )
    )


def _evaluate_gate(
    artifact: dict, workspace: str, mode: str, critical_slices: list[str]
) -> dict:
    """Пересчитать решение из полных отчётов и сохранить связанные исходники."""
    root = Path(workspace)
    if mode == "stub":
        report = read_json(Path(artifact["report"]))
        write_json(root / "release-report.json", report)
        return report
    previous = Path(artifact["previous"])
    if (
        critical_slices
        != read_json(previous / "evaluation" / "params.json")["critical_slices"]
    ):
        raise ValueError("Критические срезы отличаются от условий оценки")
    sources = {}
    reports = {}
    for role in ("baseline", "candidate"):
        report_path = previous / "metrics" / f"{role}-eval.json"
        outputs_path = previous / "metrics" / f"{role}-outputs.json"
        report = read_json(report_path)
        outputs = json.loads(outputs_path.read_text(encoding="utf-8"))
        if report.get("outputs_sha256") != prior_fingerprint(outputs):
            raise ValueError(f"{role}: сырые ответы изменились после оценки")
        reports[role] = report
        sources[role] = {
            "report_path": report_path.relative_to(previous).as_posix(),
            "report_sha256": sha256(report_path.read_bytes()),
            "outputs_path": outputs_path.relative_to(previous).as_posix(),
            "outputs_sha256": sha256(outputs_path.read_bytes()),
        }
    if reports["baseline"].get("model") != artifact["baseline"]:
        raise ValueError("Baseline отчёта не совпадает с закреплённым baseline")
    if reports["candidate"].get("model") != artifact["candidate"]:
        raise ValueError("Candidate отчёта не совпадает с training run")
    candidate = reports["candidate"]
    try:
        decision = load_quality_gate(previous)(
            reports["baseline"], candidate, critical_slices
        )
        passed, reason = decision["passed"], None
    except ValueError as exc:
        decision, passed, reason = None, False, str(exc)
    result = {
        "status": "complete"
        if all(r.get("status") == "complete" for r in reports.values())
        else "incomplete",
        "passed": passed,
        "reason": reason,
        "evaluation_run_id": candidate["evaluation_run_id"],
        "model_id": artifact["candidate"]["run_id"],
        "protocol_sha256": candidate["protocol_sha256"],
        "golden_sha256": candidate["golden_sha256"],
        "metrics": {
            "accuracy": candidate["overall"]["accuracy"],
            "format_rate": candidate["overall"]["format_rate"],
        },
        "sources": sources,
        "decision": decision,
        "source_project": previous.relative_to(PROJECT.parent.parent).as_posix(),
        "synthetic": False,
    }
    write_json(root / "release-report.json", result)
    get_run_logger().info("QUALITY_GATE passed=%s reason=%s", passed, reason)
    return result


@task
def evaluate_gate(
    artifact: dict, workspace: str, mode: str, critical_slices: list[str]
) -> dict:
    try:
        return _evaluate_gate(artifact, workspace, mode, critical_slices)
    except Exception as exc:
        write_json(
            Path(workspace) / "release-report.json",
            {
                "status": "incomplete",
                "passed": False,
                "reason": str(exc),
                "synthetic": mode == "stub",
            },
        )
        raise


@task
def publish_candidate(
    artifact: dict, report: dict, workspace: str, mode: str, fail_after_pointer: bool
) -> dict:
    root = Path(workspace)
    if not report["passed"]:
        return {"status": "rejected", "reason": report["reason"]}
    if mode == "stub":
        manifest = read_json(Path(artifact["manifest"]))
        artifact_root = root
    else:
        from candidate_manifest import build_candidate_manifest

        manifest = build_candidate_manifest(
            Path(artifact["previous"]), root, artifact["candidate"]
        )
        if (
            manifest["model_id"] != report["model_id"]
            or manifest["protocol_sha256"] != report["protocol_sha256"]
            or manifest["golden_sha256"] != report["golden_sha256"]
        ):
            raise ValueError("Manifest кандидата не связан с полным eval")
        artifact_root = Path(manifest["artifact_uri"])
        report = {
            **report,
            "model_sha256": manifest["model_sha256"],
            "runtime_sha256": manifest["runtime_sha256"],
        }
        write_json(root / "release-report.json", report)
    identifier, created = publish_once(
        manifest, report, root, artifact_root, fail_after_pointer
    )
    return {
        "status": "published" if created else "already-published",
        "release_id": identifier,
    }


@flow(name="hw8910-release")
def release_flow(
    workspace: str,
    mode: str,
    data_path: str,
    config: dict,
    previous_project: str,
    critical_slices: list[str],
    fail_after_prepare: bool = False,
    fail_after_pointer: bool = False,
) -> dict:
    prepared = prepare_data(data_path, config, mode)
    if fail_after_prepare:
        raise RuntimeError("Инъекция сбоя после подготовки")
    artifact = obtain_artifact(workspace, mode, previous_project)
    report = evaluate_gate(artifact, workspace, mode, critical_slices)
    outcome = publish_candidate(artifact, report, workspace, mode, fail_after_pointer)
    return {"prepared": prepared, **outcome}


def main() -> None:
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--mode", choices=("stub", "real"), required=True)
    parser.add_argument("--fail-after-prepare", action="store_true")
    parser.add_argument("--fail-after-pointer", action="store_true")
    args = parser.parse_args()
    params = yaml.safe_load((PROJECT / "params.yaml").read_text(encoding="utf-8"))
    previous = (PROJECT / params["previous_project"]).resolve()
    if args.mode == "stub":
        data_path = args.workspace / "input.txt"
        config = read_json(args.workspace / "config.json")
    else:
        data_path = previous / params["evaluation"]["golden"]
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer

        baseline = read_json(previous / "evaluation" / "baseline.json")
        snapshot = Path(
            snapshot_download(
                baseline["name"],
                revision=baseline["revision"],
                local_files_only=True,
            )
        )
        tokenizer = AutoTokenizer.from_pretrained(
            baseline["name"],
            revision=baseline["revision"],
            local_files_only=True,
        )
        evaluation = read_json(previous / "evaluation" / "params.json")
        config = {
            **params["prepare"],
            "base_model": baseline["name"],
            "tokenizer_revision": baseline["revision"],
            "tokenizer_sha256": file_sha256(snapshot / "tokenizer.json"),
            "template_sha256": sha256(
                json.dumps(
                    tokenizer.chat_template,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ),
            "system": evaluation["system"],
            "enable_thinking": evaluation["enable_thinking"],
        }
    result = release_flow(
        str(args.workspace.resolve()),
        args.mode,
        str(data_path.resolve()),
        config,
        str(previous),
        params["evaluation"]["critical_slices"],
        args.fail_after_prepare,
        args.fail_after_pointer,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
