import os, redis, logging, random, traceback

import chainlit as cl
from chainlit.types import ThreadDict
from mcp import ClientSession

from mcp_tool_helper import (
  fetch_registered_mcp_tools_for_user,
  deregister_mcp_tools_for_user
)
from azure_openai_client import AzureOpenAIClient
from session_store import RedisSessionStore
from auth_manager import AuthManager
from guardrails import GuardrailEngine, GuardrailViolation

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)
store = RedisSessionStore(redis_client)
auth = AuthManager(store)

CCAPI_MCP_SERVER_VERSION = os.getenv("CCAPI_MCP_SERVER_VERSION", "latest")
AWS_COST_EXPLORER_MCP_SERVER_VERSION = os.getenv("AWS_COST_EXPLORER_MCP_SERVER_VERSION", "latest")

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
    return None
  return cl.User(
    identifier=user["identifier"],
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
  user = cl.user_session.get("user")
  if not user:
    logger.info("User not logged in. Showing login page...")
    await cl.Message(content="Please login to continue.").send()
    return
  guardrails = GuardrailEngine.from_env()
  cl.user_session.set("guardrails", guardrails)
  client = AzureOpenAIClient(guardrails=guardrails)
  cl.user_session.set("client", client)
  cl.user_session.set("mcp_tools", {})
  logger.info(f"User {user.display_name} has logged in. Session ID: {cl.context.session.id}")

@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
  messages = thread["steps"]
  memory = []
  for message in messages:
    if message["type"] == "user_message":
      memory.append({"role": "user", "content": message["output"]})
    elif message["type"] == "assistant_message":
      memory.append({"role": "assistant", "content": message["output"]})

  # Updating client with the old messages
  guardrails = GuardrailEngine.from_env()
  cl.user_session.set("guardrails", guardrails)
  client = AzureOpenAIClient(guardrails=guardrails)
  client.messages.extend(memory)
  cl.user_session.set("client", client)
  cl.user_session.set("mcp_tools", {})

  # Save the restored memory/context back into the user session
  cl.user_session.set("memory", memory)

@cl.on_chat_end
async def on_chat_end():
  user = cl.user_session.get("user")
  if user:
    await deregister_mcp_tools_for_user(user)
    cl.user_session.set("mcp_tools", {})
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
async def new_message(message: cl.Message):
  try:
    user = cl.user_session.get("user")
    if not user:
      await cl.Message(content="Unauthorized. Please login.").send()
      return

    tools = await fetch_registered_mcp_tools_for_user(user)
    client: AzureOpenAIClient = cl.user_session.get("client")
    guardrails: GuardrailEngine = cl.user_session.get("guardrails")

    guardrails.guard_input(
      session_id=cl.context.session.id,
      user_id=user.identifier,
      text=message.content
    )

    client.messages.extend(cl.user_session.get("memory", [])) # Add existing messages if any

    content, next_questions = await client.generate_response(
      query=message.content,
      tools=tools,
      session_id=cl.context.session.id,
      user_id=user.identifier
    )

    guardrails.guard_model_response(
      session_id=cl.context.session.id,
      user_id=user.identifier,
      content=content
    )
    msg_actions = [
      cl.Action(
        name     = "next_question_click",
        icon     = nq["icon"],
        label    = nq["question"],
        payload  = { "question": nq["question"] }
      ) for nq in next_questions
    ]

    # Store the messages back in session
    cl.user_session.set("memory", client.messages)

    # Primary assistant response with standard actions (copy/feedback)
    response_message = cl.Message(content=content,
    actions=msg_actions
    )
    await response_message.send()

    # Attach buttons for follow-up suggestions
    # if msg_actions:
    #   await cl.Message(
    #     content="\u200b",
    #     actions=msg_actions,
    #     author="system"
    #   ).send()
  except GuardrailViolation as violation:
    logger.warning(
      "Guardrail violation in session %s: %s",
      cl.context.session.id,
      violation,
      exc_info=True
    )
    await cl.Message("Your request was blocked by safety policies. Please adjust and try again.").send()
  except Exception:
    logger.error(f"Error while processing: {traceback.print_exc()}")
    await cl.Message("An error occurred while processing! Please contact admin team!").send()

@cl.action_callback("next_question_click")
async def next_question_click_action_callback(action: cl.Action):
  question = action.payload["question"]
  user_echo = cl.Message(author="user", content=question, type="user_message")
  await user_echo.send()
  await new_message(cl.Message(author="user", content=question))
