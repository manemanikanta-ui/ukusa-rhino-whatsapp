from datetime import datetime, timedelta
from collections import defaultdict

# In-memory store: phone_number → {messages, metadata, last_active}
_store = defaultdict(lambda: {
    "messages": [],
    "name": None,
    "language": "en",
    "last_active": datetime.now(),
    "visit_count": 0,
    "last_items_discussed": []
})

MAX_HISTORY = 20        # keep last 20 messages per customer
SESSION_TIMEOUT = 6     # hours before resetting context

def get_history(phone: str) -> list:
    _clean_old_sessions(phone)
    return _store[phone]["messages"]

def add_message(phone: str, role: str, content: str):
    _store[phone]["messages"].append({"role": role, "content": content})
    _store[phone]["last_active"] = datetime.now()
    # Trim to max history
    if len(_store[phone]["messages"]) > MAX_HISTORY:
        _store[phone]["messages"] = _store[phone]["messages"][-MAX_HISTORY:]

def get_meta(phone: str) -> dict:
    return _store[phone]

def set_meta(phone: str, key: str, value):
    _store[phone][key] = value
    
def increment_visits(phone: str):
    _store[phone]["visit_count"] += 1

def _clean_old_sessions(phone: str):
    last = _store[phone]["last_active"]
    if datetime.now() - last > timedelta(hours=SESSION_TIMEOUT):
        _store[phone]["messages"] = []  # reset conversation, keep metadata