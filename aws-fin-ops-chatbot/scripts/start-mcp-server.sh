#!/bin/bash
set -e

# Exporting app AWS credentials (ignore blank lines/comments)
set -a
# shellcheck disable=SC1091
[ -f /app/aws.env ] && . /app/aws.env
set +a

ROLE_ARN=$1
shift

# Assume the specified role and extract temporary credentials
CREDS=$(aws sts assume-role --role-arn "$ROLE_ARN" --role-session-name uvx-session --query "Credentials" --output json)
export AWS_ACCESS_KEY_ID="$(echo "$CREDS" | jq -r '.AccessKeyId')"
export AWS_SECRET_ACCESS_KEY="$(echo "$CREDS" | jq -r '.SecretAccessKey')"
export AWS_SESSION_TOKEN="$(echo "$CREDS" | jq -r '.SessionToken')"

# Default MCP settings
ENFORCE_LOCAL_MCP=$(echo "${ENFORCE_LOCAL_MCP:-true}" | tr '[:upper:]' '[:lower:]')
AWS_API_MCP_TRANSPORT="${AWS_API_MCP_TRANSPORT:-streamable-http}"
AUTH_TYPE="${AUTH_TYPE:-no-auth}"
AWS_API_MCP_HOST="${AWS_API_MCP_HOST:-0.0.0.0}"
AWS_API_MCP_BIND_HOST="${AWS_API_MCP_BIND_HOST:-${AWS_API_MCP_HOST}}"
AWS_API_MCP_CLIENT_HOST="${AWS_API_MCP_CLIENT_HOST:-${AWS_API_MCP_HOST}}"
AWS_API_MCP_PORT="${AWS_API_MCP_PORT:-8000}"
AWS_API_MCP_URL="${AWS_API_MCP_URL:-http://${AWS_API_MCP_CLIENT_HOST}:${AWS_API_MCP_PORT}/mcp}"
AWS_API_MCP_ALLOWED_HOSTS="${AWS_API_MCP_ALLOWED_HOSTS:-${AWS_API_MCP_CLIENT_HOST}}"
AWS_API_MCP_ALLOWED_ORIGINS="${AWS_API_MCP_ALLOWED_ORIGINS:-${AWS_API_MCP_URL}}"

if [ "$ENFORCE_LOCAL_MCP" = "true" ]; then
  AWS_API_MCP_HOST="127.0.0.1"
  AWS_API_MCP_BIND_HOST="127.0.0.1"
  AWS_API_MCP_CLIENT_HOST="127.0.0.1"
  AWS_API_MCP_URL="http://127.0.0.1:${AWS_API_MCP_PORT}/mcp"
  AWS_API_MCP_ALLOWED_HOSTS="127.0.0.1"
  AWS_API_MCP_ALLOWED_ORIGINS="$AWS_API_MCP_URL"
fi

export AWS_API_MCP_TRANSPORT
export AUTH_TYPE
export AWS_API_MCP_HOST
export AWS_API_MCP_BIND_HOST
export AWS_API_MCP_CLIENT_HOST
export AWS_API_MCP_PORT
export AWS_API_MCP_URL
export AWS_API_MCP_ALLOWED_HOSTS
export AWS_API_MCP_ALLOWED_ORIGINS

# MOST IMPORTANT FIX → prepend uvx
exec env \
  AWS_API_MCP_TRANSPORT="${AWS_API_MCP_TRANSPORT}" \
  AUTH_TYPE="${AUTH_TYPE}" \
  AWS_API_MCP_HOST="${AWS_API_MCP_HOST}" \
  AWS_API_MCP_BIND_HOST="${AWS_API_MCP_BIND_HOST}" \
  AWS_API_MCP_CLIENT_HOST="${AWS_API_MCP_CLIENT_HOST}" \
  AWS_API_MCP_PORT="${AWS_API_MCP_PORT}" \
  AWS_API_MCP_URL="${AWS_API_MCP_URL}" \
  AWS_API_MCP_ALLOWED_HOSTS="${AWS_API_MCP_ALLOWED_HOSTS}" \
  AWS_API_MCP_ALLOWED_ORIGINS="${AWS_API_MCP_ALLOWED_ORIGINS}" \
  uvx "$@"
