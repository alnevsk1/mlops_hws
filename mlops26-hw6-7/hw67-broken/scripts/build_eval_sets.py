"""Сборка наборов проверки части B: golden, behavioral и training-index.

Вход — сплиты ДЗ 3 v2 (data/source под DVC: train = вход tokenize ДЗ 4,
test = nestjs/nest, в обучение не попадал) и ручной разбор
evaluation/review/golden-review.csv. Выход — data/eval/*.jsonl, стадия eval_sets.
Все пути и квоты — в params.yaml, секция eval_sets.
"""
import csv
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch

from evaluation.contract import validate_golden
from src.common import load_params, save_json

# "Changed files (3):" или "Changed files (14 total, showing 10):" — берём полное число.
FILES = re.compile(r"\n\nChanged files \((\d+)(?: total, showing \d+)?\):")


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def label(row):
    return json.loads(row["messages"][2]["content"])["type"]


def slices_for(text, kind):
    """Срезы пересекаются: тип, «не fix», число файлов, есть ли тело сообщения."""
    m = FILES.search(text)
    n_files = int(m.group(1)) if m else 0
    message = text[:m.start()] if m else text
    out = [f"type-{kind}"] + (["not-fix"] if kind != "fix" else [])
    out.append("single-file" if n_files == 1 else "multi-file" if n_files > 1 else "no-files")
    out.append("with-body" if "\n" in message.strip() else "subject-only")
    return out


def main():
    p = load_params()["eval_sets"]
    with Path(p["review"]).open(encoding="utf-8", newline="") as f:
        review = {r["id"]: r for r in csv.DictReader(f)}
    if any(r["verdict"] not in ("keep", "drop") or not r["reason"].strip() for r in review.values()):
        raise SystemExit(f"{p['review']}: verdict только keep/drop, причина обязательна")

    by_type = {}
    for row in sorted(read_jsonl(p["source_test"]), key=lambda r: r["id"]):
        by_type.setdefault(label(row), []).append(row)
    golden = []
    for kind, quota in p["per_type"].items():
        pool = by_type.get(kind, [])
        random.Random(p["seed"]).shuffle(pool)
        taken = []
        for row in pool:
            if len(taken) == quota:
                break
            verdict = review.get(row["id"])
            # Каждый взятый пример и каждый пропущенный до него — с ручным решением.
            if verdict is None:
                raise SystemExit(f"{row['id']} ({kind}) не проверен вручную: добавьте в {p['review']}")
            if verdict["type"] != kind:
                raise SystemExit(f"{row['id']}: в разборе тип {verdict['type']}, в источнике {kind}")
            if verdict["verdict"] == "keep":
                taken.append((row, verdict))
        if len(taken) < quota:
            raise SystemExit(f"{kind}: проверенных примеров {len(taken)} < квоты {quota}")
        for row, verdict in taken:
            text = row["messages"][1]["content"]
            golden.append(dict(id=row["id"], group_id=row["topic"], input=text, expected=kind,
                               choices=p["choices"], slices=slices_for(text, kind),
                               source=f"ДЗ 3 v2 test ({row['topic']}), коммит {row['id']}; метка — исходный "
                                      f"префикс Conventional Commits; ручной разбор v1: {verdict['reason']}"))

    # Полный индекс train из тех же строк, что токенизировал ДЗ 4; связь — по id примеров в тензоре.
    index = [dict(id=r["id"], group_id=r["topic"], input=r["messages"][1]["content"])
             for r in read_jsonl(p["source_train"])]
    tensor_ids = {e["id"] for e in torch.load(p["tokenized_train"], weights_only=True)["examples"]}
    not_indexed = tensor_ids - {r["id"] for r in index}
    if not_indexed:
        raise SystemExit(f"В {p['tokenized_train']} есть примеры вне индекса: {sorted(not_indexed)[:5]}")

    behavioral = read_jsonl(p["behavioral"])
    proof = validate_golden(golden, index, behavioral)
    out = Path(p["out_dir"])
    write_jsonl(out / "golden.jsonl", golden)
    write_jsonl(out / "behavioral.jsonl", behavioral)
    write_jsonl(out / "training-index.jsonl", index)

    names = sorted({s for g in golden for s in g["slices"]})
    summary = {**proof, "n_tensor_train": len(tensor_ids),
               "index_rows_not_in_tensor": len({r["id"] for r in index} - tensor_ids),
               "reviewed": len(review), "dropped_on_review": sum(r["verdict"] == "drop" for r in review.values()),
               "per_type": {k: sum(g["expected"] == k for g in golden) for k in p["per_type"]},
               "slices": {s: sum(s in g["slices"] for g in golden) for s in names}}
    save_json("metrics/eval_sets.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
