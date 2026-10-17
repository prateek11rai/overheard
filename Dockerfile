FROM python:3.13.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --create-home app && mkdir -p /home/app/data && chown app /home/app/data
USER app
ENV PATH="/app/.venv/bin:$PATH" OVERHEARD_HOST=0.0.0.0 OVERHEARD_DATA_DIR=/home/app/data
EXPOSE 8000
CMD ["overheard", "web"]
