import os
from langchain_ollama import ChatOllama
from langgraph_base_client import BaseLangGraphClient

class OllamaLangGraphClient(BaseLangGraphClient):
  def _init_llm(self) -> ChatOllama:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b")

    # Optional performance/tuning settings for Ollama
    # Strict sampling for reliable tool-call JSON output.
    top_p = float(os.environ.get("OLLAMA_TOP_P", "0.9"))
    temperature = float(os.environ.get("OLLAMA_TEMPERATURE", "0.1"))
    top_k = int(os.environ.get("OLLAMA_TOP_K", "40"))
    num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", "32768"))
    num_predict = int(os.environ.get("OLLAMA_NUM_PREDICT", "8192"))

    llm_kwargs = {
      "base_url": base_url,
      "model": model,
      "temperature": temperature,
      "top_p": top_p,
      "top_k": top_k,
      "num_ctx": num_ctx,
      "num_predict": num_predict
    }

    # Controls reasoning/thinking mode via ChatOllama's `reasoning` param,
    # which maps to Ollama API's `think` option.
    # Defaults to False — thinking tokens interfere with reliable tool-call JSON.
    # Set OLLAMA_REASONING=true to enable for thinking-capable models.
    # Note: the old `extra_body` approach was silently ignored by ChatOllama.
    reasoning = os.environ.get("OLLAMA_REASONING", "false").lower() == "true"
    llm_kwargs["reasoning"] = reasoning

    return ChatOllama(**llm_kwargs)
