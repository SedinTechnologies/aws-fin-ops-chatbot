import os, json, logging
from typing import AsyncGenerator, List, Tuple
from mcp_tool_helper import call_tool
from openai import AsyncAzureOpenAI
from response_utils import parse_structured_response
from tool_utils import (
    _load_tool_arguments,
    _missing_required_fields,
    _normalize_tool_arguments,
    _populate_default_tool_arguments,
    _serialize_tool_content
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an advanced AWS FinOps Assistant. Your goal is to provide **sharp, crisp, and actionable** insights.

### 🛠️ AVAILABLE TOOLS
- **Cost Explorer**: Historical cost & usage.
- **Billing**: Invoices, budgets, savings plans.
- **Pricing**: Service pricing & comparisons.
- **CloudWatch**: Metrics, logs, alarms.
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
    - **Tables**: Use for data comparison.

---

### 🔮 NEXT STEPS (JSON)
At the very end, provide 3 follow-up questions in a JSON array inside a `json_suggestions` code block.
Format: `[{"question": "...", "label": "...", "description": "...", "icon": "..."}]`
"""

class AzureOpenAIClient:
    def __init__(self, guardrails=None) -> None:
        self.deployment_name = os.environ["AZURE_OPENAI_MODEL"]
        self.guardrails = guardrails

        self.client = AsyncAzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ["OPENAI_API_VERSION"]
        )

        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.title = None

        # pending tool calls per stream
        self._pending_tool_calls = {}
        self._tool_call_in_progress = False
        self._ephemeral_messages: list[dict] = []

    def _purge_ephemeral_messages(self) -> None:
        if not self._ephemeral_messages:
            return
        self.messages = [
            msg for msg in self.messages
            if msg not in self._ephemeral_messages
        ]
        self._ephemeral_messages.clear()

    # ----------------------------------------
    # TOOL CALL ASSEMBLY (STREAMING)
    # ----------------------------------------
    def _handle_tool_call_delta(self, deltas, *, choice_index: int | None):
        self._tool_call_in_progress = True
        base_index = choice_index if choice_index is not None else 0

        for idx, delta in enumerate(deltas):
            tool_index = getattr(delta, "index", idx)
            key = (base_index, tool_index)

            entry = self._pending_tool_calls.setdefault(key, {
                "id": getattr(delta, "id", None),
                "name": "",
                "arguments": ""
            })

            function = getattr(delta, "function", None)
            if function:
                if getattr(function, "name", None):
                    entry["name"] = function.name
                if getattr(function, "arguments", None):
                    entry["arguments"] += function.arguments or ""

            if getattr(delta, "id", None):
                entry["id"] = delta.id

    # ----------------------------------------
    # EXECUTE ALL PENDING TOOL CALLS
    # ----------------------------------------
    async def _flush_tool_calls(self, *, session_id, user_id):
        pending_keys = list(self._pending_tool_calls.keys())

        for key in pending_keys:
            payload = self._pending_tool_calls[key]
            if not payload.get("name"):  # skip bad entries
                continue

            await self._consume_tool_call(
                tool_id=payload.get("id") or f"call-{key[0]}-{key[1]}",
                tool_name=payload["name"],
                raw_arguments=payload.get("arguments", ""),
                session_id=session_id,
                user_id=user_id
            )

            del self._pending_tool_calls[key]

        self._tool_call_in_progress = False

    # ----------------------------------------
    # EXECUTE A SINGLE TOOL CALL
    # ----------------------------------------
    async def _consume_tool_call(self, *, tool_id, tool_name, raw_arguments, session_id, user_id):
        # Parse arguments -------------------------------------------------------
        try:
            tool_args = _load_tool_arguments(raw_arguments)
            tool_args = _normalize_tool_arguments(tool_name, tool_args)
            tool_args = _populate_default_tool_arguments(tool_name, tool_args, self.messages)
        except json.JSONDecodeError:
            raw_preview = (raw_arguments or "")[:500]
            logger.error(
                "Invalid tool arguments for %s (id=%s). Raw payload (truncated): %s",
                tool_name, tool_id, raw_preview,
            )
            if self.guardrails:
                self.guardrails.audit_event(
                    "tool_argument_parse_error",
                    session_id, user_id,
                    {"tool": tool_name, "tool_call_id": tool_id, "raw_preview": raw_preview},
                )

            warning = (
                f"Tool '{tool_name}' arguments could not be parsed. "
                "Please retry with valid JSON."
            )

            self.messages.append({
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tool_id,
                        "function": {"name": tool_name, "arguments": json.dumps({})},
                        "type": "function"
                    }
                ]
            })
            self.messages.append({
                "role": "tool",
                "name": tool_name,
                "tool_call_id": tool_id,
                "content": warning
            })
            return

        # Guardrails -----------------------------------------------------------
        if self.guardrails:
            self.guardrails.guard_tool_call(
                session_id=session_id,
                user_id=user_id,
                tool_name=tool_name,
                arguments=tool_args
            )

        # Required field validation --------------------------------------------
        missing_fields = _missing_required_fields(tool_name, tool_args)
        if missing_fields:
            warning = (
                f"Tool '{tool_name}' called without required fields: "
                f"{', '.join(sorted(missing_fields))}. Please retry with complete arguments."
            )
            logger.warning(
                "%s | raw=%s",
                warning,
                json.dumps({"tool_args": tool_args, "raw_arguments": raw_arguments[-200:]})
            )

            # Send tool placeholder + tool response
            self.messages.append({
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tool_id,
                        "function": {"name": tool_name, "arguments": json.dumps(tool_args)},
                        "type": "function"
                    }
                ]
            })
            self.messages.append({
                "role": "tool",
                "name": tool_name,
                "tool_call_id": tool_id,
                "content": warning
            })
            return

        # Register tool call in message history
        self.messages.append({
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tool_id,
                    "function": {"name": tool_name, "arguments": json.dumps(tool_args)},
                    "type": "function"
                }
            ]
        })

        # Execute MCP tool (without cache) -------------------------------------
        tool_resp = await call_tool(tool_name, tool_args)

        serialized = _serialize_tool_content(tool_resp)
        self.messages.append({
            "role": "tool",
            "name": tool_name,
            "tool_call_id": tool_id,
            "content": serialized
        })

    # ----------------------------------------
    # FINAL OUTPUT PARSING (<title>::<md>::<json>)
    # ----------------------------------------
    def _parse_final_message(self, content: str) -> Tuple[str, List[dict]]:
        try:
            title, markdown, next_questions = parse_structured_response(content)
            if not self.title and title:
                self.title = title
            return markdown, next_questions
        except Exception:
            logger.warning("Failed to parse streaming payload, returning raw content")
            return content, []

    # ----------------------------------------
    # MAIN STREAMING LOOP
    # ----------------------------------------
    async def stream_response(
        self,
        query,
        tools,
        *,
        session_id="unknown",
        user_id="unknown"
    ) -> AsyncGenerator[str, None]:

        logger.info(f"Streaming query to Azure OpenAI: {query}")

        self._purge_ephemeral_messages()

        self.messages.append({"role": "user", "content": query})

        # audit
        if self.guardrails:
            self.guardrails.audit_event(
                "client_prompt_enqueued",
                session_id=session_id,
                user_id=user_id,
                payload={"query": query[:200]}
            )

        while True:
            # Prepare request --------------------------------------------------
            request_kwargs = {
                "model": self.deployment_name,
                "messages": self.messages,
                "stream": True,
            }
            if tools:
                request_kwargs["tools"] = tools
                request_kwargs["parallel_tool_calls"] = False

            stream = await self.client.chat.completions.create(**request_kwargs)

            # Reset accumulators
            self._pending_tool_calls.clear()
            self._tool_call_in_progress = False
            collected = []

            async for event in stream:
                if not getattr(event, "choices", None):
                    continue

                choice = event.choices[0]
                msg = choice.delta
                finish_reason = choice.finish_reason

                # TOOL CALL STREAMING DELTAS ---------------------------------
                if msg and msg.tool_calls:
                    self._handle_tool_call_delta(
                        msg.tool_calls,
                        choice_index=getattr(choice, "index", None)
                    )

                    if finish_reason == "tool_calls":
                        await self._flush_tool_calls(
                            session_id=session_id,
                            user_id=user_id
                        )
                        break
                    continue

                # NORMAL TEXT STREAMING -------------------------------------
                token = (msg.content if msg else "") or ""
                if token:
                    collected.append(token)
                    yield token

                # END OF COMPLETION -----------------------------------------
                if finish_reason == "stop":
                    if self._tool_call_in_progress and self._pending_tool_calls:
                        await self._flush_tool_calls(
                            session_id=session_id,
                            user_id=user_id
                        )
                        break

                    final_text = "".join(collected)
                    markdown, next_questions = self._parse_final_message(final_text)

                    # Clean prior tool-call messages
                    self.messages = [
                        m for m in self.messages
                        if not (m.get("tool_calls") or m["role"] == "tool")
                    ]
                    self._purge_ephemeral_messages()

                    self.messages.append({"role": "assistant", "content": markdown})

                    yield json.dumps({
                        "type": "final",
                        "content": markdown,
                        "next_questions": next_questions
                    })
                    return

            # SECOND-PASS (AFTER TOOL CALL)
            if self._tool_call_in_progress:
                logger.info("Tool call detected mid-stream, reissuing completion without streaming")

                response = await self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=self.messages,
                    tools=tools,
                    stream=False,
                    parallel_tool_calls=False
                )

                msg = response.choices[0].message

                if msg.tool_calls:
                    self._handle_tool_call_delta(msg.tool_calls, choice_index=None)
                    await self._flush_tool_calls(
                        session_id=session_id,
                        user_id=user_id
                    )
                    continue

                if msg.content:
                    final_json = json.loads(msg.content)

                    self.messages = [
                        m for m in self.messages
                        if not (m.get("tool_calls") or m["role"] == "tool")
                    ]
                    self._purge_ephemeral_messages()

                    self.messages.append({"role": "assistant", "content": final_json["content"]})

                    yield json.dumps({
                        "type": "final",
                        "content": final_json["content"],
                        "next_questions": final_json["next_questions"]
                    })
                    return

    # ----------------------------------------
    # SIMPLE (NON-STREAM) WRAPPER
    # ----------------------------------------
    async def generate_response(
        self,
        query,
        tools,
        *,
        session_id="unknown",
        user_id="unknown"
    ):
        chunks = []
        async for chunk in self.stream_response(query, tools, session_id=session_id, user_id=user_id):
            chunks.append(chunk)

        final_payload = json.loads(chunks[-1]) if chunks else {"content": "", "next_questions": []}
        return final_payload.get("content", ""), final_payload.get("next_questions", [])
