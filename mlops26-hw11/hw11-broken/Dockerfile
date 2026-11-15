FROM ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff AS uv
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONUTF8=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY src ./src
COPY scripts ./scripts
COPY artifacts ./artifacts
COPY params.yaml ./
USER 10001:10001
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "python", "-m", "src.service", "--config", "/config/params.yaml"]
