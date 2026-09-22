# CPU image. On the NVIDIA edge node swap the base for
# nvcr.io/nvidia/cuda:12.x-cudnn-runtime and onnxruntime for onnxruntime-gpu.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project --extra lpr
COPY src ./src
RUN uv sync --no-dev --extra lpr
RUN useradd -r ivaas
USER ivaas
CMD ["/app/.venv/bin/python", "-m", "ivaas_pipeline"]
