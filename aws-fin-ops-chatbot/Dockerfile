FROM python:3.13-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends awscli jq \
  && rm -rf /var/lib/apt/lists/*

# Install uv (lightweight universal package runner)
RUN pip install --no-cache-dir uv

# Install required MCP server packages
ENV AWS_COST_EXPLORER_MCP_SERVER_VERSION=latest
ENV CCAPI_MCP_SERVER_VERSION=latest
ENV AWS_CLOUDWATCH_MCP_SERVER_VERSION=latest
ENV AWS_BILLING_MCP_SERVER_VERSION=latest
ENV AWS_CLOUDTRAIL_MCP_SERVER_VERSION=latest
ENV AWS_PRICING_MCP_SERVER_VERSION=latest
RUN uvx awslabs.cost-explorer-mcp-server@$AWS_COST_EXPLORER_MCP_SERVER_VERSION \
  && uvx awslabs.ccapi-mcp-server@$CCAPI_MCP_SERVER_VERSION \
  && uvx awslabs.cloudwatch-mcp-server@$AWS_CLOUDWATCH_MCP_SERVER_VERSION \
  && uvx awslabs.billing-cost-management-mcp-server@$AWS_BILLING_MCP_SERVER_VERSION \
  && uvx awslabs.cloudtrail-mcp-server@$AWS_CLOUDTRAIL_MCP_SERVER_VERSION \
  && uvx awslabs.aws-pricing-mcp-server@$AWS_PRICING_MCP_SERVER_VERSION

WORKDIR /app

# Setting up UV related paths
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy dependencies and app
COPY pyproject.toml uv.lock* ./
RUN uv sync --locked

COPY . .

CMD ["chainlit", "run", "src/app.py"]
