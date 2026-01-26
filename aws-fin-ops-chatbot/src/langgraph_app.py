"""LangGraph-based conversation client using standard components (KISS)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, TypedDict, Annotated

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
- **Cost Explorer**: Historical cost & usage.
- **Billing**: Invoices, budgets, savings plans.
- **Pricing**: Service pricing & comparisons.
- **CloudWatch**: Metrics, logs, alarms.
  - **IMPORTANT**: For `get_metric_data`, use **naive ISO datetimes** (NO timezone, NO 'Z'). Example: `2023-10-27T10:00:00`.
- **CloudTrail**: Audit logs, user activity.
- **Cloud Control API**: Resource management.

---

### 🧠 STRATEGIC WORKFLOWS
1.  **Debugging Cost Spikes**: Cost Explorer (Identify) -> CloudWatch (Correlate) -> CloudTrail (Root Cause).
2.  **Cost Optimization**: Cost Explorer (Usage) -> Pricing (Cheaper Options) -> Billing (Savings Plans).

---

### 📝 RESPONSE GUIDELINES
**VISUAL IMPACT IS CRITICAL.**
1.  **Structure**:
    - **Headline**: MUST use `###` markdown. Example: `### 🚀 S3 Cost Spike Analysis`
    - **Key Findings**: Max 3 bullet points. **Bold** key numbers and terms.
    - **Action**: 1 clear recommendation.
2.  **Tone**: Direct, professional, and confident.
3.  **Formatting**:
    - **Headings**: ALWAYS use `###` for section titles.
    - **Metrics**: ALWAYS **bold** key numbers (e.g., **$50.00**).
    - **Resources**: Use `code` for IDs.
    - **Do NOT use H1 (#) headers.** They are too big. Start with H2 (##) or H3 (###).
    - **Use Emojis & Icons:** Use emojis liberally throughout your response (e.g., in headers, lists, and key points) to make it look robust and user-friendly.
    - **Use Markdown** to make your responses beautiful.
    - **Tables**: **ALWAYS** use tables for comparing multiple items, regions, instance types, or costs.

### ⚡ PERFORMANCE RULES
1.  **Parallel Execution**: When comparing multiple regions or services, **ALWAYS call all necessary tools in parallel** in a single turn. Do not wait for one result before requesting the next.
2.  **Efficiency**: Fetch all required data (pricing, specs, usage) in the fewest number of turns possible.
3.  **Timeouts**: If a tool call fails, retry once with a simplified query.

---

### 🔮 NEXT STEPS (JSON)
At the very end, provide 3 follow-up questions in a JSON array inside a `json_suggestions` code block.
Format: `[{"question": "...", "label": "...", "description": "...", "icon": "..."}]`
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
                    logger.info(f"[TOOL_DEBUG] Executing tool: {tool_call['name']} with args: {tool_call['args']}")
            
            # Use standard ToolNode logic
            tool_node = ToolNode(self.tools)
            return await tool_node.ainvoke(state)

        workflow.add_node("agent", llm_node)
        workflow.add_node("tools", tool_node_with_logging)

        # Add edges
        workflow.add_edge(START, "agent")
        
        # Conditional edge: agent -> tools (if tool call) OR agent -> END (if final answer)
        workflow.add_conditional_edges(
            "agent",
            tools_condition,
        )
        
        # Edge: tools -> agent (loop back to agent after tool execution)
        workflow.add_edge("tools", "agent")

        return workflow

    async def stream_response(
        self,
        message: str,
        session_id: str,
        user_id: str,
        guardrails: Any = None
    ):
        """Streams the response from the graph."""
        
        # Prepare initial state
        # We only pass the NEW message. The graph loads history from the checkpointer.
        inputs = {
            "messages": [
                HumanMessage(content=message)
            ]
        }

        # Use session_id as thread_id for memory
        config = {"configurable": {"thread_id": session_id, "user_id": user_id}}

        # Run guardrails on input if provided
        if guardrails:
            try:
                guardrails.guard_input(
                    session_id=session_id,
                    user_id=user_id,
                    text=message
                )
            except Exception as e:
                yield str(e)
                return

        # Stream events using astream (values mode) to avoid pickling issues with astream_events
        logger.info(f"[LANGGRAPH_DEBUG] Starting astream with inputs: {inputs}")
        last_content = ""
        async for event in self._app.astream(inputs, config=config, stream_mode="values"):
            logger.info(f"[LANGGRAPH_DEBUG] Received event: {list(event.keys())}")
            if "messages" in event:
                messages = event["messages"]
                if not messages:
                    continue
                    
                last_message = messages[-1]
                
                # If it's an AI message
                if last_message.type == "ai":
                    content = last_message.content
                    if content:
                        # In values mode, we get the full content.
                        # If we want to stream, we can yield the diff, but for now let's just 
                        # yield the new content if it's different from what we saw (which handles the final response).
                        # However, since we might get intermediate steps (like tool calls), we need to be careful.
                        
                        # If it has tool_calls, it's an intermediate step (usually)
                        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
                            # We can optionally yield a status update here
                            pass
                        else:
                            # It's likely the final answer or a text response
                            # We yield the full content. The UI will render it.
                            # Since we are not streaming tokens, we yield the whole thing.
                            # To avoid duplicate full text in UI if multiple events emit it,
                            # we can check if it's the same.
                            if content != last_content:
                                yield content
                                last_content = content
