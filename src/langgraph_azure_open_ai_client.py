import os
from langchain_openai import AzureChatOpenAI
from langgraph_base_client import BaseLangGraphClient

class AzureLangGraphClient(BaseLangGraphClient):
  def _init_llm(self) -> AzureChatOpenAI:
    llm_kwargs = {
      "azure_deployment": os.environ["AZURE_OPENAI_MODEL"],
      "azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
      "api_key": os.environ["AZURE_OPENAI_API_KEY"],
      "api_version": os.environ["OPENAI_API_VERSION"],
      "streaming": True
    }
    return AzureChatOpenAI(**llm_kwargs)
