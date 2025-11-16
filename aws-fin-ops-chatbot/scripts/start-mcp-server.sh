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

exec uvx "$@"
