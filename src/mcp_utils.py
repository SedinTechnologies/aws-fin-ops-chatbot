import os, shlex, logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def _build_mcp_command(
  *,
  port: str,
  asgi_app: str,
  role_arn: str,
  pkg_name: str,
  pkg_version: str = "latest"
):
  package_spec = f"{pkg_name}[streamable-http]"
  package_arg = shlex.quote(f"{package_spec}@{pkg_version}")
  command = " ".join([
    f"MCP_SERVER_PORT={port}",
    f"MCP_SERVER_ASGI_APP={asgi_app}",
    f"/app/scripts/start-mcp-server.sh {shlex.quote(role_arn)} {package_arg}"
  ])
  logger.info(f"Generated MCP command: {command}")
  return command

def build_mcp_connections_for_user(user: Dict[str, Any]) -> List[Dict[str, Any]]:
  logger.info(f"Building MCP connections for user: {user['identifier']}")
  return [
    {
      "name": "aws-cost-explorer-mcp-server",
      "url": "http://localhost:8001/mcp",
      "command": _build_mcp_command(
        port = "8001",
        asgi_app = "awslabs.cost_explorer_mcp_server.server:app.streamable_http_app",
        role_arn = user["aws_role_arn"],
        pkg_name = "awslabs.cost-explorer-mcp-server",
        pkg_version = os.getenv("AWS_COST_EXPLORER_MCP_SERVER_VERSION", "latest")
      ),
    },
    {
      "name": "aws-ccapi-mcp-server",
      "url": "http://localhost:8002/mcp",
      "command": _build_mcp_command(
        port = "8002",
        asgi_app = "awslabs.ccapi_mcp_server.server:mcp.streamable_http_app",
        role_arn = user["aws_role_arn"],
        pkg_name = "awslabs.ccapi-mcp-server",
        pkg_version = os.getenv("AWS_CCAPI_MCP_SERVER_VERSION", "latest")
      ),
    },
    {
      "name": "aws-cloudwatch-mcp-server",
      "url": "http://localhost:8003/mcp",
      "command": _build_mcp_command(
        port = "8003",
        asgi_app = "awslabs.cloudwatch_mcp_server.server:mcp.streamable_http_app",
        role_arn = user["aws_role_arn"],
        pkg_name = "awslabs.cloudwatch-mcp-server",
        pkg_version = os.getenv("AWS_CLOUDWATCH_MCP_SERVER_VERSION", "latest")
      ),
    },
    {
      "name": "aws-billing-mcp-server",
      "url": "http://localhost:8004/mcp",
      "command": _build_mcp_command(
        port = "8004",
        asgi_app = "awslabs.billing_cost_management_mcp_server.server:mcp.streamable_http_app",
        role_arn = user["aws_role_arn"],
        pkg_name = "awslabs.billing-cost-management-mcp-server",
        pkg_version = os.getenv("AWS_BILLING_MCP_SERVER_VERSION", "latest")
      ),
    },
    {
      "name": "aws-cloudtrail-mcp-server",
      "url": "http://localhost:8005/mcp",
      "command": _build_mcp_command(
        port = "8005",
        asgi_app = "awslabs.cloudtrail_mcp_server.server:mcp.streamable_http_app",
        role_arn = user["aws_role_arn"],
        pkg_name = "awslabs.cloudtrail-mcp-server",
        pkg_version = os.getenv("AWS_CLOUDTRAIL_MCP_SERVER_VERSION", "latest")
      ),
    },
    {
      "name": "aws-pricing-mcp-server",
      "url": "http://localhost:8006/mcp",
      "command": _build_mcp_command(
        port = "8006",
        asgi_app = "awslabs.aws_pricing_mcp_server.server:mcp.streamable_http_app",
        role_arn = user["aws_role_arn"],
        pkg_name = "awslabs.aws-pricing-mcp-server",
        pkg_version = os.getenv("AWS_PRICING_MCP_SERVER_VERSION", "latest")
      ),
    }
  ]
