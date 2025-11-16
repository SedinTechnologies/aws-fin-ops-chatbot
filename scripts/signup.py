import os, bcrypt, json, redis

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", 6379)
REDIS_DB = os.getenv("REDIS_DB", 0)

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

def hash_password(plain: str) -> str:
  return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def store_user(identifier: str, name: str, password: str, aws_role_arn: str):
  data = {
    "identifier": identifier,
    "name": name,
    "password_hash": hash_password(password),
    "aws_role_arn": aws_role_arn
  }
  key = f'user:{identifier}'
  redis_client.set(key, json.dumps(data))
  print(f"Stored user {key} in Redis.")

# Example usage
if __name__ == "__main__":
  store_user("sedin-rails-factory", "Sedin DevOps - RF", "SecurePass123!", "arn:aws:iam::784582181785:role/AwsFinOpsGPTRole")
