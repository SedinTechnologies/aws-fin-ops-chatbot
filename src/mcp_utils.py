import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def register_mcp_connections_for_user(user: Dict[str, Any]) -> List[Dict[str, Any]]:
  logger.info(f"Generating MCP connections for user")
  return [
    { "name": "aws-iac-mcp-server", "url": "http://mcp-servers:8001/mcp" },
    { "name": "aws-cloudwatch-mcp-server", "url": "http://mcp-servers:8002/mcp" },
    { "name": "aws-billing-cost-management-mcp-server", "url": "http://mcp-servers:8003/mcp" },
    { "name": "aws-cloudtrail-mcp-server", "url": "http://mcp-servers:8004/mcp" },
    { "name": "aws-pricing-mcp-server", "url": "http://mcp-servers:8005/mcp" }
  ]
