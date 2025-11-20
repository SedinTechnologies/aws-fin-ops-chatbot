#!/bin/bash
set -e

# Exporting app AWS credentials
export $(cat /app/aws.env | xargs)

ROLE_ARN=$1
shift

# Assume the specified role and extract temporary credentials
CREDS=$(aws sts assume-role --role-arn "$ROLE_ARN" --role-session-name uvx-session --query "Credentials" --output json)
export AWS_ACCESS_KEY_ID="$(echo "$CREDS" | jq -r '.AccessKeyId')"
export AWS_SECRET_ACCESS_KEY="$(echo "$CREDS" | jq -r '.SecretAccessKey')"
export AWS_SESSION_TOKEN="$(echo "$CREDS" | jq -r '.SessionToken')"

# Default MCP settings
export AWS_API_MCP_TRANSPORT="${AWS_API_MCP_TRANSPORT:-streamable-http}"
export AUTH_TYPE="${AUTH_TYPE:-no-auth}"
export AWS_API_MCP_HOST="${AWS_API_MCP_HOST:-0.0.0.0}"
export AWS_API_MCP_PORT="${AWS_API_MCP_PORT:-8000}"
export AWS_API_MCP_ALLOWED_HOSTS="${AWS_API_MCP_ALLOWED_HOSTS:-${AWS_API_MCP_HOST}}"
export AWS_API_MCP_ALLOWED_ORIGINS="${AWS_API_MCP_ALLOWED_ORIGINS:-${AWS_API_MCP_HOST}}"

# MOST IMPORTANT FIX → prepend uvx
exec env \
  AWS_API_MCP_TRANSPORT="${AWS_API_MCP_TRANSPORT}" \
  AUTH_TYPE="${AUTH_TYPE}" \
  AWS_API_MCP_HOST="${AWS_API_MCP_HOST}" \
  AWS_API_MCP_PORT="${AWS_API_MCP_PORT}" \
  AWS_API_MCP_ALLOWED_HOSTS="${AWS_API_MCP_ALLOWED_HOSTS}" \
  AWS_API_MCP_ALLOWED_ORIGINS="${AWS_API_MCP_ALLOWED_ORIGINS}" \
  uvx "$@"
