"""Закрепить исторически принятую baseline-версию без ложного PASS кандидата."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from release import atomic_write, canonical, file_sha256, sha256, verify_files

PROJECT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_manifest(previous: Path, max_prompt_chars: int) -> dict:
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer

    baseline = read_json(previous / "evaluation" / "baseline.json")
    report = read_json(previous / "metrics" / "baseline-eval.json")
    config = read_json(previous / "evaluation" / "params.json")
    old_params = yaml.safe_load((previous / "params.yaml").read_text(encoding="utf-8"))
    if report["model"] != baseline:
        raise ValueError("Baseline-отчёт не соответствует закреплённой версии")
    revision = baseline["revision"]
    if len(revision) != 40:
        raise ValueError("Нужна точная ревизия базы")
    snapshot = Path(
        snapshot_download(baseline["name"], revision=revision, local_files_only=True)
    )
    file_hashes = {
        path.name: file_sha256(path)
        for path in sorted(snapshot.iterdir())
        if path.is_file()
    }
    tokenizer = AutoTokenizer.from_pretrained(
        baseline["name"], revision=revision, local_files_only=True
    )
    runtime = {
        "base_model": baseline["name"],
        "base_revision": revision,
        "tokenizer_sha256": file_hashes["tokenizer.json"],
        "template_sha256": sha256(canonical(tokenizer.chat_template)),
        "generation": {
            "system": config["system"],
            "do_sample": False,
            "seed": config["seed"],
            "max_new_tokens": config["max_new_tokens"],
            "enable_thinking": config["enable_thinking"],
        },
        "dtype": old_params["model"]["dtype"],
        "max_input_chars": max_prompt_chars,
    }
    manifest = {
        "schema_version": 1,
        "model_id": f"{baseline['name']}@{revision}",
        "artifact_uri": f"hf://{baseline['name']}@{revision}",
        "model_sha256": sha256(canonical(file_hashes)),
        "protocol_sha256": report["protocol_sha256"],
        "golden_sha256": report["golden_sha256"],
        "runtime_sha256": sha256(canonical(runtime)),
        "runtime": runtime,
        "file_hashes": file_hashes,
        "origin": "historical-baseline",
        "baseline_evaluation_run_id": report["evaluation_run_id"],
        "baseline_report_sha256": sha256(
            (previous / "metrics" / "baseline-eval.json").read_bytes()
        ),
    }
    verify_files(manifest, snapshot)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    params = yaml.safe_load((PROJECT / "params.yaml").read_text(encoding="utf-8"))
    previous = (PROJECT / params["previous_project"]).resolve()
    target = args.workspace / "accepted.json"
    if target.exists():
        raise FileExistsError(
            "Принятая версия уже задана; baseline-init её не перезаписывает"
        )
    manifest = build_manifest(previous, params["api"]["max_prompt_chars"])
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        target,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode(
            "utf-8"
        )
        + b"\n",
    )
    print(
        json.dumps(
            {
                "model_id": manifest["model_id"],
                "model_sha256": manifest["model_sha256"],
                "runtime_sha256": manifest["runtime_sha256"],
                "origin": manifest["origin"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
