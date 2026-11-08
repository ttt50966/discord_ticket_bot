import datetime
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


def _normalize(entry) -> dict:
    """舊格式的值只是 username 字串，轉成新格式（無欠款明細）"""
    if isinstance(entry, str):
        return {"name": entry, "added_at": None, "debts": []}
    return entry


def is_unpaid(user_id: int) -> bool:
    return str(user_id) in _load()


def add_unpaid(user_id: int, username: str = "", debts: list | None = None):
    """
    debts：加入當下的未付款票，[{"date": "YYYY-MM-DD", "category", "amount"}]，
    記下來之後就算 ticket_records 被清掉也查得到他欠的是哪幾天。
    """
    data = _load()
    data[str(user_id)] = {
        "name": username,
        "added_at": datetime.date.today().isoformat(),
        "debts": debts or [],
    }
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
    """回傳 {user_id_str: {"name", "added_at", "debts"}} 字典（舊格式自動轉換）"""
    return {uid: _normalize(v) for uid, v in _load().items()}
