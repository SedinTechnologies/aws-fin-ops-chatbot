import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import chainlit as cl
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def enabled_mcp_connections_list() -> List[Dict[str, Any]]:
  logger.info(f"Generating MCP connections for user")
  # Using host field to mitigate DNS Rebinding Protection
  return [
    { "name": "aws-api-mcp-server", "url": "http://mcp-servers:8000/mcp", "host": "127.0.0.1:8000" },
    { "name": "aws-documentation-mcp-server", "url": "http://mcp-servers:8001/mcp", "host": "127.0.0.1:8001" },
    { "name": "aws-pricing-mcp-server", "url": "http://mcp-servers:8002/mcp", "host": "127.0.0.1:8002" },
    { "name": "aws-billing-cost-management-mcp-server", "url": "http://mcp-servers:8003/mcp", "host": "127.0.0.1:8003" },
    { "name": "aws-cloudtrail-mcp-server", "url": "http://mcp-servers:8004/mcp", "host": "127.0.0.1:8004" },
    { "name": "aws-iac-mcp-server", "url": "http://mcp-servers:8005/mcp", "host": "127.0.0.1:8005" }
  ]

@dataclass
class MCPToolEntry:
  name: str
  tool: BaseTool
  schema: Dict[str, Any]
  description: str
  server_name: str

# Global registry to track MCP clients/tools across sessions
# Key: mcp_name, Value: (runtime, entries)
MCP_CLIENT_CACHE: Dict[str, Tuple[Dict[str, Any], List[MCPToolEntry]]] = {}

async def _setup_server(connection_meta: Dict[str, Any]) -> Tuple[Dict[str, Any], List[MCPToolEntry]]:
  name = connection_meta["name"]

  if name in MCP_CLIENT_CACHE:
    return MCP_CLIENT_CACHE[name]

  url = connection_meta.get('url')
  if not url:
    raise ValueError(f"MCP connection '{name}' requires a 'url'")

  connection = {
    "transport": "streamable_http",
    "url": url,
    "headers": {"Host": connection_meta.get("host")}
  }

  try:
    tools = await load_mcp_tools(
      None,
      connection=connection,
      server_name=name
    )
  except Exception as tool_load_exc:
     logger.error(f"Failed to load MCP tools for {name} at {url}: {tool_load_exc}")
     raise tool_load_exc

  for tool in tools:
    tool.handle_tool_error = True

  entries = [_build_tool_entry(tool, name) for tool in tools]
  logger.info("Registered %s tools for remote MCP '%s'", len(entries), name)

  runtime = {
    "name": name,
    "connection": connection,
    "transport": "streamable_http"
  }

  MCP_CLIENT_CACHE[name] = (runtime, entries)
  return runtime, entries

def _build_tool_entry(tool: BaseTool, server_name: str) -> MCPToolEntry:
  args_schema = getattr(tool, "args_schema", None)
  parameters: Dict[str, Any]
  if args_schema is None:
    parameters = {"type": "object", "properties": {}}
  else:
    if hasattr(args_schema, "model_json_schema"):
      parameters = args_schema.model_json_schema()
    elif hasattr(args_schema, "schema"):
      parameters = args_schema.schema()
    else:
      parameters = {"type": "object", "properties": {}}
  return MCPToolEntry(
    name=tool.name,
    tool=tool,
    schema=parameters,
    description=tool.description or "",
    server_name=server_name
  )

async def _get_all_tool_entries(user: cl.User) -> List[MCPToolEntry]:
  if not user:
    return []
  connections = (user.metadata or {}).get("mcp_connections", [])
  all_entries = []
  for conn in connections:
    try:
      _, tool_entries = await _setup_server(conn)
      all_entries.extend(tool_entries)
    except Exception:
      logger.exception("Failed to initialize or fetch tools for '%s'", conn.get("name"))
  return all_entries

async def fetch_registered_mcp_tools_for_user(user: cl.User) -> List[Dict[str, Any]]:
  entries = await _get_all_tool_entries(user)
  if not entries:
    raise RuntimeError("MCP tool registry is empty for this session.")
  return [{
    "type": "function",
    "function": {
      "name": entry.name,
      "description": entry.description,
      "parameters": entry.schema
    }
  } for entry in entries]

async def get_configured_mcp_tools(user: cl.User) -> List[BaseTool]:
  entries = await _get_all_tool_entries(user)
  return [entry.tool for entry in entries]
