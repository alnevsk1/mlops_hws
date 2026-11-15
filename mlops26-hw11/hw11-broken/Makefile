export PYTHONUTF8 := 1
export PYTHONIOENCODING := utf-8

.PHONY: check serve
check:
	uv run --no-sync ruff check .
	uv run --no-sync ruff format --check .
	uv run --no-sync python -m unittest discover -s tests -v

serve:
	uv run --no-sync python -m src.service --config params.yaml

.PHONY: check-evidence
check-evidence:
	uv run --no-sync python -m scripts.check_evidence
