import datetime
import json
import os

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'ticket_records.json')

# 票券金額單一來源
TICKET_PRICES = {"游泳池": 50, "健身中心": 40}


def _load() -> list:
    if not os.path.exists(_DATA_PATH):
        return []
    with open(_DATA_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _save(data: list):
    os.makedirs(os.path.dirname(_DATA_PATH), exist_ok=True)
    with open(_DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_record(user_id: int, name: str, category: str, dm_message_id: int, remaining: int | None = None):
    """remaining：這張用掉後該類別還剩幾張（KC Home 狀態頁讀最後一筆顯示，2026-10-29 起才有）"""
    data = _load()
    data.append({
        "user_id": user_id,
        "name": name,
        "category": category,
        "amount": TICKET_PRICES.get(category, 0),
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dm_message_id": dm_message_id,
        "last_reminded": None,
        "remaining": remaining,
    })
    _save(data)


_REMAINING_PATH = os.path.join(os.path.dirname(_DATA_PATH), 'remaining.json')


def save_remaining(category: str, remaining: int, source: str):
    """記下各類別最新已知剩餘張數（取票成功或 /剩餘票數 查詢），給 KC Home 狀態頁讀"""
    data = {}
    if os.path.exists(_REMAINING_PATH):
        with open(_REMAINING_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    data[category] = {"remaining": remaining, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "source": source}
    with open(_REMAINING_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_records() -> list:
    """回傳全部紀錄"""
    return _load()


def mark_reminded(user_id: int):
    """把該 user 所有紀錄的 last_reminded 設為現在（UTC ISO8601）"""
    data = _load()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for record in data:
        if record.get("user_id") == user_id:
            record["last_reminded"] = now
    _save(data)
