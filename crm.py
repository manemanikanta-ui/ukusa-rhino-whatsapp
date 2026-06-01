# NOTE: Railway ephemeral filesystem.
# memory.json resets on redeploy.
# TODO: migrate to Railway PostgreSQL
# for persistent memory in production.
from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
CRM_FILE = DATA_DIR / 'crm.json'
MEMORY_FILE = DATA_DIR / 'memory.json'

_LOCK = threading.RLock()
_db: dict[str, Any] = {
    'leads': [],
    'bookings': [],
    'orders': [],
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_local() -> str:
    return datetime.now().astimezone().date().isoformat()


def _save() -> None:
    with _LOCK:
        CRM_FILE.write_text(json.dumps(_db, ensure_ascii=False, indent=2), encoding='utf-8')


def _load() -> None:
    if not CRM_FILE.exists():
        return
    try:
        payload = json.loads(CRM_FILE.read_text(encoding='utf-8'))
    except Exception:
        return
    if isinstance(payload, dict):
        _db['leads'] = payload.get('leads', []) if isinstance(payload.get('leads', []), list) else []
        _db['bookings'] = payload.get('bookings', []) if isinstance(payload.get('bookings', []), list) else []
        _db['orders'] = payload.get('orders', []) if isinstance(payload.get('orders', []), list) else []


def _load_memory_store() -> dict[str, Any]:
    if not MEMORY_FILE.exists():
        return {}
    try:
        payload = json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _booking_date_string(entry: dict[str, Any]) -> str:
    value = str(entry.get('created_at', ''))
    if not value:
        return ''
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return ''
    return dt.astimezone().date().isoformat()


def record_lead(phone: str, name: str | None, intent: str, message: str = '', source: str = 'whatsapp') -> dict[str, Any]:
    lead = {
        'phone': str(phone or ''),
        'name': name or None,
        'intent': intent or 'general customer',
        'message': (message or '')[:300],
        'source': source,
        'timestamp': _iso_now(),
    }
    with _LOCK:
        _db['leads'].append(lead)
        _save()
    return lead


def record_booking(
    phone: str,
    name: str | None,
    date: str,
    time: str,
    guests: str,
    notes: str = '',
    source: str = 'whatsapp',
) -> dict[str, Any]:
    booking = {
        'phone': str(phone or ''),
        'name': name or None,
        'date': date,
        'time': time,
        'guests': guests,
        'notes': notes,
        'source': source,
        'created_at': _iso_now(),
    }
    with _LOCK:
        _db['bookings'].append(booking)
        _save()
    return booking


def save_order(order: dict[str, Any]) -> None:
    with _LOCK:
        orders = _db.setdefault('orders', [])
        if not isinstance(orders, list):
            orders = []
        payload = dict(order)
        payload['created_at'] = _iso_now()
        orders.append(payload)
        _db['orders'] = orders
        _save()


def get_orders(limit: int = 20) -> list[dict[str, Any]]:
    with _LOCK:
        orders = _db.get('orders', [])
        if not isinstance(orders, list):
            return []
        recent = list(reversed(orders))
        return recent[:limit]


def get_leads(limit: int | None = 10) -> list[dict[str, Any]]:
    with _LOCK:
        leads = list(reversed(_db['leads']))
        return leads if limit is None else leads[:limit]


def get_recent_bookings(limit: int | None = 10) -> list[dict[str, Any]]:
    with _LOCK:
        bookings = list(reversed(_db['bookings']))
        return bookings if limit is None else bookings[:limit]


def get_bookings(limit: int | None = 10) -> list[dict[str, Any]]:
    return get_recent_bookings(limit=limit)


def get_stats() -> dict[str, Any]:
    with _LOCK:
        lead_intents = Counter(lead.get('intent', 'general customer') for lead in _db['leads'])
        bookings_today = sum(1 for booking in _db['bookings'] if _booking_date_string(booking) == _today_local())
        memory_store = _load_memory_store()
        now = datetime.now(timezone.utc)
        active_conversations = 0
        for record in memory_store.values():
            if not isinstance(record, dict) or not record.get('messages'):
                continue
            last_active = str(record.get('last_active', ''))
            try:
                last_dt = datetime.fromisoformat(last_active)
            except ValueError:
                continue
            if now - last_dt <= timedelta(hours=6):
                active_conversations += 1
        return {
            'total_leads': len(_db['leads']),
            'total_bookings': len(_db['bookings']),
            'bookings_today': bookings_today,
            'active_conversations': active_conversations,
            'lead_intents': dict(lead_intents),
            'recent_booking_names': [booking.get('name') for booking in _db['bookings'] if booking.get('name')][-5:],
        }


_load()


