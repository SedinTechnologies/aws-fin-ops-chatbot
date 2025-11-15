import bcrypt
from session_store import SessionStore

class AuthManager:
  def __init__(self, store: SessionStore):
    self.store = store

  def hash_password(self, plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

  def verify_password(self, plain: str, hashed: str) -> bool:
    try:
      return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
      return False

  def authenticate(self, user_name: str, password: str) -> dict | None:
    user = self.store.get_user(user_name)
    if not user:
      return None
    if not self.verify_password(password, user["password_hash"]):
      return None
    return user
