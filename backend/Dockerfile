FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libgl1 \
        libglib2.0-0 \
        libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml

RUN pip install --no-cache-dir --upgrade pip \
    && python - <<'PY'
import subprocess
from pathlib import Path

try:
    import tomllib
except Exception as exc:
    raise SystemExit(f"tomllib not available: {exc}")

data = tomllib.loads(Path("pyproject.toml").read_text())

deps = data.get("project", {}).get("dependencies", [])
if not deps:
    raise SystemExit("No dependencies found in pyproject.toml")

subprocess.check_call(["pip", "install", "--no-cache-dir", *deps])
PY

COPY . /app

EXPOSE 8000

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
