import os
from typing import List
from langchain_core.tools import BaseTool

from langgraph_azure_open_ai_client import AzureLangGraphClient
from langgraph_ollama_client import OllamaLangGraphClient

def LangGraphClient(tools: List[BaseTool]):
  """
  Factory method to return the appropriate LangGraph client implementation
  based on the targeted AI Provider.

  Defaults to Azure OpenAI.
  """
  provider = os.getenv("AI_PROVIDER", "AZURE_OPEN_AI")

  if provider == "OLLAMA":
    return OllamaLangGraphClient(tools=tools)
  else:
    return AzureLangGraphClient(tools=tools)
