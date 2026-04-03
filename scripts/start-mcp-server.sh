#!/bin/bash
set -e

# Exporting app AWS credentials (ignore blank lines/comments)
set -a
# shellcheck disable=SC1091
[ -f /app/aws-creds.env ] && . /app/aws-creds.env
set +a

ROLE_ARN=$1
shift

# Assume the specified role and extract temporary credentials
CREDS=$(aws sts assume-role --role-arn "$ROLE_ARN" --role-session-name uvx-session --query "Credentials" --output json)
export AWS_ACCESS_KEY_ID="$(echo "$CREDS" | jq -r '.AccessKeyId')"
export AWS_SECRET_ACCESS_KEY="$(echo "$CREDS" | jq -r '.SecretAccessKey')"
export AWS_SESSION_TOKEN="$(echo "$CREDS" | jq -r '.SessionToken')"

echo "Starting MCP server with uvicorn for streamable-http..." >&2
echo "DEBUG: AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION}" >&2
# Extract package name from args (assumes format package@version or package[extra]@version)
PACKAGE_ARG="$1"
shift

# Convert package@version to PEP 508 requirement for --with
# 1. Remove @latest (implies latest version, so no constraint needed)
# 2. Replace remaining @ with == (for specific versions)
WITH_ARG="${PACKAGE_ARG//@latest/}"
WITH_ARG="${WITH_ARG//@/==}"

export MCP_SERVER_HOST="127.0.0.1"
export MCP_SERVER_PORT="$MCP_SERVER_PORT"
export MCP_SERVER_URL="http://${MCP_SERVER_HOST}:${MCP_SERVER_PORT}/mcp"

# We need to run uvicorn in the context of the package dependencies
exec env \
  AWS_API_MCP_TRANSPORT="streamable-http" \
  AUTH_TYPE="no-auth" \
  AWS_API_MCP_HOST="${MCP_SERVER_HOST}" \
  AWS_API_MCP_BIND_HOST="${MCP_SERVER_HOST}" \
  AWS_API_MCP_CLIENT_HOST="${MCP_SERVER_HOST}" \
  AWS_API_MCP_PORT="${MCP_SERVER_PORT}" \
  AWS_API_MCP_URL="${MCP_SERVER_URL}" \
  AWS_API_MCP_ALLOWED_HOSTS="${MCP_SERVER_HOST}" \
  AWS_API_MCP_ALLOWED_ORIGINS="${MCP_SERVER_URL}" \
  AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}" \
  uvx --verbose --with "$WITH_ARG" uvicorn --factory "$MCP_SERVER_ASGI_APP" --host "$MCP_SERVER_HOST" --port "$MCP_SERVER_PORT"
