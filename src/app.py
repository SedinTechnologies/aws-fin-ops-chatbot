import os, redis, logging

import chainlit as cl
import chainlit.types as cl_types
import chainlit.server as cl_server
from mcp import ClientSession

from chatclient import ChatClient
from session_store import RedisSessionStore
from auth_manager import AuthManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)
store = RedisSessionStore(redis_client)
auth = AuthManager(store)

CCAPI_MCP_SERVER_VERSION = os.getenv("CCAPI_MCP_SERVER_VERSION", "latest")
AWS_COST_EXPLORER_MCP_SERVER_VERSION = os.getenv("AWS_COST_EXPLORER_MCP_SERVER_VERSION", "latest")

@cl.password_auth_callback
async def auth_callback(username: str, password: str):
  user = auth.authenticate(username, password)
  if not user:
    return None
  return cl.User(
    identifier=user["email"],
    display_name=user["name"],
    metadata={
      "mcp_connections": [
        {
          "name": "aws-cost-explorer-mcp-server",
          "command": f"/app/scripts/start-mcp-server.sh {user['aws_role_arn']} awslabs.cost-explorer-mcp-server@{AWS_COST_EXPLORER_MCP_SERVER_VERSION}"
        },
        {
          "name": "aws-ccapi-mcp-server",
          "command": f"/app/scripts/start-mcp-server.sh {user['aws_role_arn']} awslabs.ccapi-mcp-server@{CCAPI_MCP_SERVER_VERSION}"
        }
      ]
    }
  )

@cl.on_chat_start
async def on_chat_start():
  # When a user logs in, Chainlit stores user info in cl.user_session under "user"
  user = cl.user_session.get("user")
  if not user:
    # If not logged in, ask to login; Chainlit will show login UI because require_login=true
    await cl.Message(content="Please login to continue.").send()
    return
  logger.info(f"User {user.display_name} has logged in. Session ID: {cl.context.session.id}")

  # Create MCP connection to AWS Cost Explorer MCP Server
  for mcp_conn in user.metadata.get("mcp_connections", []):
    logger.info(f"Establishing MCP connection: {mcp_conn['name']} Session ID: {cl.context.session.id}")
    await create_new_mcp_connection(
      mcp_name=mcp_conn["name"],
      command=mcp_conn["command"]
    )

  # Load existing chats for this user from Redis and set into user_session
  chats = store.load_chats(user.identifier)
  cl.user_session.set("chats", chats)
  cl.user_session.set("messages", [])

  await cl.Message(content=f"Welcome back, {user.display_name}!").send()

@cl.on_chat_end
async def on_chat_end():
  user = cl.user_session.get("user")
  if user:
    for mcp_conn in user.metadata.get("mcp_connections", []):
      logger.info(f"Disconnecting MCP connection: {mcp_conn['name']} Session ID: {cl.context.session.id}")
      await cl_server.disconnect_mcp(
        cl_types.DisconnectMCPRequest(
          sessionId=cl.context.session.id,
          name=mcp_conn["name"]
        ),
        cl.context.session.user
      )
    logger.info(f"User {user.display_name} session ended with ID: {cl.context.session.id}")

@cl.on_mcp_connect
async def on_mcp_connect(connection, session: ClientSession):
  result = await session.list_tools()
  tools = [{
    "name": t.name,
    "description": t.description,
    "parameters": t.inputSchema,
    } for t in result.tools]

  mcp_tools = cl.user_session.get("mcp_tools", {})
  mcp_tools[connection.name] = tools
  cl.user_session.set("mcp_tools", mcp_tools)
  logger.info(f"Registered tools from MCP '{connection.name}' for Session ID: {cl.context.session.id}")

@cl.on_message
async def on_message(message: cl.Message):
  user = cl.user_session.get("user")
  if not user:
    await cl.Message(content="Unauthorized. Please login.").send()
    return

  # Restore conversation context; keep your ChatClient usage intact
  client = ChatClient()
  client.messages = cl.user_session.get("messages", [])

  # Fetch registered mcp tools if present
  mcp_tools = cl.user_session.get("mcp_tools", {})
  tools = []
  for _, ts in mcp_tools.items():
    tools.extend(ts)
  tools = [{"type": "function", "function": t} for t in tools]

  msg = cl.Message(content="")
  async for token in client.generate_response(human_input=message.content, tools=tools):
    await msg.stream_token(token)

  # Persist updated messages to session and to per-user chats
  cl.user_session.set("messages", client.messages)

  # Append to current chat; simple approach: keep single active chat (index 0)
  chats = cl.user_session.get("chats", []) or []
  if not chats:
    # create first chat
    chat = {"id": str(os.urandom(8).hex()), "title": message.content[:80], "messages": client.messages}
    chats.insert(0, chat)
  else:
    # update active chat (0)
    chats[0]["messages"] = client.messages
    # optionally update title
    if len(chats[0].get("messages", [])) > 0:
      chats[0]["title"] = chats[0]["messages"][0].get("content", "")[:80] if isinstance(chats[0]["messages"][0], dict) else str(chats[0]["messages"][0])[:80]

  # Save chats in Redis via store
  store.save_chats(user.identifier, chats)
  cl.user_session.set("chats", chats)

async def create_new_mcp_connection(mcp_name: str, command: str):
  conn_request = cl_types.ConnectStdioMCPRequest(
    sessionId=cl.context.session.id,
    clientType="stdio",
    name=mcp_name,
    fullCommand=command
  )
  await cl_server.connect_mcp(conn_request, cl.context.session.user)
