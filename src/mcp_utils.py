import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def enabled_mcp_connections_list() -> List[Dict[str, Any]]:
  logger.info(f"Generating MCP connections for user")
  # Using host field to mitigate DNS Rebinding Protection
  return [
    { "name": "aws-iac-mcp-server", "url": "http://mcp-servers:8001/mcp", "host": "localhost:8001" },
    { "name": "aws-cloudwatch-mcp-server", "url": "http://mcp-servers:8002/mcp", "host": "localhost:8002" },
    { "name": "aws-billing-cost-management-mcp-server", "url": "http://mcp-servers:8003/mcp", "host": "localhost:8003" },
    { "name": "aws-cloudtrail-mcp-server", "url": "http://mcp-servers:8004/mcp", "host": "localhost:8004" },
    { "name": "aws-pricing-mcp-server", "url": "http://mcp-servers:8005/mcp", "host": "localhost:8005" }
  ]
