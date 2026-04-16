from __future__ import annotations

import os, json, copy, logging
from datetime import datetime
from typing import Any, List, TypedDict, Annotated
from collections import deque

from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

TOOL_CALLING_PROMPT = """
You are an advanced AWS FinOps Assistant. You MUST provide crisp, actionable insights regarding AWS cloud infrastructure and billing.

### 🛑 REDIRECT NON-AWS QUERIES
You are strictly bounded to AWS, DevOps, and FinOps. Decline to answer unrelated topics.

### 🔧 TOOL ROUTING (CRITICAL — read this FIRST)
For ANY question about costs, billing, spending, or pricing you MUST call the tool named **`cost-explorer`**. Do NOT use CloudTrail, documentation, IAC, or any other tool for cost queries.

**`cost-explorer` tool usage**:
- `operation="getCostAndUsage"` — for historical cost/spend data (most common)
- `operation="getCostForecast"` — for future cost projections
- `operation="getDimensionValues"` — to list available services, accounts, regions
- `operation="getSavingsPlansUtilization"` — for Savings Plans data
- Required params: `operation`, `start_date`, `end_date`, `granularity`, `metrics`
- `metrics` and `group_by` MUST be **JSON stringified arrays**: `metrics="[\"UnblendedCost\"]"`, `group_by="[{\"Type\": \"DIMENSION\", \"Key\": \"SERVICE\"}]"`

### 🛠️ GENERAL TOOL GUIDELINES
- You MUST use tools to answer questions. Do NOT explain how to get data — call the tools.
- For CloudWatch `get_metric_data`, use **naive ISO datetimes** (NO timezone, NO 'Z'). Example: `2023-10-27T10:00:00`.
- **NO_THINK**: Do NOT output <think> tags or reasoning steps. Output only the tool call JSON.
- **SILENT TOOL CALLS**: Do NOT output any text before or alongside a tool call. Only produce text in your FINAL response after you have all tool results.

### ⚡ PERFORMANCE & DATA RULES
- **Parallel Execution**: ALWAYS call all necessary tools in parallel in a single turn.
- **Efficiency**: Fetch all required data in the fewest turns possible.
- **Granularity (CRITICAL)**: For ANY Cost Explorer queries fetching more than 14 days of data, you MUST explicitly set the parameter `granularity="MONTHLY"`. If you use `DAILY` for long periods, the AWS MCP Server will crash or lock the result into an unreadable state due to massive payload sizes!

### 🔢 DATA ACCURACY (CRITICAL)
- **EXACT FIGURES**: Report all dollar amounts rounded to exactly 2 decimal places (e.g., $1,748.63, NOT $1,748.6341603047). Do NOT invent or change the magnitude of numbers — only format them as standard currency.
- **NO DUPLICATION**: Each service must appear EXACTLY ONCE per period. If a service appears in the tool output once, it must appear in your table once. Never duplicate rows.
- **NO EXTRAPOLATION**: If the tool does not return data for a month, do NOT copy another month's values into it. Only report data that the tool explicitly returned.
- **VERIFY TOTALS**: Manually add up individual amounts to confirm your totals match. If they don't, recompute before responding.

### 📅 DATE RANGE RULES (CRITICAL)
- "Last N months" means the last N COMPLETED calendar months (excluding the current partial month). Example: If today is 2026-04-16, "last 3 months" = January 1, 2026 to April 1, 2026 (Jan, Feb, Mar).
- For a SPECIFIC month query like "March 2026 cost", use start_date=2026-03-01 and end_date=2026-04-01 (Cost Explorer end_date is exclusive).
- NEVER overlap date ranges or include the current partial month unless the user explicitly asks for it (e.g., "including this month", "year to date").
"""

RESPONSE_FORMAT_PROMPT = """
### RESPONSE FORMAT RULES

**Layout** — every cost response MUST follow this exact order:
1. One `###` heading (short, e.g., `### 📊 AWS Cost Summary — March 2026`). No H1/H2. No redundant text like "(requested month: …)".
2. A one-line summary sentence with the total spend bolded (e.g., "Your total AWS spend for March 2026 was **$3,352.43** across 12 active services.").
3. `### Key Takeaways` — 2-3 short plain-English bullet points. Simple language a non-technical reader can understand. Bold the dollar amount only. One bullet = one insight.
4. `### Cost Breakdown` — the cost **table**. Present ALL cost data here, not in bullet points.
5. `### Recommendation` — one short actionable paragraph.

**Table rules**:
- Columns: `| Service | Jan | Feb | Mar |` — one column per month. NO per-service total column.
- Last row MUST be: `| **Total** | **$3,500** | **$3,300** | **$3,600** |` showing each month's total spend.
- Include every month requested. REMOVE any service row where the cost is $0.00 in every column — do not show it at all.
- Monthly totals must equal the exact sum of all service rows above.
- Data source is AWS Cost Explorer. Do not fabricate service names.

**Style**:
- Keep headings short and clean — no parenthetical metadata.
- Use one emoji per heading at most (e.g., `### 📊 Cost Summary`). No emojis inline with dollar amounts or service names.
- Do NOT list cost figures inside bullet points — that belongs in the table.

**Suggestions (MANDATORY)** — your response MUST always end with the keyword `suggestions:` on its own line, followed by exactly 3 follow-up questions (one per line, no numbering, no prefixes). The UI uses this keyword to render clickable buttons — omitting it means the user sees no follow-up options.
- Each question MUST be specific to the data and query the user just made. Do NOT use generic or static questions.
- Bad: "What are my costs?" — Good: "Which EC2 instances drove the $1,748.63 compute cost in March?"

suggestions:
<question specific to the user's query and results>
<question specific to the user's query and results>
<question specific to the user's query and results>
"""

SYSTEM_PROMPT = f"{TOOL_CALLING_PROMPT}\n{RESPONSE_FORMAT_PROMPT}"

# Tool names that require JSON-stringified array/object parameters for fastmcp
_COST_TOOL_NAMES = {'cost_explorer', 'cost-explorer', 'get_cost_and_usage', 'getcostexplorer'}
_COST_TOOL_NAMES_NORMALIZED = {n.replace('-', '_') for n in _COST_TOOL_NAMES}
_STRINGIFY_KEYS = {'metrics', 'group_by', 'filter'}

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
    current_date = datetime.now().strftime("%Y-%m-%d")
    return f"CURRENT SYSTEM DATE: {current_date}\n\n{SYSTEM_PROMPT}"

  def _build_graph(self) -> StateGraph:
    llm_with_tools = self._llm.bind_tools(self.tools)
    tool_node = ToolNode(self.tools)

    def llm_node(state: MessagesState):
      try:
        messages = [SystemMessage(content=self._get_system_prompt())] + state["messages"]
        return {"messages": [llm_with_tools.invoke(messages)]}
      except Exception as e:
        logger.error(f"LLM Error: {e}", exc_info=True)
        return {"messages": [AIMessage(content=f"An error occurred while generating the response or calling tools. Error details: {str(e)}")]}

    async def tool_node_with_logging(state: MessagesState):
      last_message = state["messages"][-1]
      modified = False

      if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        new_tool_calls = copy.deepcopy(last_message.tool_calls)

        for tc in new_tool_calls:
          if tc['name'].lower().replace('-', '_') in _COST_TOOL_NAMES_NORMALIZED:
            for key in _STRINGIFY_KEYS:
              if key in tc['args'] and not isinstance(tc['args'][key], str):
                tc['args'][key] = json.dumps(tc['args'][key])
                modified = True
          logger.debug(f"Executing tool: {tc['name']} with args: {tc['args']}")

        if modified:
          new_message = AIMessage(
              content=last_message.content,
              tool_calls=new_tool_calls,
              id=last_message.id,
              additional_kwargs=last_message.additional_kwargs
          )
          state = {**state, "messages": state["messages"][:-1] + [new_message]}

      result = await tool_node.ainvoke(state)

      if "messages" in result and result["messages"]:
          if modified:
              result["messages"].insert(0, new_message)
          for msg in result["messages"]:
              logger.debug(f"Tool Result: {msg.name if hasattr(msg, 'name') else 'AIMessage'} -> {msg.content}")

      return result

    workflow = StateGraph(MessagesState)
    workflow.add_node("agent", llm_node)
    workflow.add_node("tools", tool_node_with_logging)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", tools_condition)
    workflow.add_edge("tools", "agent")
    return workflow

  async def stream_response(self, message: str, session_id: str, user_id: str, guardrails: Any = None):
    """Streams the response from the graph."""
    inputs = { "messages": [HumanMessage(content=message)] }
    recursion_limit = int(os.environ.get("LANGGRAPH_RECURSION_LIMIT", "40"))
    config = {
        "configurable": { "thread_id": session_id, "user_id": user_id },
        "recursion_limit": recursion_limit
    }

    if guardrails:
      try:
        guardrails.guard_input(session_id=session_id, user_id=user_id, text=message)
      except Exception as e:
        yield str(e)
        return

    in_think_block = False
    # Repetition detection: track recent lines, break if same line repeats 3+ times
    recent_lines = deque(maxlen=10)

    try:
      prev_node = None
      async for msg, metadata in self._app.astream(inputs, config=config, stream_mode="messages"):
        current_node = metadata.get("langgraph_node")

        # Newline separator when agent resumes after tool execution (preserves markdown headings)
        if current_node == "agent" and prev_node == "tools":
          yield "\n\n"

        if current_node == "agent":
          text = ""
          if isinstance(msg.content, str):
            text = msg.content
          elif isinstance(msg.content, list):
            text = "".join(
              block.get("text", "") if isinstance(block, dict) else str(block)
              for block in msg.content
            )

          if text:
            # Filter <think> blocks
            if in_think_block:
              if "</think>" in text:
                in_think_block = False
                text = text.split("</think>", 1)[1]
              else:
                text = ""
            if "<think>" in text:
              in_think_block = True
              text = text.split("<think>", 1)[0]

            if text:
              # Repetition detection on complete lines
              for line in text.split('\n'):
                stripped = line.strip()
                if stripped:
                  recent_lines.append(stripped)
              if len(recent_lines) >= 6 and len(set(list(recent_lines)[-6:])) == 1:
                yield "\n\n*(...output truncated — repetitive content detected)*"
                break

              yield text

        prev_node = current_node
    except Exception as e:
      logger.error(f"Stream Error: {e}", exc_info=True)
      yield f"\n\n**Error:** {str(e)}"
