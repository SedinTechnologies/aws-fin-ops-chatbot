FROM python:3.13-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends awscli jq \
  && rm -rf /var/lib/apt/lists/*

# Install uv (lightweight universal package runner)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependencies and app
COPY requirements.txt uv.lock ./
RUN pip install --no-cache-dir -r requirements.txt

# # Install required MCP server packages
# Please update in app.py if versions are changed here
ENV AWS_COST_EXPLORER_MCP_SERVER_VERSION=0.0.13
ENV CCAPI_MCP_SERVER_VERSION=1.0.10
RUN uvx awslabs.cost-explorer-mcp-server@$AWS_COST_EXPLORER_MCP_SERVER_VERSION \
  && uvx awslabs.ccapi-mcp-server@$CCAPI_MCP_SERVER_VERSION

COPY . .

CMD ["chainlit", "run", "src/app.py"]
