import logging
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import chainlit as cl
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.types import AudioContent, EmbeddedResource, ImageContent, ResourceLink

try:  # Optional dependency
  from langgraph.types import Command as LangGraphCommand
except ImportError:  # pragma: no cover
  LangGraphCommand = None  # type: ignore[assignment]

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

async def _setup_server(connection_meta: dict) -> tuple[dict, List[MCPToolEntry]]:
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

async def call_tool(tool_name: str, tool_args: Dict[str, Any]) -> List[Dict[str, Any]]:
  guardrails = cl.user_session.get("guardrails")
  user = cl.user_session.get("user")
  session_id = cl.context.session.id
  user_id = getattr(user, "identifier", "unknown")

  entries = await _get_all_tool_entries(user)
  entry = next((e for e in entries if e.name == tool_name), None)

  if entry is None:
    raise Exception(f"No MCP tool registered with name '{tool_name}'")

  if guardrails:
    guardrails.guard_tool_call(
      session_id=session_id,
      user_id=user_id,
      tool_name=tool_name,
      arguments=tool_args or {}
    )

  logger.info("Session ID: %s Calling tool: %s with args: %s", session_id, tool_name, tool_args)

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
