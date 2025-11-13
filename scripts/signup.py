import bcrypt, json, redis

redis_client = redis.Redis(host="redis", port=6379, db=0)

def hash_password(plain: str) -> str:
  return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def store_user(email: str, name: str, password: str, aws_role_arn: str):
  data = {
    "email": email,
    "name": name,
    "password_hash": hash_password(password),
    "aws_role_arn": aws_role_arn
  }
  key = f'user:{email}'
  redis_client.set(key, json.dumps(data))
  print(f"Stored user {key} in Redis.")

# Example usage
if __name__ == "__main__":
  store_user("madhav@tarkalabs.com", "Madhava Reddy SV", "MySecurePass123!", "arn:aws:iam::260741046218:role/AwsFinOpsGPTRole")
