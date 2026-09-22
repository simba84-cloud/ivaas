FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project
COPY src ./src
RUN uv sync --no-dev
RUN useradd -r ivaas
USER ivaas
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "ivaas.adapters.http.app:app", "--host", "0.0.0.0", "--port", "8000"]
