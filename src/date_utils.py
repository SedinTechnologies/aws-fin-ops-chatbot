from datetime import datetime as _datetime

class LenientDatetime(_datetime):
  @classmethod
  def strptime(cls, date_string, fmt):
    try:
      return _datetime.strptime(date_string, fmt)
    except ValueError as exc:
      if fmt.endswith("Z") and not date_string.endswith("Z"):
        return _datetime.strptime(f"{date_string}Z", fmt)
      if not fmt.endswith("Z") and date_string.endswith("Z"):
        return _datetime.strptime(date_string[:-1], fmt)
      raise exc
