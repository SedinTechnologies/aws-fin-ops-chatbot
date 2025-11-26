import asyncio
import logging
import os
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import urlparse

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

STREAMABLE_PROC_KEY = "streamable_mcp_processes"
ADAPTER_STATE_KEY = "mcp_adapter_state"
DEFAULT_READY_TIMEOUT_SECONDS = 30.0
DEFAULT_READY_DELAY_SECONDS = 1.0


def _get_float_env(name: str, default: float) -> float:
  raw = os.getenv(name)
  if raw is None:
    return default
  try:
    return float(raw)
  except ValueError:
    logger.warning(
      "Invalid value '%s' for %s. Falling back to default %.1f seconds.",
      raw,
      name,
      default
    )
    return default


STREAMABLE_HTTP_READY_TIMEOUT = _get_float_env(
  "STREAMABLE_HTTP_READY_TIMEOUT",
  DEFAULT_READY_TIMEOUT_SECONDS
)
STREAMABLE_HTTP_READY_DELAY = _get_float_env(
  "STREAMABLE_HTTP_READY_INITIAL_DELAY",
  DEFAULT_READY_DELAY_SECONDS
)


class StreamableHttpUnavailable(RuntimeError):
  """Raised when we cannot reach the configured streamable HTTP endpoint."""


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


async def deregister_mcp_tools_for_user(user: cl.User):
  await _reset_adapter_state()


async def _setup_server(connection_meta: dict) -> tuple[dict, List[MCPToolEntry]]:
  name = connection_meta["name"]
  transport_meta = connection_meta.get("transport") or {}
  normalized_transport = (transport_meta.get("type") or "").replace("-", "_").lower()

  started_process = False
  connection: Connection | None = None
  last_exception: Exception | None = None

  if normalized_transport == "streamable_http":
    try:
      started_process = await _ensure_streamable_http_process(
        name,
        connection_meta.get("command"),
        transport_meta.get("url")
      )
      connection = _build_streamable_connection(transport_meta)
    except Exception as exc:  # noqa: BLE001
      last_exception = exc
      logger.warning(
        "Streamable HTTP connection failed for '%s': %s",
        name,
        exc
      )
      await _stop_streamable_http_process(name)
      connection = None

  if connection is None:
    stdio_command = connection_meta.get("stdio_command") or connection_meta.get("command")
    if not stdio_command:
      if last_exception:
        raise last_exception
      raise StreamableHttpUnavailable(
        f"No stdio command available for MCP '{name}'"
      )
    connection = _build_stdio_connection(stdio_command)
    transport_mode = "stdio"
  else:
    transport_mode = "streamable_http"

  tools = await load_mcp_tools(
    None,
    connection=connection,
    server_name=name
  )

  entries = [_build_tool_entry(tool, name) for tool in tools]
  logger.info(
    "Registered %s tools for MCP '%s' via %s transport",
    len(entries),
    name,
    transport_mode
  )

  runtime = {
    "name": name,
    "connection": connection,
    "transport": transport_mode,
    "started_http_process": started_process
  }

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
  registry = cl.user_session.get(ADAPTER_STATE_KEY)
  if not registry:
    return

  servers: dict[str, dict] = registry.get("servers") or {}
  for runtime in servers.values():
    if runtime.get("started_http_process"):
      await _stop_streamable_http_process(runtime["name"])

  cl.user_session.set(ADAPTER_STATE_KEY, None)


async def _wait_for_endpoint_ready(
  url: str,
  *,
  process: asyncio.subprocess.Process | None = None,
  timeout: float | None = None
) -> None:
  if not url:
    return
  timeout = timeout or STREAMABLE_HTTP_READY_TIMEOUT
  parsed = urlparse(url)
  host = parsed.hostname or "127.0.0.1"
  port = parsed.port or (443 if parsed.scheme == "https" else 80)
  loop = asyncio.get_running_loop()
  deadline = loop.time() + timeout
  while True:
    if process and process.returncode is not None:
      raise StreamableHttpUnavailable(
        f"Streamable HTTP MCP process exited early with code {process.returncode} "
        f"while waiting for {host}:{port} to become ready."
      )
    try:
      reader, writer = await asyncio.open_connection(host, port)
      writer.close()
      await writer.wait_closed()
      return
    except Exception as exc:  # noqa: BLE001
      if loop.time() >= deadline:
        raise StreamableHttpUnavailable(
          f"Timed out waiting for MCP endpoint at {host}:{port} to become ready ({exc})"
        ) from exc
      await asyncio.sleep(0.5)


def _build_streamable_connection(transport: dict) -> Connection:
  url = transport.get("url")
  if not url:
    raise ValueError("Streamable HTTP transport missing 'url'.")
  headers = {"Authorization": transport.get("auth", "no-auth")}
  return {
    "transport": "streamable_http",
    "url": url,
    "headers": headers
  }


def _build_stdio_connection(command: str) -> Connection:
  if not command:
    raise ValueError("STDIO command is required.")
  return {
    "transport": "stdio",
    "command": "/bin/sh",
    "args": ["-c", command]
  }


async def _ensure_streamable_http_process(
  mcp_name: str,
  command: str | None,
  transport_url: str | None
) -> bool:
  if not command:
    logger.warning(
      "Streamable HTTP MCP '%s' missing command. Skipping auto-start; assuming external server exists.",
      mcp_name
    )
    return False

  processes = cl.user_session.get(STREAMABLE_PROC_KEY) or {}
  existing_proc: asyncio.subprocess.Process | None = processes.get(mcp_name)

  if existing_proc and existing_proc.returncode is None:
    return False

  if existing_proc and existing_proc.returncode is not None:
    processes.pop(mcp_name, None)

  logger.info("Starting streamable HTTP MCP server '%s' with command: %s", mcp_name, command)
  proc = await asyncio.create_subprocess_shell(
    command,
    stdout=asyncio.subprocess.DEVNULL,
    stderr=asyncio.subprocess.DEVNULL
  )
  processes[mcp_name] = proc
  cl.user_session.set(STREAMABLE_PROC_KEY, processes)

  if STREAMABLE_HTTP_READY_DELAY > 0:
    await asyncio.sleep(STREAMABLE_HTTP_READY_DELAY)

  try:
    if transport_url:
      await _wait_for_endpoint_ready(
        transport_url,
        process=proc,
        timeout=STREAMABLE_HTTP_READY_TIMEOUT
      )
  except StreamableHttpUnavailable as exc:
    await _stop_streamable_http_process(mcp_name)
    raise exc

  if proc.returncode is not None:
    raise RuntimeError(
      f"Streamable HTTP MCP server '{mcp_name}' exited early with code {proc.returncode}"
    )

  return True


async def _stop_streamable_http_process(mcp_name: str):
  processes = cl.user_session.get(STREAMABLE_PROC_KEY) or {}
  proc: asyncio.subprocess.Process | None = processes.pop(mcp_name, None)
  if not proc:
    return

  if proc.returncode is not None:
    return

  logger.info("Stopping streamable HTTP MCP server '%s'", mcp_name)
  proc.terminate()
  try:
    await asyncio.wait_for(proc.wait(), timeout=5)
  except asyncio.TimeoutError:
    proc.kill()
    await proc.wait()

  cl.user_session.set(STREAMABLE_PROC_KEY, processes)


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
