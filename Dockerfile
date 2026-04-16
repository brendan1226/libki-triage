FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md uv.lock ./
COPY src ./src

RUN uv sync --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"

# Pre-download the fastembed model so first container run doesn't pay the
# ~90 MB cold download. Model is baked into the image layer.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

ENV LIBKI_TRIAGE_DB_PATH=/data/libki-triage.db

VOLUME /data
EXPOSE 8000

ENTRYPOINT ["libki-triage"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
