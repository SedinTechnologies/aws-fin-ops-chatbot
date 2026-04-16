from __future__ import annotations

import os, logging
from typing import Any, List, TypedDict, Annotated

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# Configure logging
logger = logging.getLogger(__name__)

TOOL_CALLING_PROMPT = """
You are an advanced AWS FinOps Assistant. Your MUST provide crisp, actionable insights regarding AWS cloud infrastructure and billing.

### 🛑 REDIRECT NON-AWS QUERIES
You are strictly bounded to AWS, DevOps, and FinOps. Decline to answer unrelated topics.

### 🛠️ TOOL CALLING GUIDELINES (CRITICAL)
- You MUST use the provided tools to answer questions. Do NOT explain how to get data — call the tools to get it.
- **IMPORTANT**: For CloudWatch `get_metric_data`, use **naive ISO datetimes** (NO timezone, NO 'Z'). Example: `2023-10-27T10:00:00`.
- **IMPORTANT**: For Cost Explorer tools, the `metrics` and `group_by` parameters MUST be passed as **JSON stringified arrays**, NOT actual JSON arrays. Example: `metrics="[\"UnblendedCost\"]"`, `group_by="[{\"Type\": \"DIMENSION\", \"Key\": \"SERVICE\"}]"`
- **NO_THINK**: Do NOT output <think> tags or reasoning steps. Output only the tool call JSON.
- **SILENT TOOL CALLS**: When you decide to call a tool, do NOT output any explanatory text before or alongside the tool call. Just call the tool silently. Only produce text in your FINAL response after you have all tool results.

### ⚡ PERFORMANCE & DATA RULES
- **Parallel Execution**: ALWAYS call all necessary tools in parallel in a single turn.
- **Efficiency**: Fetch all required data in the fewest turns possible.
- **Granularity (CRITICAL)**: For ANY Cost Explorer queries fetching more than 14 days of data, you MUST explicitly set the parameter `granularity="MONTHLY"`. If you use `DAILY` for long periods, the AWS MCP Server will crash or lock the result into an unreadable state due to massive payload sizes!

### 🔢 DATA ACCURACY (CRITICAL)
- **EXACT FIGURES**: When reporting cost data, you MUST copy the EXACT dollar amounts from the tool output. NEVER round, estimate, or approximate numbers.
- **NO FABRICATION**: If the tool returns $2,345.67, report exactly $2,345.67. Do NOT change it to $2,346 or $2,350 or any other value.
- **NO DUPLICATION**: Each service must appear EXACTLY ONCE per period. If a service appears in the tool output once, it must appear in your table once. Never duplicate rows.
- **NO EXTRAPOLATION**: If the tool does not return data for a month, do NOT copy another month's values into it. Only report data that the tool explicitly returned.
- **VERIFY TOTALS**: Manually add up individual amounts to confirm your totals match. If they don't, recompute before responding.

### 📅 DATE RANGE RULES (CRITICAL)
- "Last N months" means the last N COMPLETED calendar months (excluding the current partial month). Example: If today is 2026-04-16, "last 3 months" = January 1, 2026 to April 1, 2026 (Jan, Feb, Mar).
- For a SPECIFIC month query like "March 2026 cost", use start_date=2026-03-01 and end_date=2026-04-01 (Cost Explorer end_date is exclusive).
- NEVER overlap date ranges or include the current partial month unless the user explicitly asks for it (e.g., "including this month", "year to date").
"""

RESPONSE_FORMAT_PROMPT = """
### 📝 RESPONSE FORMATTING (Apply only when sending final text response to the user)
**VISUAL IMPACT IS CRITICAL.**
1. **Structure**: Max 3 bullet points for key findings. **Bold** key numbers. 1 clear recommendation.
2. **Headings**: ALWAYS use `###` for section titles. Strictly avoid using H1 (#).
3. **Use Emojis**: Use emojis liberally (e.g., in headers and lists).
4. **Tables**: **ALWAYS** use Markdown tables for presenting ANY cost or billing data, regardless of the duration.
   - **CRITICAL FOR COST DATA**: When summarizing multi-month data, ALWAYS include EVERY month in the table (do not skip any months) to ensure an accurate timeline.
   - **MONTHLY TOTALS ROW**: For multi-month tables, ALWAYS include a bold **Total** row at the bottom that shows the sum for EACH month column AND the grand total. Example: `| **Total** | **$3,200** | **$2,800** | **$6,000** |`
   - **DYNAMIC PRESENTATION**: Adapt your table columns to the user's specific query. If they ask for breakdowns, include columns for the specific services.
   - **MATH & TOTALS**: The 'Total Cost' must unconditionally match the true gross sum of the period! If you filter columns for brevity, you MUST still calculate the total logically including 'Tax' and miscellaneous unblended services.
   - **TRUTHFULNESS**: Do NOT hallucinate origins. Always state data is sourced from `AWS Cost Explorer`, never make up services like 'CloudTrail Lake'.

### 🔮 NEXT STEPS
At the very end of your response, provide 3 short actionable follow-up questions explicitly formatted like:
```
suggestions:
question 1
question 2
question 3
```
"""

SYSTEM_PROMPT = f"{TOOL_CALLING_PROMPT}\n{RESPONSE_FORMAT_PROMPT}"

class MessagesState(TypedDict):
  messages: Annotated[List[BaseMessage], add_messages]

class BaseLangGraphClient:
  def __init__(self, tools: List[BaseTool]):
    self.tools = tools
    self._llm = self._init_llm()
    self._graph = self._build_graph()
    self.checkpointer = MemorySaver()
    self._app = self._graph.compile(checkpointer=self.checkpointer)

  def load_historical_messages(self, session_id: str, messages: List[dict]):
    config = { "configurable": { "thread_id": session_id } }
    langchain_messages = []
    from langchain_core.messages import AIMessage, HumanMessage
    for msg in messages:
      if msg["role"] == "user":
        langchain_messages.append(HumanMessage(content=msg["content"]))
      elif msg["role"] == "assistant":
        langchain_messages.append(AIMessage(content=msg["content"]))

    if langchain_messages:
      self._app.update_state(config, {"messages": langchain_messages})

  def _init_llm(self):
    raise NotImplementedError("Subclasses must implement _init_llm")

  def _get_system_prompt(self) -> str:
    """Returns the system prompt for the agent. Subclasses can override if needed."""
    from datetime import datetime
    current_date = datetime.now().strftime("%Y-%m-%d")
    return f"CURRENT SYSTEM DATE: {current_date}\n\n{SYSTEM_PROMPT}"

  def _build_graph(self) -> StateGraph:
    # Bind tools to LLM
    llm_with_tools = self._llm.bind_tools(self.tools)

    def llm_node(state: MessagesState):
      try:
        messages = [SystemMessage(content=self._get_system_prompt())] + state["messages"]
        return {"messages": [llm_with_tools.invoke(messages)]}
      except Exception as e:
        logger.error(f"LLM Error: {e}", exc_info=True)
        from langchain_core.messages import AIMessage
        return {"messages": [AIMessage(content=f"An error occurred while generating the response or calling tools. Error details: {str(e)}")]}

    def guard_node(state: MessagesState):
      # Placeholder for guardrails if needed, or simple pass-through
      # In KISS approach, we keep it simple for now
      return state

    workflow = StateGraph(MessagesState)

    # Add nodes
    # Custom tool node with logging
    # Tool names that require JSON-stringified array/object parameters
    COST_TOOL_NAMES = {'cost_explorer', 'cost-explorer', 'get_cost_and_usage', 'getcostexplorer'}
    STRINGIFY_KEYS = {'metrics', 'group_by', 'filter'}

    async def tool_node_with_logging(state: MessagesState):
      import json
      import copy
      last_message = state["messages"][-1]
      modified = False

      if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        new_tool_calls = copy.deepcopy(last_message.tool_calls)

        for tool_call in new_tool_calls:
          # Intercept and stringify lists/dicts for fastmcp compatibility
          if tool_call['name'].lower().replace('-', '_') in {n.replace('-', '_') for n in COST_TOOL_NAMES}:
            for key in STRINGIFY_KEYS:
              if key in tool_call['args'] and not isinstance(tool_call['args'][key], str):
                tool_call['args'][key] = json.dumps(tool_call['args'][key])
                modified = True
          logger.debug(f"Executing tool: {tool_call['name']} with args: {tool_call['args']}")

        if modified:
          from langchain_core.messages import AIMessage
          new_message = AIMessage(
              content=last_message.content,
              tool_calls=new_tool_calls,
              id=last_message.id,
              additional_kwargs=last_message.additional_kwargs
          )
          state = {**state, "messages": state["messages"][:-1] + [new_message]}

      # Use standard ToolNode logic
      tool_node = ToolNode(self.tools)
      result = await tool_node.ainvoke(state)

      available_tool_names = [t.name for t in self.tools]
      logger.debug(f"Available tools for LLM: {available_tool_names}")

      # Log the result of the tool
      if "messages" in result and result["messages"]:
          # If we modified the AIMessage, we MUST return it so LangGraph state overwrites the old unstringified one!
          if modified:
              result["messages"].insert(0, new_message)

          for msg in result["messages"]:
              logger.debug(f"Tool Result: {msg.name if hasattr(msg, 'name') else 'AIMessage'} -> {msg.content}")

      return result

    workflow.add_node("agent", llm_node)
    workflow.add_node("tools", tool_node_with_logging)

    # Add edges
    workflow.add_edge(START, "agent")
    # Conditional edge: agent -> tools (if tool call) OR agent -> END (if final answer)
    workflow.add_conditional_edges("agent", tools_condition)
    # Edge: tools -> agent (loop back to agent after tool execution)
    workflow.add_edge("tools", "agent")

    return workflow

  def _filter_thinking_tokens(self) -> Any:
    """Returns an object to strip <think> blocks from streamed output."""
    class ThinkFilter:
      def __init__(self):
        self.in_think_block = False
      
      def process(self, piece: str) -> str:
        if not piece: return ""
        
        if self.in_think_block:
          if "</think>" in piece:
            self.in_think_block = False
            return piece.split("</think>", 1)[1]
          return ""
          
        if "<think>" in piece:
          self.in_think_block = True
          return piece.split("<think>", 1)[0]
          
        return piece
        
    return ThinkFilter()

  async def stream_response(self, message: str, session_id: str, user_id: str, guardrails: Any = None):
    """Streams the response from the graph."""

    logger.debug(f"Handling new message for user: {user_id}, message: {message}")

    # Prepare initial state
    # We only pass the NEW message. The graph loads history from the checkpointer.
    inputs = { "messages": [HumanMessage(content=message)] }

    # Use session_id as thread_id for memory
    recursion_limit = int(os.environ.get("LANGGRAPH_RECURSION_LIMIT", "40"))
    config = {
        "configurable": { "thread_id": session_id, "user_id": user_id },
        "recursion_limit": recursion_limit
    }

    # Run guardrails on input if provided
    if guardrails:
      try:
        guardrails.guard_input(session_id=session_id, user_id=user_id, text=message)
      except Exception as e:
        yield str(e)
        return

    think_filter = self._filter_thinking_tokens()

    # Stream events using astream (messages mode) to get token-level streaming
    try:
      prev_node = None
      async for msg, metadata in self._app.astream(inputs, config=config, stream_mode="messages"):
        current_node = metadata.get("langgraph_node")

        # When the agent resumes after tool execution, insert a newline separator
        # so the post-tool response starts on a fresh line (preserves markdown headings).
        if current_node == "agent" and prev_node == "tools":
          yield "\n\n"

        if current_node == "agent":
          # Extract text content, handling both str and list-of-blocks formats
          text = ""
          if isinstance(msg.content, str):
            text = msg.content
          elif isinstance(msg.content, list):
            text = "".join(
              block.get("text", "") if isinstance(block, dict) else str(block)
              for block in msg.content
            )
          if text:
            filtered_chunk = think_filter.process(text)
            if filtered_chunk:
              yield filtered_chunk

        prev_node = current_node
    except Exception as e:
      logger.error(f"Stream Error: {e}", exc_info=True)
      yield f"\n\n**Error:** {str(e)}"
