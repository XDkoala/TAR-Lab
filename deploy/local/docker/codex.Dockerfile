# Keep the upstream agent image and update only the Codex client.
FROM agentic-scientist/agent-cli:latest
USER root
RUN npm install -g @openai/codex@0.169.0 && codex --version
RUN apt-get update -o Acquire::Retries=3 \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
USER agent
