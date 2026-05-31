from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from agent import chat, format_event_overview, format_menu_overview, normalize_phone
from crm import get_bookings, get_leads, get_stats
from memory import set_meta

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger('ukusa_rhino')
if not logger.handlers:
    logger.setLevel(logging.INFO)
    file_handler = RotatingFileHandler(LOG_DIR / 'rex.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s'))
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(levelname)s | %(message)s'))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

app = Flask(__name__)

WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN', '')
PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_ID', '')
print("WHATSAPP_TOKEN loaded:", bool(WHATSAPP_TOKEN))
print("PHONE_NUMBER_ID loaded:", PHONE_NUMBER_ID)
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN', 'ukusa_rhino_rex')
ADMIN_PHONE_NUMBERS = {
    normalize_phone(number)
    for raw in (
        os.getenv('ADMIN_PHONE_NUMBERS', ''),
        os.getenv('ADMIN_PHONE_NUMBER', ''),
    )
    for number in str(raw).split(',')
    if number.strip()
}


def send_message(to: str, text: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error('WhatsApp credentials are missing; cannot send message to %s', to)
        return False

    url = f'https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages'
    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type': 'application/json',
    }
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': text},
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        logger.info('Outgoing WhatsApp message sent to %s', to)
        return True
    except requests.RequestException as exc:
        logger.exception('Failed to send WhatsApp message to %s: %s', to, exc)
        return False


def _extract_value_and_message(data: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for entry in data.get('entry', []):
        for change in entry.get('changes', []):
            value = change.get('value', {})
            messages = value.get('messages') or []
            if messages:
                return value, messages[0]
    return None, None


def _extract_text(message: dict[str, Any]) -> str:
    msg_type = message.get('type')
    if msg_type == 'text':
        return str(message.get('text', {}).get('body', '')).strip()
    if msg_type == 'interactive':
        interactive = message.get('interactive', {})
        button = interactive.get('button_reply', {})
        if button.get('title'):
            return str(button.get('title')).strip()
        list_reply = interactive.get('list_reply', {})
        if list_reply.get('title'):
            return str(list_reply.get('title')).strip()
    if msg_type == 'button':
        button = message.get('button', {})
        return str(button.get('text', '')).strip()
    return ''


def _extract_contact_name(value: dict[str, Any]) -> str | None:
    contacts = value.get('contacts') or []
    if not contacts:
        return None
    profile = contacts[0].get('profile') or {}
    name = profile.get('name')
    return str(name).strip() if name else None


def _is_admin(phone: str) -> bool:
    return normalize_phone(phone) in ADMIN_PHONE_NUMBERS if ADMIN_PHONE_NUMBERS else False


def _format_admin_list(title: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f'{title}\nNo records found.'
    lines = [title]
    for row in rows:
        parts = []
        for key in ('name', 'phone', 'intent', 'date', 'time', 'guests', 'timestamp', 'created_at'):
            value = row.get(key)
            if value:
                parts.append(f'{key}={value}')
        lines.append('- ' + ' | '.join(parts) if parts else '- (empty)')
    return '\n'.join(lines)


def _handle_admin_command(text: str) -> str | None:
    command = text.strip().lower()
    if command == 'admin menu':
        return f"Ukusa Rhino menu\n\n{format_menu_overview()}"
    if command == 'admin leads':
        return _format_admin_list('Recent leads', get_leads(10))
    if command == 'admin bookings':
        return _format_admin_list('Recent bookings', get_bookings(10))
    if command == 'admin stats':
        stats = get_stats()
        return (
            'Rex admin stats\n\n'
            f"Total leads: {stats['total_leads']}\n"
            f"Total bookings: {stats['total_bookings']}\n"
            f"Lead intents: {stats['lead_intents']}\n"
            f"Recent booking names: {', '.join(stats['recent_booking_names']) or 'none'}"
        )
    return None


@app.route('/webhook', methods=['GET'])
def verify():
    challenge = request.args.get('hub.challenge', '')
    token = request.args.get('hub.verify_token', '')
    if token == VERIFY_TOKEN:
        return challenge
    return 'Forbidden', 403


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True) or {}
    value, message = _extract_value_and_message(data)
    if not value or not message:
        logger.info('Webhook received a non-message payload')
        return jsonify({'status': 'ok'}), 200

    phone = normalize_phone(message.get('from', ''))
    if not phone:
        logger.warning('Webhook message missing sender phone')
        return jsonify({'status': 'ok'}), 200

    contact_name = _extract_contact_name(value)
    if contact_name:
        set_meta(phone, 'name', contact_name)

    user_text = _extract_text(message)
    if not user_text:
        help_text = 'Hey! Rex can read text messages best. Send your mood, a booking request, or ask about events.'
        send_message(phone, help_text)
        return jsonify({'status': 'ok'}), 200

    logger.info('Incoming WhatsApp message from %s: %s', phone, user_text)

    if _is_admin(phone):
        admin_reply = _handle_admin_command(user_text)
        if admin_reply:
            send_message(phone, admin_reply)
            return jsonify({'status': 'ok', 'mode': 'admin'}), 200

    try:
        reply = chat(phone, user_text)
        send_message(phone, reply)
    except Exception as exc:
        logger.exception('Failed to process webhook message from %s: %s', phone, exc)
        fallback = 'Rex hit a quick pit stop. Please send that again in a moment.'
        send_message(phone, fallback)

    return jsonify({'status': 'ok'}), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8000')), debug=os.getenv('FLASK_DEBUG', '0') == '1')
