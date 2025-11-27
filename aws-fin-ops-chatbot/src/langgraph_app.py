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

# Configure logging
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an advanced AWS FinOps assistant.
Your goal is to help users optimize their AWS costs and usage.

You have access to the following tools:
- AWS Cost Explorer: For retrieving cost and usage data.
- AWS Cloud Control API: For listing and managing AWS resources.

GUIDELINES:
1. ALWAYS use the available tools to answer questions. Do not hallucinate data.
2. If a tool fails, explain the error to the user and suggest a fix if possible.
3. If the user asks about a specific time range, ensure the tool arguments match that range.
4. Be concise and helpful.

FORMATTING:
- **Do NOT use H1 (#) headers.** They are too big. Start with H2 (##) or H3 (###).
- **Use Emojis & Icons:** Use emojis liberally throughout your response (e.g., in headers, lists, and key points) to make it look robust and user-friendly.
- Use **Markdown** to make your responses beautiful.
- Use **Tables** for data.
- Use **Bold** for emphasis.

SUGGESTIONS:
At the very end of your response, provide 3 relevant follow-up questions that the user might want to ask next.
Format these suggestions strictly as a JSON array inside a markdown code block labeled `json_suggestions`.
Each suggestion should have:
- "question": The exact text the user would send.
- "label": A short title (max 3 words).
- "description": A brief explanation (max 5-7 words) of what this question does.
- "icon": An emoji.

Example:
```json_suggestions
[
  {"question": "Show me cost breakdown by service", "label": "Cost by Service", "description": "See spend per AWS service", "icon": "💰"},
  {"question": "List my EC2 instances", "label": "List EC2", "description": "View all running instances", "icon": "💻"},
  {"question": "What are my forecasted costs?", "label": "Forecast", "description": "Predict next month's spend", "icon": "📈"}
]
```
"""

class MessagesState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]

class LangGraphClient:
    def __init__(self, tools: List[BaseTool]):
        self.tools = tools
        self._llm = self._init_llm()
        self._graph = self._build_graph()
        self._app = self._graph.compile()

    def _init_llm(self) -> AzureChatOpenAI:
        llm_kwargs = {
            "azure_deployment": os.environ["AZURE_OPENAI_MODEL"],
            "azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
            "api_key": os.environ["AZURE_OPENAI_API_KEY"],
            "api_version": os.environ["OPENAI_API_VERSION"],
            "temperature": 1,
            "streaming": True
        }
        return AzureChatOpenAI(**llm_kwargs)

    def _build_graph(self) -> StateGraph:
        # Bind tools to LLM
        llm_with_tools = self._llm.bind_tools(self.tools)

        def llm_node(state: MessagesState):
            return {"messages": [llm_with_tools.invoke(state["messages"])]}

        def guard_node(state: MessagesState):
            # Placeholder for guardrails if needed, or simple pass-through
            # In KISS approach, we keep it simple for now
            return state

        workflow = StateGraph(MessagesState)

        # Add nodes
        workflow.add_node("agent", llm_node)
        workflow.add_node("tools", ToolNode(self.tools))

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
        inputs = {
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=message)
            ]
        }

        config = {"configurable": {"session_id": session_id, "user_id": user_id}}

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
            logger.info(f"[LANGGRAPH_DEBUG] Received event: {event.keys()}")
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
