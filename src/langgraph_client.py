from __future__ import annotations

import logging
import os
from typing import Any, List, TypedDict, Annotated

from langchain_openai import AzureChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# Configure logging
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an advanced AWS FinOps Assistant. Your goal is to provide **sharp, crisp, and actionable** insights.

### 🛠️ AVAILABLE TOOLS
- **AWS API** (`aws-api-mcp-server`): General-purpose direct interaction with any AWS service API. Use this to read resource configurations, execute operational commands, or query service-specific data across the AWS ecosystem (e.g., EC2, S3, Lambda, ECS, CloudWatch, etc.).
  - **IMPORTANT**: For CloudWatch `get_metric_data`, use **naive ISO datetimes** (NO timezone, NO 'Z'). Example: `2023-10-27T10:00:00`.
- **AWS Documentation** (`aws-documentation-mcp-server`): Up-to-date AWS service documentation, limits, and best practices.
- **AWS Pricing** (`aws-pricing-mcp-server`): Service pricing & cost comparisons.
- **AWS Billing & Cost Management** (`aws-billing-cost-management-mcp-server`): Cost Explorer, invoices, budgets, and savings plans.
- **AWS CloudTrail** (`aws-cloudtrail-mcp-server`): Audit logs, user activity, and security investigations.
- **AWS IaC** (`aws-iac-mcp-server`): Infrastructure as Code insights.
---
### 🧠 STRATEGIC WORKFLOWS
1. **Debugging Cost Spikes**: Cost Explorer (Identify) -> CloudWatch (Correlate) -> CloudTrail (Root Cause).
2. **Cost Optimization**: Cost Explorer (Usage) -> Pricing (Cheaper Options) -> Billing (Savings Plans).
---
### 📝 RESPONSE GUIDELINES
**VISUAL IMPACT IS CRITICAL.**
1. **Structure**:
  - **Headline**: MUST use `###` markdown. Example: `### 🚀 S3 Cost Spike Analysis`
  - **Key Findings**: Max 3 bullet points. **Bold** key numbers and terms.
  - **Action**: 1 clear recommendation.
2. **Tone**: Direct, professional, and confident.
3. **Formatting**:
  - **Headings**: ALWAYS use `###` for section titles.
  - **Metrics**: ALWAYS **bold** key numbers (e.g., **$50.00**).
  - **Resources**: Use `code` for IDs.
  - **Header Hierarchy**: Strictly avoid using H1 (#) headers; always start your header hierarchy at H2 (##) or H3 (###).
  - **Use Emojis & Icons:** Use emojis liberally throughout your response (e.g., in headers, lists, and key points) to make it look robust and user-friendly.
  - **Use Markdown** to make your responses beautiful.
  - **Tables**: **ALWAYS** use tables for comparing multiple items, regions, instance types, or costs.

### ⚡ PERFORMANCE RULES
1. **Parallel Execution**: When comparing multiple regions or services, **ALWAYS call all necessary tools in parallel** in a single turn. Do not wait for one result before requesting the next.
2. **Efficiency**: Fetch all required data (pricing, specs, usage) in the fewest number of turns possible.
3. **Timeouts**: If a tool call fails, retry once with a simplified query.
---
### 🔮 NEXT STEPS
At the very end, provide 3 follow-up questions.
These questions MUST be written as direct, actionable queries or commands without conversational fillers like "Can you", "Do you", or "Could you" (e.g., "Show me the logs for XYZ", "What is the cost breakdown for EC2?", "List the top resources by cost").
Make sure each suggestion shouldn't be more than 80 chars long.
Format exactly like below:
```
suggestions:
question 1
question 2
question 3
```
"""

class MessagesState(TypedDict):
  messages: Annotated[List[BaseMessage], add_messages]

class LangGraphClient:
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

  def _init_llm(self) -> AzureChatOpenAI:
    llm_kwargs = {
      "azure_deployment": os.environ["AZURE_OPENAI_MODEL"],
      "azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
      "api_key": os.environ["AZURE_OPENAI_API_KEY"],
      "api_version": os.environ["OPENAI_API_VERSION"],
      "streaming": True
    }
    return AzureChatOpenAI(**llm_kwargs)

  def _build_graph(self) -> StateGraph:
    # Bind tools to LLM
    llm_with_tools = self._llm.bind_tools(self.tools)

    def llm_node(state: MessagesState):
      # Prepend the System Prompt to ensure the LLM always has instructions
      # We do this here instead of adding it to the state to avoid duplication in memory
      messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
      return {"messages": [llm_with_tools.invoke(messages)]}

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

  async def stream_response(self, message: str, session_id: str, user_id: str, guardrails: Any = None):
    """Streams the response from the graph."""

    logger.debug(f"Handling new message for user: {user_id}, message: {message}")

    # Prepare initial state
    # We only pass the NEW message. The graph loads history from the checkpointer.
    inputs = { "messages": [HumanMessage(content=message)] }

    # Use session_id as thread_id for memory
    config = { "configurable": { "thread_id": session_id, "user_id": user_id } }

    # Run guardrails on input if provided
    if guardrails:
      try:
        guardrails.guard_input(session_id=session_id, user_id=user_id, text=message)
      except Exception as e:
        yield str(e)
        return

    # Stream events using astream (messages mode) to get token-level streaming
    async for msg, metadata in self._app.astream(inputs, config=config, stream_mode="messages"):
      # Only stream from the agent node to avoid echoing tool outputs
      if metadata.get("langgraph_node") == "agent":
        # Yield only string content chunks
        if msg.content and isinstance(msg.content, str):
          yield msg.content
