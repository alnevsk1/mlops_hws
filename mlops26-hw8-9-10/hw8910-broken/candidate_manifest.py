"""Сборка manifest кандидата из закреплённой версии MLflow после PASS."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from release import atomic_write, canonical, file_sha256, sha256, verify_files


def build_candidate_manifest(previous: Path, workspace: Path, spec: dict) -> dict:
    from mlflow import MlflowClient, set_tracking_uri
    from transformers import AutoTokenizer

    params = yaml.safe_load((previous / "params.yaml").read_text(encoding="utf-8"))
    set_tracking_uri(params["tracking"]["uri"])
    client = MlflowClient()
    version = client.get_model_version(spec["name"], str(spec["version"]))
    if version.run_id != spec["run_id"]:
        raise ValueError("Версия MLflow Registry не соответствует training run")
    root = Path(client.download_artifacts(spec["run_id"], "model")).resolve()
    info = yaml.safe_load((root / "MLmodel").read_text(encoding="utf-8"))
    relative = info["flavors"]["python_function"]["artifacts"]["adapter"]["path"]
    adapter = (root / relative).resolve()
    if not adapter.is_relative_to(root):
        raise ValueError("Путь адаптера выходит за каталог MLflow")
    old_manifest = json.loads((adapter / "manifest.json").read_text(encoding="utf-8"))
    model = old_manifest["model"]
    baseline = json.loads(
        (previous / "evaluation" / "baseline.json").read_text(encoding="utf-8")
    )
    if model["name"] != baseline["name"] or model["revision"] != baseline["revision"]:
        raise ValueError("База адаптера отличается от зафиксированной baseline")
    file_hashes = {
        path.name: file_sha256(path)
        for path in sorted(adapter.iterdir())
        if path.is_file()
    }
    if "adapter_config.json" not in file_hashes or not any(
        name.endswith(".safetensors") for name in file_hashes
    ):
        raise ValueError("Нет конфигурации или весов адаптера")
    tokenizer = AutoTokenizer.from_pretrained(adapter, local_files_only=True)
    evaluation = json.loads(
        (previous / "evaluation" / "params.json").read_text(encoding="utf-8")
    )
    candidate_report = json.loads(
        (previous / "metrics" / "candidate-eval.json").read_text(encoding="utf-8")
    )
    if candidate_report["model"] != spec:
        raise ValueError("Отчёт оценки относится к другому кандидату")
    runtime = {
        "base_model": model["name"],
        "base_revision": model["revision"],
        "tokenizer_sha256": file_hashes["tokenizer.json"],
        "template_sha256": sha256(canonical(tokenizer.chat_template)),
        "generation": {
            "system": evaluation["system"],
            "do_sample": False,
            "seed": evaluation["seed"],
            "max_new_tokens": evaluation["max_new_tokens"],
            "enable_thinking": evaluation["enable_thinking"],
        },
        "dtype": model["dtype"],
        "max_input_chars": yaml.safe_load(
            (Path(__file__).with_name("params.yaml")).read_text(encoding="utf-8")
        )["api"]["max_prompt_chars"],
        "adapter": True,
    }
    manifest = {
        "schema_version": 1,
        "model_id": spec["run_id"],
        "artifact_uri": str(adapter),
        "source_uri": spec["uri"],
        "model_sha256": sha256(canonical(file_hashes)),
        "protocol_sha256": candidate_report["protocol_sha256"],
        "golden_sha256": candidate_report["golden_sha256"],
        "runtime_sha256": sha256(canonical(runtime)),
        "runtime": runtime,
        "file_hashes": file_hashes,
    }
    verify_files(manifest, adapter)
    target = workspace / "candidate-manifest.json"
    atomic_write(
        target,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode(
            "utf-8"
        )
        + b"\n",
    )
    return manifest
