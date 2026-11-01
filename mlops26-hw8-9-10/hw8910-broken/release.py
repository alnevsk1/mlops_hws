"""Контракт и атомарная запись локального указателя принятой версии."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HASH_FIELDS = ("model_sha256", "protocol_sha256", "golden_sha256", "runtime_sha256")


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_key(data_path: Path, config: dict) -> str:
    """Идентичность результата подготовки зависит от байтов и полного конфига."""
    return sha256(
        canonical({"data_sha256": sha256(data_path.read_bytes()), "config": config})
    )


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_manifest(manifest: dict) -> None:
    """Проверить самодостаточные метаданные до загрузки артефакта."""
    if not isinstance(manifest, dict):
        raise ValueError("Manifest должен быть объектом")  # noqa: TRY004 — контракт требует ValueError
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
    ):
        raise ValueError("Неверная версия схемы")
    for key in ("model_id", "artifact_uri"):
        if not _nonempty(manifest.get(key)):
            raise ValueError(f"Manifest: нет {key}")
    for key in HASH_FIELDS:
        value = manifest.get(key)
        if not isinstance(value, str) or HEX64.fullmatch(value) is None:
            raise ValueError(f"Manifest: неверный {key}")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not runtime:
        raise ValueError("Manifest: пустой runtime")
    try:
        expected_runtime = sha256(canonical(runtime))
    except (TypeError, ValueError) as exc:
        raise ValueError("Manifest: некорректный runtime") from exc
    if manifest["runtime_sha256"] != expected_runtime:
        raise ValueError("Manifest: runtime_sha256 не совпадает")


def validate_release(manifest: dict, report: dict) -> None:
    """Проверить связанность метаданных; фактические файлы проверяются отдельно."""
    validate_manifest(manifest)
    if not isinstance(report, dict):
        raise ValueError("Report должен быть объектом")  # noqa: TRY004 — контракт требует ValueError
    if report.get("status") != "complete" or report.get("passed") is not True:
        raise ValueError("Кандидат отклонён или оценка неполная")
    if not _nonempty(report.get("evaluation_run_id")):
        raise ValueError("Report: нет evaluation_run_id")
    for key in ("model_id", *HASH_FIELDS):
        if report.get(key) != manifest[key]:
            raise ValueError(f"Report: несовместимый {key}")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("Report: нет metrics")  # noqa: TRY004 — контракт требует ValueError
    for key in ("accuracy", "format_rate"):
        value = metrics.get(key)
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError(f"Report: некорректная метрика {key}")


def verify_files(manifest: dict, artifact_root: Path) -> None:
    """Пересчитать хеши закреплённого списка файлов перед выпуском и загрузкой."""
    files = manifest.get("file_hashes")
    if not isinstance(files, dict) or not files:
        raise ValueError("Нет списка файлов артефакта")
    actual: dict[str, str] = {}
    root = artifact_root.resolve()
    for name, expected in files.items():
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise ValueError("Неверный относительный путь артефакта")
        if ".." in Path(name).parts:
            raise ValueError("Выход за пределы каталога артефакта")
        path = root / name
        if not path.is_file():
            raise ValueError(f"Файл артефакта недоступен: {name}")
        actual[name] = file_sha256(path)
        if actual[name] != expected:
            raise ValueError(f"Хеш файла артефакта изменился: {name}")
    if sha256(canonical(actual)) != manifest.get("model_sha256"):
        raise ValueError("Хеш модели не совпадает со списком файлов")


def atomic_write(target: Path, payload: bytes) -> None:
    """Записать файл через fsync и замену рядом с целью."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish(manifest: dict, report: dict, target: Path) -> None:
    validate_release(manifest, report)
    payload = (
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
        + b"\n"
    )
    if target.exists() and target.read_bytes() == payload:
        return
    atomic_write(target, payload)


def release_id(manifest: dict, report: dict) -> str:
    """Идентичность выпуска связывает модель, runtime и конкретное решение допуска."""
    return sha256(
        canonical(
            {
                "model_id": manifest["model_id"],
                "model_sha256": manifest["model_sha256"],
                "runtime_sha256": manifest["runtime_sha256"],
                "protocol_sha256": manifest["protocol_sha256"],
                "golden_sha256": manifest["golden_sha256"],
                "evaluation_run_id": report["evaluation_run_id"],
            }
        )
    )


def publish_once(
    manifest: dict,
    report: dict,
    workspace: Path,
    artifact_root: Path,
    fail_after_pointer: bool = False,
) -> tuple[str, bool]:
    """Примирить accepted.json и журнал после повтора с одним писателем."""
    verify_files(manifest, artifact_root)
    publish(manifest, report, workspace / "accepted.json")
    if fail_after_pointer:
        raise RuntimeError("Инъекция сбоя после указателя")
    identifier = release_id(manifest, report)
    journal = workspace / "publications.jsonl"
    events = (
        [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        if journal.exists()
        else []
    )
    if identifier in {event["release_id"] for event in events}:
        return identifier, False
    events.append(
        {
            "release_id": identifier,
            "model_id": manifest["model_id"],
            "runtime_sha256": manifest["runtime_sha256"],
            "evaluation_run_id": report["evaluation_run_id"],
        }
    )
    atomic_write(journal, b"".join(canonical(event) + b"\n" for event in events))
    return identifier, True
