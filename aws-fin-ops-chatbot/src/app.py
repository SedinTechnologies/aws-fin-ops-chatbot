import os, redis, logging, random, traceback, json, shlex, re #kiss
from typing import List, Dict, Any

import chainlit as cl
from chainlit.types import ThreadDict
from mcp import ClientSession
from chainlit.data import chainlit_data_layer
from datetime import datetime as _datetime

from mcp_tool_helper import (
  fetch_registered_mcp_tools_for_user,
  deregister_mcp_tools_for_user,
  get_configured_mcp_tools
)
from azure_openai_client import AzureOpenAIClient
from langgraph_app import LangGraphClient
from session_store import RedisSessionStore
from auth_manager import AuthManager
from guardrails import GuardrailEngine, GuardrailViolation

# Chainlit stores timestamps with a trailing 'Z'; keep the default parser so history persists.
chainlit_data_layer.ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class _LenientDatetime(_datetime):
  @classmethod
  def strptime(cls, date_string, fmt):
    try:
      return _datetime.strptime(date_string, fmt)
    except ValueError as exc:
      if fmt.endswith("Z") and not date_string.endswith("Z"):
        return _datetime.strptime(f"{date_string}Z", fmt)
      if not fmt.endswith("Z") and date_string.endswith("Z"):
        return _datetime.strptime(date_string[:-1], fmt)
      raise exc


chainlit_data_layer.datetime = _LenientDatetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)
store = RedisSessionStore(redis_client)
auth = AuthManager(store)

CCAPI_MCP_SERVER_VERSION = os.getenv("CCAPI_MCP_SERVER_VERSION", "latest")
AWS_COST_EXPLORER_MCP_SERVER_VERSION = os.getenv("AWS_COST_EXPLORER_MCP_SERVER_VERSION", "latest")
AWS_CLOUDWATCH_MCP_SERVER_VERSION = os.getenv("AWS_CLOUDWATCH_MCP_SERVER_VERSION", "latest")
AWS_BILLING_MCP_SERVER_VERSION = os.getenv("AWS_BILLING_MCP_SERVER_VERSION", "latest")
AWS_CLOUDTRAIL_MCP_SERVER_VERSION = os.getenv("AWS_CLOUDTRAIL_MCP_SERVER_VERSION", "latest")
AWS_PRICING_MCP_SERVER_VERSION = os.getenv("AWS_PRICING_MCP_SERVER_VERSION", "latest")
ENABLE_LANGGRAPH = os.getenv("ENABLE_LANGGRAPH", "false").lower() == "true"
ENFORCE_LOCAL_MCP = os.getenv("ENFORCE_LOCAL_MCP", "true").lower() == "true"


def _build_mcp_config(
  prefix: str,
  *,
  default_host: str,
  default_port: str,
  default_auth: str = "no-auth",
  default_transport: str = "stdio",
  default_client_host: str | None = None
):
  host_env = os.getenv(f"{prefix}_HOST")
  if ENFORCE_LOCAL_MCP:
    host_env = "127.0.0.1"
  bind_host = os.getenv(f"{prefix}_BIND_HOST") or host_env or default_host
  client_host = os.getenv(f"{prefix}_CLIENT_HOST")
  if client_host is None:
    if default_client_host is not None:
      client_host = default_client_host
    else:
      client_host = host_env or bind_host
  if ENFORCE_LOCAL_MCP:
    bind_host = "127.0.0.1"
    client_host = "127.0.0.1"
  port = os.getenv(f"{prefix}_PORT", default_port)
  auth = os.getenv(f"{prefix}_AUTH", default_auth)
  transport = os.getenv(f"{prefix}_TRANSPORT", default_transport)
  allowed_hosts = os.getenv(f"{prefix}_ALLOWED_HOSTS")
  allowed_origins = os.getenv(f"{prefix}_ALLOWED_ORIGINS")
  if transport.replace("-", "").replace("_", "").lower() == "streamablehttp":
    if not ENFORCE_LOCAL_MCP and host_env is None and os.getenv(f"{prefix}_BIND_HOST") is None:
      bind_host = "0.0.0.0"
    if not ENFORCE_LOCAL_MCP and os.getenv(f"{prefix}_CLIENT_HOST") is None and host_env is None and default_client_host is None:
      client_host = "127.0.0.1"
  url = os.getenv(
    f"{prefix}_URL",
    f"http://{client_host}:{port}/mcp"
  )
  if allowed_hosts is None:
    allowed_hosts = client_host
  if allowed_origins is None:
    allowed_origins = url
  if ENFORCE_LOCAL_MCP:
    url = f"http://127.0.0.1:{port}/mcp"
    allowed_hosts = "127.0.0.1"
    allowed_origins = url
  return {
    "host": "127.0.0.1" if ENFORCE_LOCAL_MCP else bind_host,
    "bind_host": "127.0.0.1" if ENFORCE_LOCAL_MCP else bind_host,
    "client_host": client_host,
    "port": port,
    "auth": auth,
    "url": url,
    "transport": transport,
    "allowed_hosts": allowed_hosts,
    "allowed_origins": allowed_origins
  }


def _build_mcp_command(
  *,
  config: Dict[str, str],
  role_arn: str,
  server_version: str,
  package_name: str,
  transport_override: str | None = None
):
  transport = transport_override or config["transport"]
  normalized_transport = (transport or "").replace("-", "").replace("_", "").lower()
  package_spec = package_name
  if normalized_transport == "streamablehttp":
    package_spec = f"{package_name}[streamable-http]"
  package_arg = shlex.quote(f"{package_spec}@{server_version}")
  quoted_role_arn = shlex.quote(role_arn)
  enforce_local = "true" if ENFORCE_LOCAL_MCP else "false"
  parts = [
    f"AWS_API_MCP_TRANSPORT={transport}",
    f"AUTH_TYPE={config['auth']}",
    f"AWS_API_MCP_HOST={config['host']}",
    f"AWS_API_MCP_PORT={config['port']}",
    f"ENFORCE_LOCAL_MCP={enforce_local}"
  ]
  bind_host = config.get("bind_host")
  if bind_host:
    parts.append(f"AWS_API_MCP_BIND_HOST={bind_host}")
  client_host = config.get("client_host")
  if client_host:
    parts.append(f"AWS_API_MCP_CLIENT_HOST={client_host}")
  if config.get("url"):
    parts.append(f"AWS_API_MCP_URL={config['url']}")
  if config.get("allowed_hosts"):
    parts.append(f"AWS_API_MCP_ALLOWED_HOSTS={config['allowed_hosts']}")
  if config.get("allowed_origins"):
    parts.append(f"AWS_API_MCP_ALLOWED_ORIGINS={config['allowed_origins']}")

  asgi_app = config.get("asgi_app")
  if asgi_app:
    parts.append(f"MCP_ASGI_APP={asgi_app}")
    logger.info(f"MCP command includes ASGI app: {asgi_app}")
  else:
    logger.warning(f"MCP command missing ASGI app for config: {config}")

  parts.append(
    f"/app/scripts/start-mcp-server.sh {quoted_role_arn} {package_arg}"
  )
  command = " ".join(parts)
  logger.info(f"Generated MCP command: {command}")
  return command

cost_explorer_mcp = _build_mcp_config(
  "AWS_COST_EXPLORER_MCP",
  default_host="0.0.0.0",
  default_port="8001",
  default_transport="streamable-http",
  default_client_host="127.0.0.1"
)
# Add ASGI app path for Cost Explorer (uses 'app' instance)
cost_explorer_mcp["asgi_app"] = "awslabs.cost_explorer_mcp_server.server:app.streamable_http_app"

ccapi_mcp = _build_mcp_config(
  "AWS_CCAPI_MCP",
  default_host="0.0.0.0",
  default_port="8002",
  default_transport="streamable-http",
  default_client_host="127.0.0.1"
)
# Add ASGI app path for Cloud Control (uses 'mcp' instance)
ccapi_mcp["asgi_app"] = "awslabs.ccapi_mcp_server.server:mcp.streamable_http_app"

cloudwatch_mcp = _build_mcp_config(
  "AWS_CLOUDWATCH_MCP",
  default_host="0.0.0.0",
  default_port="8003",
  default_transport="streamable-http",
  default_client_host="127.0.0.1"
)
# Add ASGI app path for CloudWatch (uses 'mcp' instance)
cloudwatch_mcp["asgi_app"] = "awslabs.cloudwatch_mcp_server.server:mcp.streamable_http_app"

billing_mcp = _build_mcp_config(
  "AWS_BILLING_MCP",
  default_host="0.0.0.0",
  default_port="8004",
  default_transport="streamable-http",
  default_client_host="127.0.0.1"
)
billing_mcp["asgi_app"] = "awslabs.billing_cost_management_mcp_server.server:mcp.streamable_http_app"

cloudtrail_mcp = _build_mcp_config(
  "AWS_CLOUDTRAIL_MCP",
  default_host="0.0.0.0",
  default_port="8005",
  default_transport="streamable-http",
  default_client_host="127.0.0.1"
)
cloudtrail_mcp["asgi_app"] = "awslabs.cloudtrail_mcp_server.server:mcp.streamable_http_app"

pricing_mcp = _build_mcp_config(
  "AWS_PRICING_MCP",
  default_host="0.0.0.0",
  default_port="8006",
  default_transport="streamable-http",
  default_client_host="127.0.0.1"
)
pricing_mcp["asgi_app"] = "awslabs.aws_pricing_mcp_server.server:mcp.streamable_http_app"




def _streamable_transport_metadata(config: Dict[str, str]):
  transport = (config.get("transport") or "").replace("-", "").replace("_", "").lower()
  if transport != "streamablehttp":
    return None
  return {
    "type": "streamable-http",
    "url": config["url"],
    "auth": config["auth"]
  }

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

def _build_mcp_connections(user: Dict[str, Any]) -> List[Dict[str, Any]]:
  cost_explorer_transport = _streamable_transport_metadata(cost_explorer_mcp)
  ccapi_transport = _streamable_transport_metadata(ccapi_mcp)
  cloudwatch_transport = _streamable_transport_metadata(cloudwatch_mcp)
  billing_transport = _streamable_transport_metadata(billing_mcp)
  cloudtrail_transport = _streamable_transport_metadata(cloudtrail_mcp)
  pricing_transport = _streamable_transport_metadata(pricing_mcp)

  cost_explorer_command = _build_mcp_command(
    config=cost_explorer_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_COST_EXPLORER_MCP_SERVER_VERSION,
    package_name="awslabs.cost-explorer-mcp-server"
  )
  ccapi_command = _build_mcp_command(
    config=ccapi_mcp,
    role_arn=user["aws_role_arn"],
    server_version=CCAPI_MCP_SERVER_VERSION,
    package_name="awslabs.ccapi-mcp-server"
  )
  cloudwatch_command = _build_mcp_command(
    config=cloudwatch_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_CLOUDWATCH_MCP_SERVER_VERSION,
    package_name="awslabs.cloudwatch-mcp-server"
  )
  billing_command = _build_mcp_command(
    config=billing_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_BILLING_MCP_SERVER_VERSION,
    package_name="awslabs.billing-cost-management-mcp-server"
  )
  cloudtrail_command = _build_mcp_command(
    config=cloudtrail_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_CLOUDTRAIL_MCP_SERVER_VERSION,
    package_name="awslabs.cloudtrail-mcp-server"
  )
  pricing_command = _build_mcp_command(
    config=pricing_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_PRICING_MCP_SERVER_VERSION,
    package_name="awslabs.aws-pricing-mcp-server"
  )

  cost_explorer_stdio_command = _build_mcp_command(
    config=cost_explorer_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_COST_EXPLORER_MCP_SERVER_VERSION,
    package_name="awslabs.cost-explorer-mcp-server",
    transport_override="stdio"
  )
  ccapi_stdio_command = _build_mcp_command(
    config=ccapi_mcp,
    role_arn=user["aws_role_arn"],
    server_version=CCAPI_MCP_SERVER_VERSION,
    package_name="awslabs.ccapi-mcp-server",
    transport_override="stdio"
  )
  cloudwatch_stdio_command = _build_mcp_command(
    config=cloudwatch_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_CLOUDWATCH_MCP_SERVER_VERSION,
    package_name="awslabs.cloudwatch-mcp-server",
    transport_override="stdio"
  )
  billing_stdio_command = _build_mcp_command(
    config=billing_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_BILLING_MCP_SERVER_VERSION,
    package_name="awslabs.billing-cost-management-mcp-server",
    transport_override="stdio"
  )
  cloudtrail_stdio_command = _build_mcp_command(
    config=cloudtrail_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_CLOUDTRAIL_MCP_SERVER_VERSION,
    package_name="awslabs.cloudtrail-mcp-server",
    transport_override="stdio"
  )
  pricing_stdio_command = _build_mcp_command(
    config=pricing_mcp,
    role_arn=user["aws_role_arn"],
    server_version=AWS_PRICING_MCP_SERVER_VERSION,
    package_name="awslabs.aws-pricing-mcp-server",
    transport_override="stdio"
  )

  connections = [
    {
      "name": "aws-cost-explorer-mcp-server",
      "command": cost_explorer_command,
      "stdio_command": cost_explorer_stdio_command,
      **({"transport": cost_explorer_transport} if cost_explorer_transport else {})
    },
    {
      "name": "aws-ccapi-mcp-server",
      "command": ccapi_command,
      "stdio_command": ccapi_stdio_command,
      **({"transport": ccapi_transport} if ccapi_transport else {})
    },
    {
      "name": "aws-cloudwatch-mcp-server",
      "command": cloudwatch_command,
      "stdio_command": cloudwatch_stdio_command,
      **({"transport": cloudwatch_transport} if cloudwatch_transport else {})
    },
    {
      "name": "aws-billing-mcp-server",
      "command": billing_command,
      "stdio_command": billing_stdio_command,
      **({"transport": billing_transport} if billing_transport else {})
    },
    {
      "name": "aws-cloudtrail-mcp-server",
      "command": cloudtrail_command,
      "stdio_command": cloudtrail_stdio_command,
      **({"transport": cloudtrail_transport} if cloudtrail_transport else {})
    },
    {
      "name": "aws-pricing-mcp-server",
      "command": pricing_command,
      "stdio_command": pricing_stdio_command,
      **({"transport": pricing_transport} if pricing_transport else {})
    }
  ]
  
  return connections

@cl.password_auth_callback
async def auth_callback(username: str, password: str):
  print(f"[AUTH_DEBUG] auth_callback called for username: {username}")
  user = auth.authenticate(username, password)
  if not user:
    print("[AUTH_DEBUG] Authentication failed")
    return None

  mcp_connections = _build_mcp_connections(user)

  return cl.User(
    identifier=user["identifier"],
    display_name=user["name"],
    metadata={
      "mcp_connections": mcp_connections
    }
  )

@cl.on_chat_start
async def on_chat_start():
  user = cl.user_session.get("user")
  if not user:
    logger.info("User not logged in. Showing login page...")
    await cl.Message(content="Please login to continue.").send()
    return

  # Refresh MCP connections in case configuration changed (e.g. new servers added)
  # This ensures that even if the user session is restored from Redis, we use the latest config.
  # We need the raw user dict for _build_mcp_connections, but cl.User object doesn't expose it directly
  # in the same format as auth.authenticate returns. However, we can reconstruct what we need.
  # The _build_mcp_connections function needs 'aws_role_arn'.
  # We assume 'aws_role_arn' might be in metadata or we need to fetch it.
  # Actually, let's check if we can get it from the user object.
  # The user object in session is cl.User.
  # Let's try to get aws_role_arn from metadata if available, or re-authenticate if possible (but we don't have password).
  # A safer bet is to rely on the fact that we just need the ARN.
  # Let's assume it's in metadata.
  # If not, we might be stuck. But auth_callback puts it in... wait, auth_callback does NOT put role_arn in metadata.
  # It uses it to build commands.
  # We need to fetch the user details again from the session store using the identifier.
  
  full_user_details = store.get_user(user.identifier)
  if full_user_details:
      new_connections = _build_mcp_connections(full_user_details)
      user.metadata["mcp_connections"] = new_connections
      cl.user_session.set("user", user)
      logger.info(f"Refreshed MCP connections for user {user.identifier}")

  guardrails = GuardrailEngine.from_env()
  cl.user_session.set("guardrails", guardrails)

  use_langgraph = ENABLE_LANGGRAPH
  client = None
  if use_langgraph:
    # Get the tool objects for LangGraph
    from mcp_tool_helper import get_configured_mcp_tools
    tools = await get_configured_mcp_tools(user)
    client = LangGraphClient(tools=tools)

  if not use_langgraph:
    client = AzureOpenAIClient(guardrails=guardrails)

  cl.user_session.set("langgraph_enabled", use_langgraph)
  cl.user_session.set("client", client)
  cl.user_session.set("mcp_tools", {})
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
  full_user_details = store.get_user(user.identifier)
  if full_user_details:
      new_connections = _build_mcp_connections(full_user_details)
      user.metadata["mcp_connections"] = new_connections
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
  cl.user_session.set("mcp_tools", {})

  # Save the restored memory/context back into the user session
  cl.user_session.set("memory", memory)

@cl.on_chat_end
async def on_chat_end():
  user = cl.user_session.get("user")
  if user:
    # Do not deregister MCP tools on chat end to allow reuse across sessions/chats
    # await deregister_mcp_tools_for_user(user)
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
    logger.error(f"Error while processing: {traceback.print_exc()}")
    await cl.Message("An error occurred while processing! Please contact admin team!").send()

@cl.action_callback("next_question_click")
async def next_question_click_action_callback(action: cl.Action):
  question = action.payload["question"]
  user_echo = cl.Message(author="user", content=question, type="user_message")
  await user_echo.send()
  await new_message(cl.Message(author="user", content=question))
