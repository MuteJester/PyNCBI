# PyNCBI MCP server — minimal, non-root, agent-mode by default.
#
#   docker build -t pyncbi .
#   docker run --rm -i -e NCBI_API_KEY=$NCBI_API_KEY pyncbi            # stdio MCP server
#   docker run --rm -v "$PWD/data:/data" pyncbi bundle GSE152418 -o /data/GSE152418/
FROM python:3.12-slim

LABEL org.opencontainers.image.title="PyNCBI" \
      org.opencontainers.image.description="Programmatic & agent-ready engine, CLI and MCP server for NCBI" \
      org.opencontainers.image.source="https://github.com/MuteJester/PyNCBI" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYNCBI_AGENT_MODE=1 \
    PYNCBI_CACHE_DIR=/home/pyncbi/.cache/PyNCBI

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-compile . && rm -rf /src

RUN useradd --create-home --uid 10001 pyncbi \
    && mkdir -p /data "$PYNCBI_CACHE_DIR" \
    && chown -R pyncbi:pyncbi /data /home/pyncbi
USER pyncbi
WORKDIR /data

# `docker run -i pyncbi` speaks MCP over stdio; override the command for any CLI call.
ENTRYPOINT ["pyncbi"]
CMD ["mcp"]
