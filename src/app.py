import redis, logging, random, traceback, re

import chainlit as cl
from chainlit.types import ThreadDict
from chainlit.data import chainlit_data_layer

from date_utils import LenientDatetime
from mcp_utils import (
  fetch_registered_mcp_tools_for_user,
  get_configured_mcp_tools,
  enabled_mcp_connections_list
)
from langgraph_client import LangGraphClient
from session_store import RedisSessionStore
from auth_manager import AuthManager
from guardrails import GuardrailEngine, GuardrailViolation

# Chainlit stores timestamps with a trailing 'Z'; keep the default parser so history persists.
chainlit_data_layer.ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
chainlit_data_layer.datetime = LenientDatetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)
store = RedisSessionStore(redis_client)
auth = AuthManager(store)

seed_questions = [
  "Show my monthly AWS spend trend for the last 6 months",
  "Compare costs for the last 3 completed months and top drivers",
  "List top 10 resources by cost across all accounts and regions",
  "Break down EC2 costs by instance type, region, and tag",
  "Show S3 storage cost growth and largest buckets by expense",
  "Detect cost anomalies in the past 30 days and explain drivers",
  "Provide daily spend heatmap for selected services and regions",
  "Compare projected forecast vs actual spend for this month",
  "Show idle or underutilized resources with the highest ongoing monthly cost",
  "List potential rightsizing candidates and expected monthly savings per resource"
]

@cl.set_starters
async def set_starters():
  random_seed_questions = random.sample(seed_questions, 3)
  return [cl.Starter(label=q, message=q) for q in random_seed_questions]

@cl.password_auth_callback
async def auth_callback(username: str, password: str):
  user = auth.authenticate(username, password)
  if not user:
    logger.debug("Authentication failed")
    return None

  logger.debug(f"Authentication successful for username: {username}")
  return cl.User(
    identifier     = user["identifier"],
    display_name  = user["name"],
    metadata      = { "mcp_connections": enabled_mcp_connections_list() }
  )

@cl.on_chat_start
async def on_chat_start():
  user = cl.user_session.get("user")
  if not user:
    logger.info("User not logged in. Showing login page...")
    await cl.Message(content="Please login to continue.").send()
    return

  if store.get_user(user.identifier):
    user.metadata["mcp_connections"] = enabled_mcp_connections_list()
    cl.user_session.set("user", user)
    logger.info(f"Refreshed MCP connections for user {user.identifier}")

  guardrails = GuardrailEngine.from_env()
  cl.user_session.set("guardrails", guardrails)

  logger.info(f"Initializing LangGraph client for user {user.identifier}")
  tools = await get_configured_mcp_tools(user)
  client = LangGraphClient(tools=tools)

  cl.user_session.set("client", client)
  cl.user_session.set("memory", [])

  logger.info(f"User {user.display_name} has logged in. Session ID: {cl.context.session.id}")

@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
  user = cl.user_session.get("user")
  if not user:
    logger.warning("Chat resume triggered without authenticated user; continuing without MCP re-registration.")

  messages = thread["steps"]
  memory = []
  for message in messages:
    if message["type"] == "user_message":
      memory.append({"role": "user", "content": message["output"]})
    elif message["type"] == "assistant_message":
      memory.append({"role": "assistant", "content": message["output"]})

  # Updating client with the old messages
  # Refresh MCP connections for resumed sessions as well
  user_details = store.get_user(user.identifier)
  if user_details:
    user.metadata["mcp_connections"] = enabled_mcp_connections_list()
    cl.user_session.set("user", user)
    logger.info(f"Refreshed MCP connections for user {user.identifier} (resume)")

  guardrails = GuardrailEngine.from_env()
  cl.user_session.set("guardrails", guardrails)
  try:
    tools = await get_configured_mcp_tools(user)
    client = LangGraphClient(tools=tools)
    if memory:
      client.load_historical_messages(cl.context.session.id, memory)
  except Exception as e:
    logger.error(f"Failed to initialize LangGraph client on resume: {e}")
    client = None

  cl.user_session.set("client", client)

  # Save the restored memory/context back into the user session
  cl.user_session.set("memory", memory)

@cl.on_chat_end
async def on_chat_end():
  user = cl.user_session.get("user")
  if user:
    # Do not deregister MCP tools on chat end to allow reuse across sessions/chats
    # await deregister_mcp_tools_for_user(user)
    logger.info(f"User {user.display_name} session ended with ID: {cl.context.session.id}")

@cl.on_message
async def new_message(message: cl.Message):
  try:
    user = cl.user_session.get("user")
    if not user:
      await cl.Message(content="Unauthorized. Please login.").send()
      return

    logger.debug(f"New message from user: {message.content[:50]}")
    tools = await fetch_registered_mcp_tools_for_user(user)

    client = cl.user_session.get("client")
    if client is None:
      await cl.Message(content="Session not initialized. Please refresh and try again.").send()
      return

    tools = await get_configured_mcp_tools(user)
    logger.debug(f"Loaded {len(tools)} MCP tools for LangGraph")

    response_message = cl.Message(content="")
    await response_message.stream_token(" ")

    async for chunk in client.stream_response(
      message=message.content,
      session_id=cl.context.session.id,
      user_id=cl.user_session.get("user").identifier,
      guardrails=cl.user_session.get("guardrails")
    ):
      if isinstance(chunk, str):
        await response_message.stream_token(chunk)

    # Post-processing for suggestions
    content = response_message.content

    # Matches 'suggestions:' or '```suggestions' at the start of a line or after a newline
    start_pattern = r"(?:(?:\n|^)suggestions:?|```suggestions)"
    match = re.search(start_pattern, content, flags=re.IGNORECASE)

    if match:
      block = content[match.start():]
      lines = block.split('\n')

      for line in lines[1:]:  # skip the matched header line
        question = line.strip()
        # skip empty lines, backticks, or 'suggestions:' headers
        if not question or question.startswith('```') or question.lower().startswith('suggestions'):
          continue

        # Strip potential list markers (e.g., "1. ", "- ", "* ")
        question = re.sub(r'^(?:-|\*|\d+\.)\s+', '', question)

        response_message.actions.append(
          cl.Action(
            name="next_question_click",
            label=question,
            payload={"question": question}
          )
        )

      # Strip the matched suggestions block from the content
      response_message.content = content[:match.start()].strip()
    await response_message.update()

  except GuardrailViolation as violation:
    logger.warning(
      "Guardrail violation in session %s: %s",
      cl.context.session.id,
      violation,
      exc_info=True
    )
    await cl.Message("Your request was blocked by safety policies. Please adjust and try again.").send()
  except Exception:
    logger.error(f"Error while processing: {traceback.format_exc()}")
    await cl.Message("An error occurred while processing! Please contact admin team!").send()

@cl.action_callback("next_question_click")
async def next_question_click_action_callback(action: cl.Action):
  question = action.label
  user_echo = cl.Message(author="user", content=question, type="user_message")
  await user_echo.send()
  await new_message(cl.Message(author="user", content=question))
