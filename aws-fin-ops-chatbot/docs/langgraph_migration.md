# LangGraph Migration Plan — Phase 1

This document captures the assessment from Step 1 of the LangGraph migration effort: how the current Chainlit + AzureOpenAIClient stack works today, and how we can map each responsibility into LangGraph nodes.

## Current Flow Overview

1. **Chainlit session lifecycle** (`src/app.py`)
   - Handles auth, MCP connection bootstrap, memory restoration, and message streaming via `AzureOpenAIClient`.
2. **AzureOpenAIClient** (`src/azure_openai_client.py`)
   - Maintains message history, streams responses, parses tool deltas, and injects guardrail audits.
3. **Tool execution** (`src/mcp_tool_helper.py`)
   - Looks up the right MCP session, calls the tool, marshals text/image payloads, and re-applies guardrails.
4. **Guardrails** (`src/guardrails.py`)
   - Enforces policies on user input, tool calls, tool responses, and final outputs.

## Target LangGraph Mapping

| Current Responsibility | Proposed LangGraph Node / Component |
| --- | --- |
| User input guard + memory trim | `InputGuardNode` → enforces GuardrailEngine before graph run |
| Prompt + system rules assembly | `ComposePromptNode` → merges system prompt, trimmed history, and latest user turn |
| LLM generation (with tool-use decision) | `LLMNode` using Azure OpenAI endpoint registered as LangGraph LLM |
| Tool-call delta handling | `ToolRouterNode` → inspects LLM tool calls and decides whether to invoke MCP |
| MCP invocation | `MCPCallNode` → wraps existing `call_tool` helper or new LangGraph Tool definition |
| Guard tool response + fan-in | `ToolGuardNode` followed by `ResponseAggregatorNode` |
| Final formatting (title/next questions) | `FormatterNode` that enforces `<title>::<md>::<json>` schema |
| Output streaming to Chainlit | Custom LangGraph runner hook or Chainlit callback that relays node emissions |

## Key Integration Questions

1. **State store**: Do we keep Chainlit’s session memory or move to LangGraph’s checkpointer? (Recommended: start with in-memory checkpointer per Chainlit session.)
2. **Guardrails**: Keep existing `GuardrailEngine` and call it inside nodes; no need to reimplement rules in LangGraph.
3. **MCP reuse**: Continue to manage MCP connections in `cl.user_session` and let nodes call the helper so we don’t reconnect per turn.
4. **Streaming**: LangGraph’s event stream must be bridged to Chainlit’s `Message.stream_token`; prototype should show minimal streaming path before full replacement.

## Next Steps

1. Prototype a minimal LangGraph workflow (user → guard → LLM → formatter) inside a new module (`src/langgraph_app.py`) while keeping the existing client in place.
2. Once basic messaging works, extend the graph with tool-call nodes and replace the direct `AzureOpenAIClient` usage in `on_message`.
3. Gradually retire the old client after parity tests (happy-path RDS/S3 cost queries + guardrail violations).
