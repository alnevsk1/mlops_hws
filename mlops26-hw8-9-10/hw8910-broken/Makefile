export PYTHONIOENCODING := utf-8
PYTHON := uv run --no-sync python
.PHONY: check baseline-init gate-real serve-real bench
check:
	$(PYTHON) -m compileall -q release.py lab.py service.py real_backend.py baseline.py candidate_manifest.py flow.py benchmark.py tests
	$(PYTHON) -m unittest discover -s tests -v

baseline-init:
	$(PYTHON) baseline.py --workspace .lab-real

gate-real:
	$(PYTHON) flow.py --workspace .lab-real --mode real

serve-real:
	$(PYTHON) service.py --workspace .lab-real --port 8091 --warmup-seconds 0 --backend real

bench:
	$(PYTHON) benchmark.py --url http://127.0.0.1:8091 --n 8 --limit 32
