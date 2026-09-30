# CPU image. On the NVIDIA edge node swap the base for
# nvcr.io/nvidia/cuda:12.x-cudnn-runtime and onnxruntime for onnxruntime-gpu.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project --extra lpr
COPY src ./src
RUN uv sync --no-dev --extra lpr
# so `python -m ivaas_pipeline enroll ...` works as documented, not only via the venv path
ENV PATH="/app/.venv/bin:$PATH"
# -m: fast-alpr caches its ONNX weights under $HOME, and a user without one cannot
# start (the pipeline crash-looped on it). Same fix as the API image.
RUN useradd -r -m -d /home/ivaas ivaas
# the event spool and the node's credential live here; the pipeline runs as ivaas, so
# the directory must be its own (a fresh volume inherits this ownership)
RUN mkdir -p /var/lib/ivaas && chown ivaas /var/lib/ivaas
USER ivaas
# Fetch the plate-reader weights at build time: an edge node on site may have no
# outbound internet when it starts.
RUN /app/.venv/bin/python -c \
    "from ivaas_pipeline.adapters.fast_alpr_reader import FastAlprPlateReader; FastAlprPlateReader()"
CMD ["/app/.venv/bin/python", "-m", "ivaas_pipeline"]
