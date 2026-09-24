FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# the API runs uploaded-video analysis with the pipeline package (sibling path dependency)
COPY pipeline /pipeline
COPY api/pyproject.toml api/uv.lock* ./
RUN sed -i 's|path = "../pipeline"|path = "/pipeline"|' pyproject.toml && uv sync --no-dev --no-install-project
COPY api/src ./src
COPY api/alembic.ini ./alembic.ini
COPY api/migrations ./migrations
RUN uv sync --no-dev
# -m: fast-alpr caches its ONNX weights under $HOME, and a user without one cannot start
RUN useradd -r -m -d /home/ivaas ivaas
USER ivaas
# Fetch the plate-recognition weights now rather than on the first analysis: this image
# is deployed on site, where outbound internet is not a safe assumption.
RUN /app/.venv/bin/python -c \
    "from ivaas_pipeline.adapters.fast_alpr_reader import FastAlprPlateReader; FastAlprPlateReader()"
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "ivaas.adapters.http.app:app", "--host", "0.0.0.0", "--port", "8000"]
