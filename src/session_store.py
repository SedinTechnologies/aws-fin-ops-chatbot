import json
from abc import ABC, abstractmethod

class SessionStore(ABC):
  @abstractmethod
  def get_user(self, email: str) -> dict | None:
    pass

  @abstractmethod
  def create_user(self, email: str, user_data: dict):
    pass

  @abstractmethod
  def save_chats(self, email: str, chats: list):
    pass

  @abstractmethod
  def load_chats(self, email: str) -> list:
    pass


class RedisSessionStore(SessionStore):
  def __init__(self, redis_client):
    self.rc = redis_client

  def _user_key(self, email):
    return f"user:{email}"

  def _chats_key(self, email):
    return f"chats:{email}"

  def _mcp_key(self, email, mcp_name):
    return f"mcp:{email}:{mcp_name}"

  def get_user(self, email: str):
    val = self.rc.get(self._user_key(email))
    return json.loads(val) if val else None

  def create_user(self, email: str, user_data: dict):
    self.rc.set(self._user_key(email), json.dumps(user_data))

  def save_chats(self, email: str, chats: list):
    self.rc.set(self._chats_key(email), json.dumps(chats))

  def load_chats(self, email: str) -> list:
    val = self.rc.get(self._chats_key(email))
    return json.loads(val) if val else []

  def save_mcp_connection(self, email: str, mcp_name: str, conn_data: dict):
    # Store MCP connection details as JSON string in Redis
    self.rc.set(self._mcp_key(email, mcp_name), json.dumps(conn_data))

  def load_mcp_connection(self, email: str, mcp_name: str):
    val = self.rc.get(self._mcp_key(email, mcp_name))
    return json.loads(val) if val else None

  def delete_mcp_connection(self, email: str, mcp_name: str):
    self.rc.delete(self._mcp_key(email, mcp_name))
