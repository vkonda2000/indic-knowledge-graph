# The IKG web app and CLI (docs/08 §E, first step: a local image).
#
# The image holds the code and its dependencies only. The corpus (data/raw, data/processed) is
# mounted from the host by compose.yaml, so a build made with `ikg build --embed` on the host is
# what the container serves, and a Rebuild from the container's admin page writes back to it,
# embeddings included (compose sets IKG_EMBED=on; the model is cached in a volume).
#
#   docker compose up -d --build        # db + app, site at http://127.0.0.1:8000
#   docker compose exec app ikg vectors compare
#   docker compose --profile mcp up -d mcp  # MCP over HTTP at http://127.0.0.1:8001/mcp (needs IKG_MCP_TOKEN, or IKG_MCP_PUBLIC=on)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    IKG_DATA=/app/data \
    IKG_ONTOLOGY=/app/ontology/ikg.ttl \
    FASTEMBED_CACHE_PATH=/home/ikg/.cache/fastembed

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them. [embed] lets an admin Rebuild write
# embeddings like the host build does; without it a Rebuild would drop them. build/ goes too: its stub
# __init__.py is newer than the real one copied below, so setuptools would reinstall the stub.
COPY pyproject.toml ./
RUN mkdir -p src/ikg && touch src/ikg/__init__.py \
    && pip install ".[web,pg,embed,mcp]" \
    && pip uninstall -y indic-knowledge-graph \
    && rm -rf build src *.egg-info

COPY src ./src
COPY ontology ./ontology
RUN pip install --no-deps . \
    && rm -rf build src pyproject.toml \
    && useradd --create-home --uid 1000 ikg \
    && mkdir -p /app/data /home/ikg/.cache/fastembed && chown -R ikg /app/data /home/ikg/.cache
USER ikg

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)"
CMD ["ikg", "serve", "--host", "0.0.0.0", "--port", "8000"]
