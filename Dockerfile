FROM python:3.13.12-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends awscli jq \
  && rm -rf /var/lib/apt/lists/*

# Install uv (lightweight universal package runner)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Setting up UV related paths
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy dependencies and app
COPY pyproject.toml uv.lock* ./
RUN uv sync --locked

COPY . .

CMD ["chainlit", "run", "src/app.py"]
