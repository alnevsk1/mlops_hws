.PHONY: install sample tokenize check clean

# Windows: без этого print в пайп уходит в кодировке консоли (cp1251),
# и кириллица в логе стадии превращается в знаки вопроса.
export PYTHONIOENCODING := utf-8

install:
	uv sync

# Стадия 0, только для преподавателя: parquet курса -> data/train.jsonl, data/val.jsonl.
# У студента вход другой — его датасет из ДЗ 3, положенный в те же файлы.
sample:
	uv run python -m scripts.make_sample

# Стадия tokenize: JSONL -> токенизированный датасет + метрики + отчёт.
# Та же команда объявлена стадией в dvc.yaml — оттуда её и запускает dvc repro.
tokenize:
	uv run python -m src.tokenize_data

check:
	bash tests/check.sh

clean:
	rm -rf data metrics/tokenize.json docs/tokenize_report.md
