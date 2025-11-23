import asyncio, logging, os, traceback
from urllib.parse import urlparse
import chainlit as cl
import chainlit.types as cl_types
import chainlit.server as cl_server
from mcp.types import TextContent, ImageContent

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def call_tool(tool_name, tool_args):
  guardrails = cl.user_session.get("guardrails")
  user = cl.user_session.get("user")
  session_id = cl.context.session.id
  user_id = getattr(user, "identifier", "unknown")
  if guardrails:
    guardrails.guard_tool_call(
      session_id=session_id,
      user_id=user_id,
      tool_name=tool_name,
      arguments=tool_args or {}
    )
  try:
    logger.info(f"Session ID: {cl.context.session.id} Calling tool: {tool_name} with args: {tool_args}")
    resp_items = []
    mcp_tools = cl.user_session.get("mcp_tools", {})
    mcp_name = None

    for c_name, tool_list in mcp_tools.items():
      if any(t.get("name") == tool_name for t in tool_list):
        mcp_name = c_name
        break

    if not mcp_name:
      raise Exception(f"No mcp server registered with tool name: {tool_name}") from None

    mcp_session, _ = cl.context.session.mcp_sessions.get(mcp_name)
    func_response = await mcp_session.call_tool(tool_name, tool_args)

    for item in func_response.content:
      if isinstance(item, TextContent):
        resp_items.append({"type": "text", "text": item.text})
      elif isinstance(item, ImageContent):
        resp_items.append({
          "type": "image_url",
          "image_url": {"url": f"data:{item.mimeType};base64,{item.data}"}
        })
      else:
        raise ValueError(f"Unsupported content type: {type(item)}")

    if guardrails:
      guardrails.guard_tool_response(
        session_id=session_id,
        user_id=user_id,
        tool_name=tool_name,
        response=resp_items
      )

  except Exception as e:
    traceback.print_exc()
    resp_items.append({"type": "text", "text": str(e)})
  return resp_items

STREAMABLE_PROC_KEY = "streamable_mcp_processes"
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


async def _wait_for_endpoint_ready(url: str, timeout: float | None = None) -> None:
  if not url:
    return
  timeout = timeout or STREAMABLE_HTTP_READY_TIMEOUT
  parsed = urlparse(url)
  host = parsed.hostname
  port = parsed.port or (443 if parsed.scheme == "https" else 80)
  if not host or not port:
    return
  loop = asyncio.get_running_loop()
  deadline = loop.time() + timeout
  last_error: Exception | None = None
  while True:
    try:
      reader, writer = await asyncio.open_connection(host, port)
      writer.close()
      await writer.wait_closed()
      return
    except Exception as exc:
      last_error = exc
      if loop.time() >= deadline:
        raise StreamableHttpUnavailable(
          f"Timed out waiting for MCP endpoint at {host}:{port} to become ready ({exc})"
        )
      await asyncio.sleep(0.5)


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

  # Give the server a brief moment to boot before the HTTP client connects
  if STREAMABLE_HTTP_READY_DELAY > 0:
    await asyncio.sleep(STREAMABLE_HTTP_READY_DELAY)

  try:
    if transport_url:
      await _wait_for_endpoint_ready(
        transport_url,
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


async def deregister_mcp_tools_for_user(user: cl.User):
  for mcp_conn in user.metadata.get("mcp_connections", []):
    logger.info(f"Disconnecting MCP connection: {mcp_conn['name']} Session ID: {cl.context.session.id}")
    await cl_server.disconnect_mcp(
      cl_types.DisconnectMCPRequest(
        sessionId=cl.context.session.id,
        name=mcp_conn["name"]
      ),
      cl.context.session.user
    )
    transport = mcp_conn.get("transport", {})
    normalized_type = (transport.get("type") or "").replace("-", "").replace("_", "").lower()
    if normalized_type == "streamablehttp":
      await _stop_streamable_http_process(mcp_conn["name"])

async def fetch_registered_mcp_tools_for_user(user: cl.User):
  try:
    # Fetch mcp tools connections
    mcp_tools = cl.user_session.get("mcp_tools", None)
    if not mcp_tools: # Register if this is a new session
      for mcp_conn in user.metadata.get("mcp_connections", []):
        logger.info(f"Establishing MCP connection: {mcp_conn['name']} Session ID: {cl.context.session.id}")
        await create_new_mcp_connection(
          mcp_name=mcp_conn["name"],
          command=mcp_conn["command"],
          transport=mcp_conn.get("transport"),
          stdio_command=mcp_conn.get("stdio_command")
        )

    registered_mcp_tools = [tool for mcp_tool in cl.user_session.get("mcp_tools").values() for tool in mcp_tool]
    return [{"type": "function", "function": tool} for tool in registered_mcp_tools]
  except Exception:
    logger.error(f"Exception while fetching / registering mcp tools for session: {cl.context.session.id} Traceback: {traceback.print_exc()}")

async def create_new_mcp_connection(
  mcp_name: str,
  command: str,
  *,
  transport: dict | None = None,
  stdio_command: str | None = None
):
  transport_type = (transport or {}).get("type") or ""
  normalized_type = transport_type.replace("-", "").replace("_", "").lower()

  started_http_process = False
  started_http_process = False
  fallback_to_stdio = False
  last_exception: Exception | None = None
  if transport and normalized_type == "streamablehttp":
    try:
      started_http_process = await _ensure_streamable_http_process(
        mcp_name,
        command,
        transport.get("url") if transport else None
      )
      conn_request = cl_types.ConnectStreamableHttpMCPRequest(
        sessionId=cl.context.session.id,
        clientType="streamable-http",
        name=mcp_name,
        url=transport["url"],
        headers={"Authorization": transport.get("auth", "no-auth")}
      )
      await cl_server.connect_mcp(conn_request, cl.context.session.user)
      return
    except StreamableHttpUnavailable as exc:
      logger.warning(
        "Streamable HTTP endpoint unavailable for '%s': %s",
        mcp_name,
        exc
      )
      fallback_to_stdio = True
      last_exception = exc
    except Exception as exc:
      if started_http_process:
        await _stop_streamable_http_process(mcp_name)
      logger.warning(
        "Streamable HTTP connection failed for '%s': %s",
        mcp_name,
        exc
      )
      fallback_to_stdio = True
      last_exception = exc

  if fallback_to_stdio:
    if stdio_command:
      logger.info(
        "Falling back to stdio MCP connection for '%s' after streamable-http failure.",
        mcp_name
      )
      stdio_request = cl_types.ConnectStdioMCPRequest(
        sessionId=cl.context.session.id,
        clientType="stdio",
        name=mcp_name,
        fullCommand=stdio_command
      )
      await cl_server.connect_mcp(stdio_request, cl.context.session.user)
      return
    if last_exception:
      raise last_exception
    raise StreamableHttpUnavailable(f"Streamable HTTP connection failed for '{mcp_name}'")

  conn_request = cl_types.ConnectStdioMCPRequest(
    sessionId=cl.context.session.id,
    clientType="stdio",
    name=mcp_name,
    fullCommand=command
  )
  await cl_server.connect_mcp(conn_request, cl.context.session.user)
