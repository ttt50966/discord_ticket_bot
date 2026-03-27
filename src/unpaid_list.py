import json
import os

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'unpaid_users.json')


def _load() -> dict:
    if not os.path.exists(_DATA_PATH):
        return {}
    with open(_DATA_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs(os.path.dirname(_DATA_PATH), exist_ok=True)
    with open(_DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_unpaid(user_id: int) -> bool:
    return str(user_id) in _load()


def add_unpaid(user_id: int, username: str = ""):
    data = _load()
    data[str(user_id)] = username
    _save(data)


def remove_unpaid(user_id: int) -> bool:
    """回傳 True 表示成功移除，False 表示原本不在名單內"""
    data = _load()
    if str(user_id) not in data:
        return False
    del data[str(user_id)]
    _save(data)
    return True


def list_unpaid() -> dict:
    """回傳 {user_id_str: username} 字典"""
    return _load()
