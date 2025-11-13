import json, time
from abc import ABC, abstractmethod

class SessionStore(ABC):
  @abstractmethod
  def get_user(self, email: str) -> dict | None:
    pass

  @abstractmethod
  def create_user(self, email: str, user_data: dict):
    pass

  @abstractmethod
  def store_session(self, email: str, session_id: str):
    pass

  @abstractmethod
  def retrieve_sessions(self, email: str) -> list:
    pass

  @abstractmethod
  def save_chats(self, email: str, session_id: str, chats: list):
    pass

  @abstractmethod
  def load_chats(self, email: str, session_id: str) -> list:
    pass


class RedisSessionStore(SessionStore):
  def __init__(self, redis_client):
    self.rc = redis_client

  def _user_key(self, email):
    return f"user:{email}"

  def _sessions_key(self, email):
    return f"sessions:{email}"

  def _chats_key(self, email, session_id):
    return f"chats:{email}:{session_id}"

  def _mcp_key(self, email, mcp_name):
    return f"mcp:{email}:{mcp_name}"

  def get_user(self, email: str):
    val = self.rc.get(self._user_key(email))
    return json.loads(val) if val else None

  def create_user(self, email: str, user_data: dict):
    self.rc.set(self._user_key(email), json.dumps(user_data))

  def store_session(self, email: str, session_id: str, title: str):
    self.rc.sadd(self._sessions_key(email), json.dumps({
      "session_id": session_id,
      "title": title,
      "timestamp": int(time.time())
    }))

  def retrieve_sessions(self, email: str) -> list:
    sessions = self.rc.smembers(self._sessions_key(email))
    return [json.loads(s) for s in sessions] if sessions else []

  def save_chats(self, email: str, session_id: str, chats: list):
    # store only user & assistant messages to reduce storage
    chats_required = []
    for chat in chats:
      for msg in chat["messages"]:
        if msg.get("role") not in ["user", "assistant"] or msg.get("content", None) is None:
          continue
        chats_required.append(msg)
    self.rc.set(self._chats_key(email, session_id), json.dumps(chats_required))

  def load_chats(self, email: str, session_id: str) -> list:
    val = self.rc.get(self._chats_key(email, session_id))
    return json.loads(val) if val else []

  def save_mcp_connection(self, email: str, mcp_name: str, conn_data: dict):
    # Store MCP connection details as JSON string in Redis
    self.rc.set(self._mcp_key(email, mcp_name), json.dumps(conn_data))

  def load_mcp_connection(self, email: str, mcp_name: str):
    val = self.rc.get(self._mcp_key(email, mcp_name))
    return json.loads(val) if val else None

  def delete_mcp_connection(self, email: str, mcp_name: str):
    self.rc.delete(self._mcp_key(email, mcp_name))
