import os, redis, logging, random, traceback, json, re #kiss
from typing import List, Dict, Any

import chainlit as cl
from chainlit.types import ThreadDict
from mcp import ClientSession
from chainlit.data import chainlit_data_layer

from date_utils import LenientDatetime
from mcp_tool_helper import (
  fetch_registered_mcp_tools_for_user,
  get_configured_mcp_tools
)
from mcp_utils import register_mcp_connections_for_user
from azure_openai_client import AzureOpenAIClient
from langgraph_app import LangGraphClient
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

ENABLE_LANGGRAPH = os.getenv("ENABLE_LANGGRAPH", "false").lower() == "true"

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
  print(f"[AUTH_DEBUG] auth_callback called for username: {username}")
  user = auth.authenticate(username, password)
  if not user:
    print("[AUTH_DEBUG] Authentication failed")
    return None

  return cl.User(
    identifier     = user["identifier"],
    display_name  = user["name"],
    metadata      = { "mcp_connections": register_mcp_connections_for_user(user) }
  )

@cl.on_chat_start
async def on_chat_start():
  user = cl.user_session.get("user")
  if not user:
    logger.info("User not logged in. Showing login page...")
    await cl.Message(content="Please login to continue.").send()
    return

  user_details = store.get_user(user.identifier)
  if user_details:
      user.metadata["mcp_connections"] = register_mcp_connections_for_user(user_details)
      cl.user_session.set("user", user)
      logger.info(f"Refreshed MCP connections for user {user.identifier}")

  guardrails = GuardrailEngine.from_env()
  cl.user_session.set("guardrails", guardrails)

  client = None
  if ENABLE_LANGGRAPH:
    logger.info(f"Initializing LangGraph client for user {user.identifier}")
    from mcp_tool_helper import get_configured_mcp_tools
    tools = await get_configured_mcp_tools(user)
    client = LangGraphClient(tools=tools)
  else:
    logger.info(f"Initializing AzureOpenAI client for user {user.identifier}")
    client = AzureOpenAIClient(guardrails=guardrails)

  cl.user_session.set("langgraph_enabled", ENABLE_LANGGRAPH)
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
      user.metadata["mcp_connections"] = register_mcp_connections_for_user(user_details)
      cl.user_session.set("user", user)
      logger.info(f"Refreshed MCP connections for user {user.identifier} (resume)")

  guardrails = GuardrailEngine.from_env()
  cl.user_session.set("guardrails", guardrails)
  use_langgraph = ENABLE_LANGGRAPH
  client = None
  if use_langgraph:
    # Get the tool objects for LangGraph
    from mcp_tool_helper import get_configured_mcp_tools
    # Note: We might need to re-fetch tools if they are not in session
    # But for resume, we assume session is active or we re-fetch
    try:
       # Force re-fetch with new metadata
       await fetch_registered_mcp_tools_for_user(user)
       tools = await get_configured_mcp_tools(user)
       client = LangGraphClient(tools=tools)
    except Exception:
       logger.warning("Failed to initialize LangGraph client on resume; falling back")
       use_langgraph = False

  if not use_langgraph:
    client = AzureOpenAIClient(guardrails=guardrails)
    client.messages.extend(memory)
  cl.user_session.set("langgraph_enabled", use_langgraph)
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
  logger.info(f"[HANDLER_DEBUG] new_message called with: {message.content[:50]}")
  try:
    user = cl.user_session.get("user")
    if not user:
      await cl.Message(content="Unauthorized. Please login.").send()
      return

    tools = await fetch_registered_mcp_tools_for_user(user)

    use_langgraph = bool(cl.user_session.get("langgraph_enabled", False))
    logger.info(f"[HANDLER_DEBUG] use_langgraph={use_langgraph}, langgraph_enabled={cl.user_session.get('langgraph_enabled')}")
    client = cl.user_session.get("client")
    logger.info(f"[HANDLER_DEBUG] client type: {type(client).__name__}")
    if client is None:
      await cl.Message(content="Session not initialized. Please refresh and try again.").send()
      return
    guardrails: GuardrailEngine = cl.user_session.get("guardrails")

    if not use_langgraph:
      logger.info("[HANDLER_DEBUG] Using non-LangGraph path (AzureOpenAIClient)")
      guardrails.guard_input(
        session_id=cl.context.session.id,
        user_id=user.identifier,
        text=message.content
      )

    if use_langgraph:
      logger.info("[HANDLER_DEBUG] Using LangGraph path")
      logger.info(f"[STREAM_DEBUG] Starting LangGraph stream for message: {message.content}")

      # Get actual BaseTool objects for LangGraph
      tools = await get_configured_mcp_tools(cl.user_session.get("user"))
      logger.info(f"[STREAM_DEBUG] Loaded {len(tools)} MCP tools for LangGraph")

      # Reuse existing client to persist memory
      lg_client = client

      response_message = cl.Message(content="")
      buffered_chunks: List[str] = []
      next_questions: List[Dict[str, Any]] = []

      logger.info(f"[STREAM_DEBUG] Starting LangGraph stream for message: {message.content[:50]}")
      logger.info(f"[STREAM_DEBUG] Passing {len(tools)} tools to LangGraph: {[t.name for t in tools[:5]]}")
      chunk_count = 0
      async for chunk in lg_client.stream_response(
        message=message.content,
        session_id=cl.context.session.id,
        user_id=cl.user_session.get("user").identifier,
        guardrails=cl.user_session.get("guardrails")
      ):
        if isinstance(chunk, str):
          await response_message.stream_token(chunk)

      await response_message.send()

      # Post-processing for suggestions
      content = response_message.content

      # Robust pattern to find the START of the suggestions block
      # Matches 'json_suggestions' or '```json_suggestions' at the start of a line or after a newline
      start_pattern = r"(?:(?:\n|^)json_suggestions|```json_suggestions)"
      match = re.search(start_pattern, content)

      if match:
          # Extract the potential block from the match start to the end of the string
          block = content[match.start():]

          # Try to extract JSON from the block
          json_match = re.search(r"(\[\s*\{[\s\S]*)", block)
          if json_match:
              json_text = json_match.group(1)
              # Remove potential closing backticks if present
              json_text = re.sub(r"\s*```\s*$", "", json_text)

              try:
                  suggestions = json.loads(json_text)
                  actions = []
                  for s in suggestions:
                      label = s.get("label", s.get("question")[:20])
                      description = s.get("description")
                      if description:
                          label = f"{label} - {description}"

                      actions.append(
                          cl.Action(
                              name="next_question_click",
                              icon=s.get("icon", "👉"),
                              label=label,
                              payload={"question": s.get("question")}
                          )
                      )
                  response_message.actions = actions
              except json.JSONDecodeError:
                  # JSON is likely truncated or malformed. We log it but don't crash.
                  pass

          # ALWAYS strip the matched block from the content to prevent raw text leakage
          response_message.content = content[:match.start()].strip()
          await response_message.update()

      # Update memory if needed (LangGraph manages its own state usually, but for session consistency)
      # cl.user_session.set("memory", lg_client.history)
      return

    # Fallback to AzureOpenAIClient if LangGraph is not used
    az_client: AzureOpenAIClient = client

    az_client.messages.extend(cl.user_session.get("memory", [])) # Add existing messages if any

    response_message = cl.Message(content="")

    next_questions = []
    async for chunk in az_client.stream_response(
      query=message.content,
      tools=tools,
      session_id=cl.context.session.id,
      user_id=user.identifier
    ):
      try:
        payload = json.loads(chunk)
      except json.JSONDecodeError:
        if not response_message.id:
          await response_message.send()
        await response_message.stream_token(chunk)
        continue

      if not isinstance(payload, dict):
        if not response_message.id:
          await response_message.send()
        await response_message.stream_token(chunk)
        continue

      if payload.get("type") == "final":
        next_questions = payload["next_questions"]
        final_content = payload["content"]
        guardrails.guard_model_response(
          session_id=cl.context.session.id,
          user_id=user.identifier,
          content=final_content
        )
        if not response_message.id:
          await response_message.send()
        response_message.content = final_content
        msg_actions = [
          cl.Action(
            name     = "next_question_click",
            icon     = nq["icon"],
            label    = nq["question"],
            payload  = { "question": nq["question"] }
          ) for nq in next_questions
        ]
        response_message.actions = msg_actions
        await response_message.update()
      else:
        await response_message.stream_token(chunk)

    cl.user_session.set("memory", az_client.messages)

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
    logger.error(f"Error while processing: {traceback.format_exc()}")
    await cl.Message("An error occurred while processing! Please contact admin team!").send()

@cl.action_callback("next_question_click")
async def next_question_click_action_callback(action: cl.Action):
  question = action.payload["question"]
  user_echo = cl.Message(author="user", content=question, type="user_message")
  await user_echo.send()
  await new_message(cl.Message(author="user", content=question))
