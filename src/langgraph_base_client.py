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
- **IMPORTANT**: For Cost Explorer tools, the `metrics` parameter MUST be a valid JSON array of strings. Example: `["UnblendedCost"]`
- **NO_THINK**: Do NOT output <think> tags or reasoning steps. Output only the tool call JSON.

### ⚡ PERFORMANCE RULES
- **Parallel Execution**: ALWAYS call all necessary tools in parallel in a single turn. 
- **Efficiency**: Fetch all required data in the fewest turns possible.
"""

RESPONSE_FORMAT_PROMPT = """
### 📝 RESPONSE FORMATTING (Apply only when sending final text response to the user)
**VISUAL IMPACT IS CRITICAL.**
1. **Structure**: Max 3 bullet points for key findings. **Bold** key numbers. 1 clear recommendation.
2. **Headings**: ALWAYS use `###` for section titles. Strictly avoid using H1 (#).
3. **Use Emojis**: Use emojis liberally (e.g., in headers and lists).
4. **Tables**: **ALWAYS** use tables for comparing multiple items or costs.

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
    return SYSTEM_PROMPT

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
    async def tool_node_with_logging(state: MessagesState):
      last_message = state["messages"][-1]
      if hasattr(last_message, "tool_calls"):
        for tool_call in last_message.tool_calls:
          logger.debug(f"Executing tool: {tool_call['name']} with args: {tool_call['args']}")

      # Use standard ToolNode logic
      tool_node = ToolNode(self.tools)
      return await tool_node.ainvoke(state)

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
    async for msg, metadata in self._app.astream(inputs, config=config, stream_mode="messages"):
      # Only stream from the agent node to avoid echoing tool outputs
      if metadata.get("langgraph_node") == "agent":
        # Yield only string content chunks
        if msg.content and isinstance(msg.content, str):
          filtered_chunk = think_filter.process(msg.content)
          if filtered_chunk:
            yield filtered_chunk
