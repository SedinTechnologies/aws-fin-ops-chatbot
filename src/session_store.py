import json
from abc import ABC, abstractmethod

class SessionStore(ABC):
  @abstractmethod
  def get_user(self, user_id: str) -> dict | None:
    pass

  @abstractmethod
  def create_user(self, id: str, user_data: dict):
    pass

class RedisSessionStore(SessionStore):
  def __init__(self, redis_client):
    self.rc = redis_client

  def _user_key(self, user_id):
    return f"user:{user_id}"

  def get_user(self, user_id: str):
    val = self.rc.get(self._user_key(user_id))
    return json.loads(val) if val else None

  def create_user(self, user_id: str, user_data: dict):
    self.rc.set(self._user_key(user_id), json.dumps(user_data))
