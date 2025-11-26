"""LangGraph-based conversation client with MCP tooling support."""

from __future__ import annotations

import json
import logging
import os
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
  from langgraph.errors import GraphRecursionError
except ImportError:  # pragma: no cover - handled at runtime
  START = END = StateGraph = None  # type: ignore[assignment]
  CompiledGraph = None  # type: ignore[assignment]
  AzureChatOpenAI = None  # type: ignore[assignment]
  HumanMessage = AIMessage = SystemMessage = None  # type: ignore[assignment]
  GraphRecursionError = RecursionError  # type: ignore[assignment]


logger = logging.getLogger(__name__)


def _int_from_env(name: str, default: int, *, min_value: int = 1) -> int:
  raw_value = os.getenv(name)
  if raw_value is None:
    return default
  try:
    parsed = int(raw_value)
  except ValueError:
    logger.warning("Invalid %s=%s; falling back to %s", name, raw_value, default)
    return default
  clamped = max(min_value, parsed)
  if clamped != parsed:
    logger.warning("Clamping %s from %s to %s", name, parsed, clamped)
  return clamped


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
  tool_cap_reached: bool
  tool_attempts: Dict[str, int]


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


def _tool_signature(tool_name: str, tool_args: dict | None) -> str:
  serialized_args = json.dumps(tool_args or {}, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
  return f"{tool_name}:{serialized_args}"


@dataclass
class LangGraphClient:
  guardrails: Any = None
  history: List[Dict[str, Any]] = field(default_factory=lambda: [
    {"role": "system", "content": SYSTEM_PROMPT.strip()}
  ])
  recursion_limit: int = field(init=False)
  max_tool_loops: int = field(init=False)
  tool_retry_limit: int = field(init=False)

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
    self.recursion_limit = _int_from_env("LANGGRAPH_RECURSION_LIMIT", 40, min_value=5)
    self.max_tool_loops = _int_from_env("LANGGRAPH_MAX_TOOL_LOOPS", 6, min_value=1)
    self.tool_retry_limit = _int_from_env("LANGGRAPH_TOOL_RETRY_LIMIT", 3, min_value=1)

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
      tool_cap_reached = bool(state.get("tool_cap_reached"))
      if not user_input:
        return state

      messages = _lc_messages_from_history(history)
      messages.append(HumanMessage(content=user_input))

      llm = self._llm
      if tools and not tool_cap_reached:
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

      filtered_calls: List[Dict[str, Any]] = []
      for call in tool_calls:
        function = call.get("function") or {}
        tool_name = function.get("name")
        raw_arguments = function.get("arguments", "")
        if not tool_name:
          filtered_calls.append(call)
          continue
        try:
          tool_args = _load_tool_arguments(raw_arguments)
          tool_args = _normalize_tool_arguments(tool_name, tool_args)
          tool_args = _populate_default_tool_arguments(tool_name, tool_args, updated_history)
        except Exception:
          filtered_calls.append(call)
          continue

        filtered_call = dict(call)
        filtered_call["_normalized_args"] = tool_args
        filtered_calls.append(filtered_call)

      tool_calls = filtered_calls

      return {
        "history": updated_history,
        "raw_output": assistant_content,
        "tool_calls": tool_calls,
        "tool_cap_reached": tool_cap_reached,
        "tools": tools if not tool_cap_reached else [],
        "tool_attempts": state.get("tool_attempts") or {}
      }

    async def tool_executor_node(state: ChatState, config: Optional[Dict[str, Any]] = None):
      tool_calls = state.get("tool_calls") or []
      if not tool_calls:
        return state

      updated_history = state.get("history") or self.history
      loop_count = int(state.get("loop_count") or 0) + 1
      prior_tool_cap = bool(state.get("tool_cap_reached"))
      loop_cap_triggered_now = (loop_count >= self.max_tool_loops) and not prior_tool_cap
      tool_attempts: Dict[str, int] = dict(state.get("tool_attempts") or {})
      configurable = (config or {}).get("configurable", {})
      session_id = configurable.get("session_id", "unknown")
      user_id = configurable.get("user_id", "unknown")

      for call in tool_calls:
        function = call.get("function", {})
        tool_name = function.get("name")
        raw_arguments = function.get("arguments", "")
        tool_id = call.get("id", f"call-{loop_count}")

        if not tool_name:
          continue

        tool_args = call.get("_normalized_args")
        try:
          if tool_args is None:
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

        signature = _tool_signature(tool_name, tool_args)
        prior_attempts = tool_attempts.get(signature, 0)
        if prior_attempts >= self.tool_retry_limit:
          updated_history.append({
            "role": "system",
            "content": (
              f"Skipping tool '{tool_name}' because it has already been called "
              f"{prior_attempts} times with identical arguments. "
              "Summarize available information or adjust your plan instead of retrying."
            ),
            "ephemeral": True
          })
          tool_cap_reached = True
          continue

        tool_attempts[signature] = prior_attempts + 1

        if self.guardrails:
          self.guardrails.guard_tool_call(
            session_id=session_id,
            user_id=user_id,
            tool_name=tool_name,
            arguments=tool_args
          )

        tool_response = await call_tool(tool_name, tool_args)

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

      if loop_cap_triggered_now:
        updated_history.append({
          "role": "system",
          "content": (
            f"Tool loop limit of {self.max_tool_loops} iterations reached. "
            "Summarize the available data without issuing more tool calls."
          ),
          "ephemeral": True
        })

      tool_cap_reached = prior_tool_cap or loop_cap_triggered_now

      return {
        "history": updated_history,
        "tool_calls": [],
        "loop_count": loop_count,
        "tool_cap_reached": tool_cap_reached,
        "tool_attempts": tool_attempts,
        "tools": [] if tool_cap_reached else (state.get("tools") or [])
      }

    async def formatter_node(state: ChatState, config: Optional[Dict[str, Any]] = None):
      markdown, next_questions = _parse_final_message(state.get("raw_output", ""))
      return {
        "final_content": markdown,
        "next_questions": next_questions,
        "history": state.get("history", self.history),
        "tool_attempts": state.get("tool_attempts") or {}
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
      "tools": tools or [],
      "loop_count": 0,
      "tool_cap_reached": False,
      "tool_attempts": {}
    }
    config = {
      "configurable": {"session_id": session_id, "user_id": user_id},
      "recursion_limit": self.recursion_limit
    }
    try:
      result: ChatState = await self._app.ainvoke(state, config=config)
    except GraphRecursionError as exc:
      logger.warning(
        "LangGraph recursion limit (%s) reached for session=%s user=%s: %s",
        self.recursion_limit,
        session_id,
        user_id,
        exc
      )
      safety_msg = (
        "I attempted to orchestrate several AWS tool calls but hit the automated safety limit. "
        "Please narrow the question or try again with a smaller time range."
      )
      self.history = state.get("history", self.history)
      return safety_msg, []
    history = result.get("history", self.history)
    filtered_history: List[Dict[str, Any]] = []
    for entry in history:
      if entry.get("ephemeral"):
        continue
      if "ephemeral" in entry:
        entry = dict(entry)
        entry.pop("ephemeral", None)
      filtered_history.append(entry)
    self.history = filtered_history or self.history
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
