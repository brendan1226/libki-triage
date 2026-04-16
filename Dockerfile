FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    LIBKI_TRIAGE_DB_PATH=/data/libki-triage.db

VOLUME /data

ENTRYPOINT ["libki-triage"]
CMD ["--help"]
