# LangGraph Tooling — Step 3 Plan

This note captures the design for extending the LangGraph prototype to support MCP-based tool calls with guardrails. The goal for Step 3 is to achieve parity with the existing AzureOpenAIClient tool flow while keeping the LangGraph structure readable.

## Proposed Node Additions

1. **ToolDecisionNode**
   - Examines the LLM output to determine whether tool calls were requested.
   - Handles caching by checking `(tool_name, args)` signature before reissuing MCP calls.

2. **MCPInvokeNode**
   - Reuses `call_tool` from `mcp_tool_helper.py` to keep existing guardrail logic.
   - Transforms MCP `TextContent/ImageContent` into LangGraph-friendly text for the LLM follow-up.

3. **ToolResultNode**
   - Appends tool responses to the conversation state and feeds them back into the LLM node for a second pass.
   - Applies the repeat-limit reminders (similar to current `_consume_tool_call`).

4. **Guardrail nodes**
   - Wrap tool call and tool response in nodes that call `guardrails.guard_tool_call` and `guardrails.guard_tool_response` respectively.

## Graph Flow Sketch

```
START → GuardInput → LLM → ToolDecision →
  ├─(no tool)→ Formatter → END
  └─(tool needed)→ GuardToolCall → MCPInvoke → GuardToolResponse → ToolResult → LLM (loop)
```

## Implementation Tasks

1. Expand `ChatState` to track pending tool calls, cache map, and repeat counts.
2. Create serialization helpers (reuse `_serialize_tool_content`) for tool responses reinserted into history.
3. Bridge LangGraph streaming to Chainlit: either accumulate final text or emit intermediate tokens.
4. Update `docs/langgraph_migration.md` to reflect the new node structure once implemented.

