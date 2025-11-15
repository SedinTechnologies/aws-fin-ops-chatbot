import json, time
from abc import ABC, abstractmethod

class SessionStore(ABC):
  @abstractmethod
  def get_user(self, user_id: str) -> dict | None:
    pass

  @abstractmethod
  def create_user(self, id: str, user_data: dict):
    pass

  @abstractmethod
  def retrieve_all_chats(self, user_id: str) -> list:
    pass

  @abstractmethod
  def save_chat_info(self, user_id: str, chat_id: str, title: str) -> list:
    pass

  @abstractmethod
  def fetch_full_chat_messages(self, user_id: str, chat_id: str):
    pass

  @abstractmethod
  def save_chat_messages(self, user_id: str, chat_id: str, chat_messages: list):
    pass

class RedisSessionStore(SessionStore):
  def __init__(self, redis_client):
    self.rc = redis_client

  def _user_key(self, user_id):
    return f"user:{user_id}"

  def _all_chats_key(self, user_id):
    return f"chats:{user_id}"

  def _chat_key(self, user_id, chat_id):
    return f"chat:{user_id}:{chat_id}"

  def get_user(self, user_id: str):
    val = self.rc.get(self._user_key(user_id))
    return json.loads(val) if val else None

  def create_user(self, user_id: str, user_data: dict):
    self.rc.set(self._user_key(user_id), json.dumps(user_data))

  def retrieve_all_chats(self, user_id: str) -> list:
    chats = self.rc.smembers(self._all_chats_key(user_id))
    return [json.loads(c) for c in chats] if chats else []

  def save_chat_info(self, user_id: str, chat_id: str, title: str):
    self.rc.sadd(self._all_chats_key(user_id), json.dumps({
      "chat_id": chat_id,
      "title": title,
      "created_at": int(time.time())
    }))

  def fetch_full_chat_messages(self, user_id: str, chat_id: str) -> list:
    val = self.rc.get(self._chat_key(user_id, chat_id))
    return json.loads(val) if val else []

  def save_chat_messages(self, user_id: str, chat_id: str, messages: list):
    self.rc.set(self._chat_key(user_id, chat_id), json.dumps(messages))
