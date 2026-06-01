# NOTE: Railway ephemeral filesystem.
# memory.json resets on redeploy.
# TODO: migrate to Railway PostgreSQL
# for persistent memory in production.
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
STORE_FILE = DATA_DIR / 'memory.json'

MAX_HISTORY = 20
SESSION_TIMEOUT = 6  # hours

_LOCK = threading.RLock()
_store: dict[str, dict[str, Any]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _normalize_phone(phone: str) -> str:
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


def _default_record() -> dict[str, Any]:
    return {
        'messages': [],
        'name': None,
        'language': 'en',
        'last_active': _iso_now(),
        'visit_count': 0,
        'visit_history': [],
        'previous_visits': 0,
        'favorite_dishes': [],
        'favorite_drinks': [],
        'preferences': [],
        'last_items_discussed': [],
        'booking_draft': {},
        'bookings': [],
        'notes': [],
        'last_intent': 'general customer',
        'last_source_message': '',
    }


def _coerce_record(payload: Any) -> dict[str, Any]:
    record = _default_record()
    if isinstance(payload, dict):
        record.update(payload)

    for key in ('messages', 'visit_history', 'favorite_dishes', 'favorite_drinks', 'preferences', 'last_items_discussed', 'bookings', 'notes'):
        value = record.get(key)
        if not isinstance(value, list):
            record[key] = []

    if not isinstance(record.get('booking_draft'), dict):
        record['booking_draft'] = {}

    if not record.get('last_active'):
        record['last_active'] = _iso_now()

    return record


def _load() -> None:
    if not STORE_FILE.exists():
        return

    try:
        payload = json.loads(STORE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return

    if isinstance(payload, dict):
        for phone, record in payload.items():
            _store[_normalize_phone(phone)] = _coerce_record(record)


def _save() -> None:
    with _LOCK:
        STORE_FILE.write_text(json.dumps(_store, ensure_ascii=False, indent=2), encoding='utf-8')


def _ensure(phone: str) -> dict[str, Any]:
    phone = _normalize_phone(phone)
    if not phone:
        raise ValueError('phone is required')

    with _LOCK:
        if phone not in _store:
            _store[phone] = _default_record()
        else:
            _store[phone] = _coerce_record(_store[phone])
        return _store[phone]


def _maybe_reset_history(record: dict[str, Any]) -> None:
    last_active = record.get('last_active')
    if not last_active:
        return

    try:
        last_dt = datetime.fromisoformat(str(last_active))
    except ValueError:
        return

    if _utcnow() - last_dt > timedelta(hours=SESSION_TIMEOUT):
        record['messages'] = []
        record['last_active'] = _iso_now()
        _save()


def get_history(phone: str) -> list[dict[str, str]]:
    with _LOCK:
        record = _ensure(phone)
        _maybe_reset_history(record)
        return [dict(message) for message in record['messages']]


def add_message(phone: str, role: str, content: str) -> None:
    with _LOCK:
        record = _ensure(phone)
        _maybe_reset_history(record)
        record['messages'].append({'role': role, 'content': content})
        record['last_active'] = _iso_now()
        if len(record['messages']) > MAX_HISTORY:
            record['messages'] = record['messages'][-MAX_HISTORY:]
        _save()


def get_meta(phone: str) -> dict[str, Any]:
    with _LOCK:
        return _ensure(phone)


def set_meta(phone: str, key: str, value: Any) -> None:
    with _LOCK:
        record = _ensure(phone)
        record[key] = value
        record['last_active'] = _iso_now()
        _save()


def update_meta(phone: str, **changes: Any) -> None:
    with _LOCK:
        record = _ensure(phone)
        for key, value in changes.items():
            record[key] = value
        record['last_active'] = _iso_now()
        _save()


def increment_visits(phone: str) -> None:
    with _LOCK:
        record = _ensure(phone)
        record['visit_count'] += 1
        record['previous_visits'] += 1
        record['visit_history'].append(_iso_now())
        record['last_active'] = _iso_now()
        _save()


def clear_booking_draft(phone: str) -> None:
    set_meta(phone, 'booking_draft', {})


def add_booking(phone: str, booking: dict[str, Any]) -> None:
    with _LOCK:
        record = _ensure(phone)
        record['bookings'].append(booking)
        record['last_active'] = _iso_now()
        _save()


def add_note(phone: str, note: str) -> None:
    with _LOCK:
        record = _ensure(phone)
        record['notes'].append({'timestamp': _iso_now(), 'note': note})
        record['last_active'] = _iso_now()
        _save()


def remember_preferences(phone: str, preferences: list[str]) -> None:
    with _LOCK:
        record = _ensure(phone)
        merged = []
        for item in record['preferences'] + preferences:
            if item and item not in merged:
                merged.append(item)
        record['preferences'] = merged[-20:]
        record['last_active'] = _iso_now()
        _save()


def add_favorite(phone: str, item: str, bucket: str) -> None:
    with _LOCK:
        record = _ensure(phone)
        key = 'favorite_drinks' if bucket == 'drink' else 'favorite_dishes'
        merged = []
        for existing in record[key] + [item]:
            if existing and existing not in merged:
                merged.append(existing)
        record[key] = merged[-20:]
        record['last_items_discussed'] = [item]
        record['last_active'] = _iso_now()
        _save()


def add_to_basket(phone: str, item: str) -> list[str]:
    with _LOCK:
        record = _ensure(phone)
        basket = record.get('basket', [])
        if not isinstance(basket, list):
            basket = []
        basket.append(item)
        record['basket'] = basket
        record['last_active'] = _iso_now()
        _save()
        return basket


def get_basket(phone: str) -> list[str]:
    with _LOCK:
        basket = _ensure(phone).get('basket', [])
        return list(basket) if isinstance(basket, list) else []


def clear_basket(phone: str) -> None:
    set_meta(phone, 'basket', [])


def set_table(phone: str, table_number: str) -> None:
    set_meta(phone, 'table_number', str(table_number or '').strip())


def get_table(phone: str) -> str:
    return str(get_meta(phone).get('table_number', '') or '')


_load()


