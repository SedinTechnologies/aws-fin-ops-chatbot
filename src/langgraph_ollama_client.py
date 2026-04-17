import os
from langchain_ollama import ChatOllama
from langgraph_base_client import BaseLangGraphClient

class OllamaLangGraphClient(BaseLangGraphClient):
  def _init_llm(self) -> ChatOllama:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b")

    return ChatOllama(
      base_url=base_url,
      model=model,
      temperature=float(os.environ.get("OLLAMA_TEMPERATURE", "0.1")),
      top_p=float(os.environ.get("OLLAMA_TOP_P", "0.9")),
      top_k=int(os.environ.get("OLLAMA_TOP_K", "40")),
      num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "32768")),
      num_predict=int(os.environ.get("OLLAMA_NUM_PREDICT", "8192")),
      repeat_penalty=float(os.environ.get("OLLAMA_REPEAT_PENALTY", "1.3")),
      repeat_last_n=int(os.environ.get("OLLAMA_REPEAT_LAST_N", "256")),
      # Disable thinking to ensure reliable tool-call JSON output.
      # Set OLLAMA_REASONING=true for thinking-capable models.
      reasoning=os.environ.get("OLLAMA_REASONING", "false").lower() == "true",
    )
