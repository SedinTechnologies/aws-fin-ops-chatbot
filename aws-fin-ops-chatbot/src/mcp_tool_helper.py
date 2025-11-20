import logging, traceback
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
          transport=mcp_conn.get("transport")
        )

    registered_mcp_tools = [tool for mcp_tool in cl.user_session.get("mcp_tools").values() for tool in mcp_tool]
    return [{"type": "function", "function": tool} for tool in registered_mcp_tools]
  except Exception:
    logger.error(f"Exception while fetching / registering mcp tools for session: {cl.context.session.id} Traceback: {traceback.print_exc()}")

async def create_new_mcp_connection(mcp_name: str, command: str, transport: dict | None = None):
  if transport and transport.get("type") == "streamableHttp":
    conn_request = cl_types.ConnectStreamableHttpMCPRequest(
      sessionId=cl.context.session.id,
      name=mcp_name,
      url=transport["url"],
      headers={"Authorization": transport.get("auth", "no-auth")}
    )
  else:
    conn_request = cl_types.ConnectStdioMCPRequest(
      sessionId=cl.context.session.id,
      clientType="stdio",
      name=mcp_name,
      fullCommand=command
    )
  await cl_server.connect_mcp(conn_request, cl.context.session.user)
