import asyncio
import logging
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import chainlit as cl
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.sessions import Connection
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.types import AudioContent, EmbeddedResource, ImageContent, ResourceLink

try:  # Optional dependency
  from langgraph.types import Command as LangGraphCommand
except ImportError:  # pragma: no cover
  LangGraphCommand = None  # type: ignore[assignment]

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ADAPTER_STATE_KEY = "mcp_adapter_state"

@dataclass
class MCPToolEntry:
  name: str
  tool: BaseTool
  schema: Dict[str, Any]
  description: str
  server_name: str

def _expected_connection_names(user: cl.User):
  metadata = getattr(user, "metadata", {}) or {}
  connections = metadata.get("mcp_connections", [])
  return [conn.get("name") for conn in connections if conn.get("name")]

def _registry_ready(registry: dict | None, expected_names: list[str]) -> bool:
  if not registry:
    return False
  servers = registry.get("servers") or {}
  tool_specs = registry.get("tool_specs") or []
  if expected_names:
    server_names = set(servers.keys())
    expected = {name for name in expected_names if name}
    if not expected.issubset(server_names):
      return False
  return bool(servers) and bool(tool_specs)

async def call_tool(tool_name, tool_args):
  guardrails = cl.user_session.get("guardrails")
  user = cl.user_session.get("user")
  session_id = cl.context.session.id
  user_id = getattr(user, "identifier", "unknown")
  registry = cl.user_session.get(ADAPTER_STATE_KEY) or {}
  tools = registry.get("tools") or {}
  entry: MCPToolEntry | None = tools.get(tool_name)
  if entry is None:
    raise Exception(f"No MCP tool registered with name '{tool_name}'")

  if guardrails:
    guardrails.guard_tool_call(
      session_id=session_id,
      user_id=user_id,
      tool_name=tool_name,
      arguments=tool_args or {}
    )

  logger.info(
    "Session ID: %s Calling tool: %s with args: %s",
    session_id,
    tool_name,
    tool_args
  )

  resp_items: List[Dict[str, Any]] = []
  try:
    result = await entry.tool.ainvoke(tool_args or {})
    resp_items = _format_tool_response(result)

    if guardrails:
      guardrails.guard_tool_response(
        session_id=session_id,
        user_id=user_id,
        tool_name=tool_name,
        response=resp_items
      )
  except Exception as exc:  # noqa: BLE001
    traceback.print_exc()
    resp_items.append({"type": "text", "text": str(exc)})

  return resp_items

async def fetch_registered_mcp_tools_for_user(user: cl.User):
  expected_names = _expected_connection_names(user)
  registry = cl.user_session.get(ADAPTER_STATE_KEY)
  if _registry_ready(registry, expected_names):
    return registry["tool_specs"]

  await _reset_adapter_state()
  new_registry = {
    "servers": {},
    "tools": {},
    "tool_specs": []
  }

  connections = (user.metadata or {}).get("mcp_connections", [])
  if not connections:
    raise RuntimeError("No MCP connections configured for this user.")

  for conn in connections:
    name = conn.get("name")
    if not name:
      continue
    try:
      server_runtime, tool_entries = await _setup_server(conn)
    except Exception:  # noqa: BLE001
      logger.exception("Failed to initialize MCP connection '%s'", name)
      continue

    new_registry["servers"][name] = server_runtime
    for tool_entry in tool_entries:
      if tool_entry.name in new_registry["tools"]:
        logger.warning(
          "Duplicate MCP tool name '%s' encountered. Overwriting previous entry.",
          tool_entry.name
        )
      new_registry["tools"][tool_entry.name] = tool_entry
      new_registry["tool_specs"].append({
        "type": "function",
        "function": {
          "name": tool_entry.name,
          "description": tool_entry.description,
          "parameters": tool_entry.schema
        }
      })

  cl.user_session.set(ADAPTER_STATE_KEY, new_registry)

  if not new_registry["tool_specs"]:
    raise RuntimeError("MCP tool registry is empty for this session.")

  return new_registry["tool_specs"]

async def get_configured_mcp_tools(user: cl.User) -> List[BaseTool]:
  expected_names = _expected_connection_names(user)
  registry = cl.user_session.get(ADAPTER_STATE_KEY)

  # Ensure registry is ready
  if not _registry_ready(registry, expected_names):
    await fetch_registered_mcp_tools_for_user(user)
    registry = cl.user_session.get(ADAPTER_STATE_KEY)

  if not registry or "tools" not in registry:
    return []

  tools_map = registry.get("tools", {})
  return [entry.tool for entry in tools_map.values()]

async def deregister_mcp_tools_for_user(user: cl.User):
  await _reset_adapter_state()

# Global registry to track MCP clients/tools across sessions
# Key: mcp_name, Value: (runtime, entries)
MCP_CLIENT_CACHE: Dict[str, Tuple[Dict[str, Any], List[MCPToolEntry]]] = {}

async def _setup_server(connection_meta: dict) -> tuple[dict, List[MCPToolEntry]]:
  name = connection_meta["name"]

  if name in MCP_CLIENT_CACHE:
    logger.info(f"Reusing cached MCP client and tools for '{name}'")
    return MCP_CLIENT_CACHE[name]

  url = connection_meta.get('url')
  if not url:
    raise ValueError(f"MCP connection '{name}' requires a 'url'")

  connection = {
    "transport": "sse",
    "url": url,
    "headers": {}
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
    "transport": "sse"
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

async def _reset_adapter_state():
  cl.user_session.set(ADAPTER_STATE_KEY, None)

def _format_tool_response(result: Any) -> List[Dict[str, Any]]:
  text_content: Any = result
  artifacts = None
  if isinstance(result, tuple) and len(result) == 2:
    text_content, artifacts = result

  items: List[Dict[str, Any]] = []
  for text in _flatten_text_content(text_content):
    if text:
      items.append({"type": "text", "text": text})

  if artifacts:
    for artifact in artifacts:
      items.extend(_convert_artifact(artifact))

  return items or [{"type": "text", "text": ""}]

def _flatten_text_content(content: Any) -> List[str]:
  if content is None:
    return []
  if isinstance(content, str):
    return [content]
  if isinstance(content, list):
    return [str(item) for item in content if item is not None]
  if isinstance(content, ToolMessage):
    payload = content.content
    if isinstance(payload, list):
      return [str(chunk) for chunk in payload if chunk]
    return [str(payload)]
  if LangGraphCommand and isinstance(content, LangGraphCommand):
    try:
      return [str(content)]
    except Exception:  # noqa: BLE001
      return ["[command]"]
  return [str(content)]

def _convert_artifact(artifact: Any) -> List[Dict[str, Any]]:
  if isinstance(artifact, ImageContent):
    data = artifact.data
    mime = artifact.mimeType or "image/png"
    return [{
      "type": "image_url",
      "image_url": {"url": f"data:{mime};base64,{data}"}
    }]
  if isinstance(artifact, AudioContent):
    mime = artifact.mimeType or "audio/mpeg"
    return [{"type": "text", "text": f"[audio:{mime}]"}]
  if isinstance(artifact, ResourceLink):
    label = artifact.name or "resource"
    return [{"type": "text", "text": f"{label}: {artifact.uri}"}]
  if isinstance(artifact, EmbeddedResource):
    mime = artifact.mimeType or "application/octet-stream"
    size = len(artifact.data or "")
    return [{"type": "text", "text": f"[embedded:{mime}] ({size} bytes)"}]
  return [{"type": "text", "text": str(artifact)}]
