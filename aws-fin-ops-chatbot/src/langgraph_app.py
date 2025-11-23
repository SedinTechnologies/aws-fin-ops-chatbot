"""LangGraph-based conversation client with MCP tooling support."""

from __future__ import annotations

import json
import logging
import os
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict, AsyncGenerator

from azure_openai_client import SYSTEM_PROMPT
from mcp_tool_helper import call_tool
from response_utils import parse_structured_response
from tool_utils import (
  _load_tool_arguments,
  _missing_required_fields,
  _normalize_tool_arguments,
  _populate_default_tool_arguments,
  _serialize_tool_content
)

try:  # Optional dependency guard
  from langgraph.graph import START, END, StateGraph
  from langgraph.graph.graph import CompiledGraph
  from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
  from langchain_openai import AzureChatOpenAI
except ImportError:  # pragma: no cover - handled at runtime
  START = END = StateGraph = None  # type: ignore[assignment]
  CompiledGraph = None  # type: ignore[assignment]
  AzureChatOpenAI = None  # type: ignore[assignment]
  HumanMessage = AIMessage = SystemMessage = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


class GraphNotAvailableError(RuntimeError):
  """Raised when LangGraph dependencies are missing."""


class ChatState(TypedDict, total=False):
  history: List[Dict[str, Any]]
  user_input: str
  raw_output: str
  final_content: str
  next_questions: List[Dict[str, Any]]
  tool_calls: List[Dict[str, Any]]
  tools: List[Dict[str, Any]]
  loop_count: int


def _ensure_dependencies():
  if StateGraph is None or AzureChatOpenAI is None:
    raise GraphNotAvailableError(
      "LangGraph prototype requires langgraph and langchain-openai dependencies."
    )


def _parse_final_message(content: str) -> tuple[str, List[dict]]:
  try:
    _, markdown, next_questions = parse_structured_response(content)
    return markdown, next_questions
  except Exception:  # noqa: BLE001
    return content, []


def _lc_messages_from_history(history: List[Dict[str, Any]]):
  messages = []
  for msg in history:
    role = msg.get("role")
    content = msg.get("content", "")
    if role == "system" and SystemMessage:
      messages.append(SystemMessage(content=content))
    elif role == "assistant" and AIMessage:
      messages.append(AIMessage(content=content))
    elif role == "user" and HumanMessage:
      messages.append(HumanMessage(content=content))
  return messages


@dataclass
class LangGraphClient:
  guardrails: Any = None
  history: List[Dict[str, Any]] = field(default_factory=lambda: [
    {"role": "system", "content": SYSTEM_PROMPT.strip()}
  ])

  def __post_init__(self):
    _ensure_dependencies()
    llm_kwargs = {
      "azure_deployment": os.environ["AZURE_OPENAI_MODEL"],
      "azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
      "api_key": os.environ["AZURE_OPENAI_API_KEY"],
      "api_version": os.environ["OPENAI_API_VERSION"],
    }
    temperature_env = os.getenv("AZURE_OPENAI_TEMP")
    temperature = 1.0
    if temperature_env:
      try:
        requested_temp = float(temperature_env)
        if abs(requested_temp - 1.0) > 1e-9:
          logger.warning(
            "Model only supports temperature=1.0; overriding requested %.3f",
            requested_temp
          )
      except ValueError:
        logger.warning("Invalid AZURE_OPENAI_TEMP=%s; forcing temperature=1.0", temperature_env)
    llm_kwargs["temperature"] = temperature
    self._llm = AzureChatOpenAI(**llm_kwargs)
    self._graph = self._build_graph()
    self._app: CompiledGraph = self._graph.compile()
    self._tool_cache: Dict[str, Dict[str, Any]] = {}
    self.max_repeat_calls = int(os.getenv("MCP_MAX_REPEAT_CALLS", "2"))
    self.tool_cache_ttl = int(os.getenv("MCP_TOOL_CACHE_TTL", "900"))
    self.tool_cache_max_entries = int(os.getenv("MCP_TOOL_CACHE_MAX_ENTRIES", "64"))

  def _build_graph(self):
    graph = StateGraph(ChatState)

    async def guard_input_node(state: ChatState, config: Optional[Dict[str, Any]] = None):
      if self.guardrails:
        configurable = (config or {}).get("configurable", {})
        session_id = configurable.get("session_id", "unknown")
        user_id = configurable.get("user_id", "unknown")
        self.guardrails.guard_input(
          session_id=session_id,
          user_id=user_id,
          text=state.get("user_input", "")
        )
      return state

    async def llm_node(state: ChatState, config: Optional[Dict[str, Any]] = None):
      history = state.get("history") or self.history
      user_input = state.get("user_input", "")
      tools = state.get("tools", [])
      if not user_input:
        return state

      messages = _lc_messages_from_history(history)
      messages.append(HumanMessage(content=user_input))

      llm = self._llm
      if tools:
        llm = llm.bind(tools=tools, parallel_tool_calls=False)

      response = await llm.ainvoke(messages)
      assistant_content = response.content if isinstance(response.content, str) else "".join(
        chunk.get("text", "") for chunk in response.content  # type: ignore[arg-type]
      )
      tool_calls = response.additional_kwargs.get("tool_calls") or []
      logger.debug("LangGraph LLM tool_calls=%s", tool_calls)

      assistant_entry: Dict[str, Any] = {
        "role": "assistant",
        "content": assistant_content
      }
      if tool_calls:
        assistant_entry["tool_calls"] = tool_calls

      updated_history = history + [
        {"role": "user", "content": user_input},
        assistant_entry
      ]
      return {
        "history": updated_history,
        "raw_output": assistant_content,
        "tool_calls": tool_calls
      }

    async def tool_executor_node(state: ChatState, config: Optional[Dict[str, Any]] = None):
      tool_calls = state.get("tool_calls") or []
      if not tool_calls:
        return state

      updated_history = state.get("history") or self.history
      loop_count = int(state.get("loop_count") or 0) + 1
      configurable = (config or {}).get("configurable", {})
      session_id = configurable.get("session_id", "unknown")
      user_id = configurable.get("user_id", "unknown")

      self._prune_tool_cache()

      for call in tool_calls:
        function = call.get("function", {})
        tool_name = function.get("name")
        raw_arguments = function.get("arguments", "")
        tool_id = call.get("id", f"call-{loop_count}")

        if not tool_name:
          continue

        try:
          tool_args = _load_tool_arguments(raw_arguments)
          tool_args = _normalize_tool_arguments(tool_name, tool_args)
          tool_args = _populate_default_tool_arguments(tool_name, tool_args, updated_history)
        except Exception as exc:  # noqa: BLE001
          logger.error("Failed to parse tool args for %s: %s", tool_name, exc)
          updated_history.append({
            "role": "tool",
            "name": tool_name,
            "tool_call_id": tool_id,
            "content": f"Tool '{tool_name}' arguments could not be parsed."
          })
          continue

        missing_fields = _missing_required_fields(tool_name, tool_args)
        if missing_fields:
          guidance = (
            "TOOL_RETRY_REQUIRED\n"
            f"tool={tool_name}\n"
            f"missing_fields={', '.join(sorted(missing_fields))}\n"
            "action=Re-read the latest user request and retry the tool call."
          )
          updated_history.append({
            "role": "tool",
            "name": tool_name,
            "tool_call_id": tool_id,
            "content": guidance
          })
          continue

        if self.guardrails:
          self.guardrails.guard_tool_call(
            session_id=session_id,
            user_id=user_id,
            tool_name=tool_name,
            arguments=tool_args
          )

        signature = json.dumps({"tool": tool_name, "args": tool_args}, sort_keys=True)
        cache_entry = self._tool_cache.get(signature)
        if cache_entry:
          tool_response = deepcopy(cache_entry["response"])
          cache_entry["count"] = cache_entry.get("count", 1) + 1
          logger.info("LangGraph tool cache hit for %s (count=%s)", tool_name, cache_entry["count"])
        else:
          tool_response = await call_tool(tool_name, tool_args)
          self._tool_cache[signature] = {
            "response": deepcopy(tool_response),
            "count": 1,
            "timestamp": time.time()
          }
          logger.debug("LangGraph tool cache miss for %s signature=%s", tool_name, signature)

        if self.guardrails:
          self.guardrails.guard_tool_response(
            session_id=session_id,
            user_id=user_id,
            tool_name=tool_name,
            response=tool_response
          )

        content = _serialize_tool_content(tool_response)
        updated_history.append({
          "role": "tool",
          "name": tool_name,
          "tool_call_id": tool_id,
          "content": content
        })

        cache_entry = self._tool_cache.get(signature)
        if cache_entry and cache_entry.get("count", 1) >= self.max_repeat_calls:
          updated_history.append({
            "role": "system",
            "content": (
              f"You have already called `{tool_name}` with the same arguments multiple times. "
              "Use the cached data to compose the final response."
            )
          })

      return {
        "history": updated_history,
        "tool_calls": [],
        "loop_count": loop_count
      }

    async def formatter_node(state: ChatState, config: Optional[Dict[str, Any]] = None):
      markdown, next_questions = _parse_final_message(state.get("raw_output", ""))
      return {
        "final_content": markdown,
        "next_questions": next_questions,
        "history": state.get("history", self.history)
      }

    graph.add_node("guard_input", guard_input_node)
    graph.add_node("llm", llm_node)
    graph.add_node("tool_executor", tool_executor_node)
    graph.add_node("formatter", formatter_node)

    graph.add_edge(START, "guard_input")
    graph.add_edge("guard_input", "llm")

    def _tool_decision(state: ChatState):
      return "tools" if state.get("tool_calls") else "final"

    graph.add_conditional_edges(
      "llm",
      _tool_decision,
      {
        "tools": "tool_executor",
        "final": "formatter"
      }
    )

    graph.add_edge("tool_executor", "llm")
    graph.add_edge("formatter", END)

    return graph

  async def run(self, query: str, *, session_id: str, user_id: str, tools: Optional[List[Dict[str, Any]]] = None):
    state: ChatState = {
      "history": self.history,
      "user_input": query,
      "tools": tools or []
    }
    result: ChatState = await self._app.ainvoke(
      state,
      config={"configurable": {"session_id": session_id, "user_id": user_id}}
    )
    self.history = result.get("history", self.history)
    return result.get("final_content", ""), result.get("next_questions", [])

  async def stream_response(
    self,
    query: str,
    *,
    session_id: str,
    user_id: str,
    tools: Optional[List[Dict[str, Any]]] = None,
    chunk_size: Optional[int] = None
  ) -> AsyncGenerator[str, None]:
    chunk_len = chunk_size or int(os.getenv("LANGGRAPH_STREAM_CHUNK_SIZE", "120"))
    final_content, next_questions = await self.run(
      query,
      session_id=session_id,
      user_id=user_id,
      tools=tools
    )

    if final_content:
      for idx in range(0, len(final_content), max(1, chunk_len)):
        yield final_content[idx: idx + chunk_len]

    yield json.dumps({
      "type": "final",
      "content": final_content,
      "next_questions": next_questions
    })

  def _prune_tool_cache(self) -> None:
    if not self._tool_cache:
      return

    now = time.time()
    ttl = max(1, self.tool_cache_ttl)
    evicted = 0

    for signature in list(self._tool_cache.keys()):
      entry = self._tool_cache.get(signature) or {}
      ts = float(entry.get("timestamp", now))
      if now - ts > ttl:
        self._tool_cache.pop(signature, None)
        evicted += 1

    if len(self._tool_cache) > self.tool_cache_max_entries:
      sorted_items = sorted(
        self._tool_cache.items(),
        key=lambda item: item[1].get("timestamp", now)
      )
      overflow = len(self._tool_cache) - self.tool_cache_max_entries
      for signature, _ in sorted_items[:overflow]:
        self._tool_cache.pop(signature, None)
        evicted += 1

    if evicted:
      logger.debug("LangGraph tool cache pruned %s entries (remaining=%s)", evicted, len(self._tool_cache))
