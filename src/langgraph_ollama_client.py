import os
from langchain_ollama import ChatOllama
from langgraph_base_client import BaseLangGraphClient

class OllamaLangGraphClient(BaseLangGraphClient):
  def _init_llm(self) -> ChatOllama:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b")
    
    # Optional performance/tuning settings for Ollama compatible with defaults
    top_p = float(os.environ.get("OLLAMA_TOP_P", "0.95"))
    presence_penalty = float(os.environ.get("OLLAMA_PRESENCE_PENALTY", "1.5"))
    temperature = float(os.environ.get("OLLAMA_TEMPERATURE", "1.0"))
    top_k = int(os.environ.get("OLLAMA_TOP_K", "20"))
    
    llm_kwargs = {
      "base_url": base_url,
      "model": model,
      "temperature": temperature,
      "top_p": top_p,
      "top_k": top_k,
      "presence_penalty": presence_penalty
    }
    return ChatOllama(**llm_kwargs)
