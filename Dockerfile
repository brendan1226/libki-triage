FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md uv.lock ./
COPY src ./src

RUN uv sync --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH" \
    LIBKI_TRIAGE_DB_PATH=/data/libki-triage.db

VOLUME /data
EXPOSE 8000

ENTRYPOINT ["libki-triage"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
