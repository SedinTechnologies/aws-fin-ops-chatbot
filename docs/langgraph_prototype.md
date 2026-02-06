# LangGraph Prototype — Step 2 Outline

Goal: bootstrap a minimal LangGraph workflow that replicates the existing “user → LLM → formatted response” path while still invoked from Chainlit. This prototype intentionally skips MCP/tool logic so we can verify LangGraph wiring, context storage, and streaming.

## Components to Implement

1. **State dataclass**
   - Holds conversation history and latest user input.
   - Provides helper to append assistant output back into history.

2. **Nodes**
   - `GuardInputNode`: wraps existing `GuardrailEngine.guard_input` to validate text before the graph proceeds.
   - `LLMNode`: LangGraph `Runnable` that calls Azure OpenAI chat completion (non-tool mode) using the same system prompt.
   - `FormatterNode`: parses `<title>::<md>::<json>` segments, mirrors `_parse_final_message` logic.

3. **Graph wiring**
   - Use `StateGraph` with edges: `input -> guard -> llm -> formatter -> output`.
   - Register in `src/langgraph_app.py` with a `build_graph()` helper returning the compiled graph.

4. **Chainlit integration shim**
   - Add a feature flag (env var `ENABLE_LANGGRAPH`) in `src/app.py` to decide between the legacy `AzureOpenAIClient` and the new graph runner.
   - For the prototype, the graph runner simply executes `graph.invoke({"user_input": message.content, ...})` and streams the formatted text back via `cl.Message`.

## Open Questions / TODOs

- Decide on state persistence: initial prototype can keep history in Chainlit session dict; later we might use LangGraph checkpointer.
- Streaming: LangGraph can emit events, but for v0 we can run synchronously and send the final message.
- Dependency management: need to add `langgraph` and `langchain-openai` (or similar) to `pyproject.toml` once we start coding the nodes.

## Next Implementation Steps

1. Add dependencies (langgraph, langchain-core, langchain-openai) to the project environment.
2. Create `src/langgraph_app.py` with state + node scaffolding.
3. Add a minimal integration path in `src/app.py` behind a toggle so we can test without disrupting existing flow.

