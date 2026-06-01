from __future__ import annotations

import logging
import os
import re
import random
from datetime import datetime, timezone
from typing import Any

import anthropic
from dotenv import load_dotenv

from business_data import BUSINESS
from crm import get_recent_bookings, get_stats, record_booking, record_lead
from memory import (
    add_to_basket,
    add_booking,
    add_favorite,
    add_message,
    clear_basket,
    get_basket,
    get_table,
    get_history,
    get_meta,
    increment_visits,
    remember_preferences,
    set_meta,
    set_table,
    update_meta,
)
from menu_data import UKUSA_MENU

load_dotenv()

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
MODEL = 'claude-sonnet-4-20250514'


INTERCEPT_LOCATION = {
    'location', 'located', 'address', 'where are you',
    'where is', 'directions', 'direction', 'maps', 'map',
    'how to reach', 'how to come', 'reach you',
    'kahan', 'kidhar', 'ekkade', 'cheppandi'
}

INTERCEPT_HOURS = {
    'hours', 'timing', 'timings', 'open', 'opening',
    'close', 'closing', 'what time', 'when do you open',
    'enduku', 'time'
}

INTERCEPT_MENU = {
    'menu', 'full menu', 'show menu', 'send menu',
    'what do you have', 'food menu', 'drink menu'
}

INTERCEPT_CONTACT = {
    'phone', 'call', 'number', 'contact', 'whatsapp number'
}

INTERCEPT_EVENTS = {
    'events', 'event', 'f1', 'ipl', 'screening',
    'bike meet', 'bike meets', 'motogp', 'private event'
}

MENU_CATEGORIES = list(UKUSA_MENU.values())
ALL_MENU_ITEMS = [item for category in MENU_CATEGORIES for item in category.get('items', [])]
ALL_MENU_ITEMS_LOWER = [(item, item.lower()) for item in ALL_MENU_ITEMS]
MENU_ITEM_LOOKUP = {re.sub(r'[^a-z0-9]+', '', item.lower()): item for item in ALL_MENU_ITEMS}

CATEGORY_TO_KEYS = {
    'cat_food': ['cold_start', 'full_throttle', 'pit_lane', 'victory_lap'],
    'cat_drinks': ['ignition'],
    'cat_desserts': ['victory_lap'],
}

RECOMMENDATION_RULES: dict[str, list[str]] = {
    'coffee': ['Roasted Hazelnut Coffee', 'Peach Cold Brew', 'Flat White'],
    'sweet': ['Tres Leches', 'Tiramisu', 'Hazelnut Pancakes (Maple Syrup & Whipped Cream)'],
    'spicy': ['Arrabiata Pasta', 'Chicken Satay with Basil Lime Sauce', 'Prawns with Chili Basil Dressing'],
    'protein': ['Grilled Chicken with Gravy & Mash Potatoes', 'Chicken Satay with Basil Lime Sauce', 'Prawns with Chili Basil Dressing'],
    'light': ['Signature Salads', 'Stacked Sandwiches', 'Classic Pancakes'],
    'breakfast': ['Veggie Breakfast Platter (Hash Brown, Grilled Tomato, Satay Mushroom, Baked Beans, Grilled Veggies, Cottage Cheese Toast)', 'Iranian Breakfast Platter (Hummus, Tzatziki, Tabbouleh, Pita Bread, Lamb Keema Omelette)', 'Classic Pancakes'],
    'hungry': ['Barbecue Chicken Pizza', 'Alfredo Pasta', 'Grilled Chicken with Gravy & Mash Potatoes'],
}

RECOMMENDATION_REASONS: dict[str, str] = {
    'coffee': 'fast, smooth, and built for a proper pit stop',
    'sweet': 'good if you want a dessert-style finish',
    'spicy': 'brings a hotter, more energetic kick',
    'protein': 'more filling and muscle-fuel friendly',
    'light': 'easygoing if you want something not too heavy',
    'breakfast': 'great for a morning start or brunch',
    'hungry': 'better when you want a fuller meal',
}

BOOKING_KEYWORDS = {'book', 'booking', 'reserve', 'reservation', 'table', 'tables'}
EVENT_KEYWORDS = {'event', 'events', 'screening', 'screenings', 'f1', 'formula 1', 'motogp', 'ipl', 'bike meet', 'bike meets', 'community'}
MENU_KEYWORDS = {'menu', 'eat', 'food', 'suggest', 'recommend', 'recommendation', 'coffee', 'spicy', 'sweet', 'protein', 'breakfast', 'brunch', 'light', 'hungry'}
CORPORATE_KEYWORDS = {'corporate', 'company', 'team outing', 'team out', 'office', 'offsite', 'bulk'}
HINGLISH_HINTS = {'kya', 'hai', 'nahi', 'mujhe', 'chahiye', 'batao', 'acha', 'bhook', 'khana', 'thoda', 'bahut', 'menu', 'booking'}


def normalize_phone(phone: str) -> str:
    return ''.join(ch for ch in str(phone or '') if ch.isdigit())


ADMIN_PHONES = {'919052462424'}
for raw in (os.getenv('ADMIN_PHONE_NUMBER', ''), os.getenv('ADMIN_PHONE_NUMBERS', '')):
    for value in str(raw).split(','):
        digits = normalize_phone(value)
        if digits:
            ADMIN_PHONES.add(digits)


def format_menu_overview() -> str:
    lines: list[str] = []
    for category in UKUSA_MENU.values():
        items_preview = ', '.join(category['items'][:3])
        lines.append(f"{category['label']}\n{items_preview}...")
    return '\n'.join(lines)


def format_event_overview() -> str:
    return '\n'.join(BUSINESS['events'])


def _menu_pdf_line() -> str:
    return f"Full menu: {BUSINESS['menu_pdf_url']}"


SYSTEM_PROMPT = (
    "You are Rex 🏍️ - the WhatsApp concierge for Ukusa Rhino, Hyderabad's first motorsport cafe.\n\n"
    "PERSONALITY:\n"
    "- Fast. Direct. Warm. Like a pit crew member who loves food.\n"
    "- Short WhatsApp replies - maximum 4 lines unless showing menu.\n"
    "- Never robotic. Never over-explain.\n"
    "- Light racing puns are welcome. Not forced.\n\n"
    "LANGUAGE:\n"
    "- Detect customer language from their first message.\n"
    "- Reply in the EXACT same language: Telugu, Hindi, Hinglish, English.\n"
    "- Never switch unless they do.\n\n"
    "STRICT RULES - READ CAREFULLY:\n"
    "1. NEVER invent addresses, locations, or directions.\n"
    "2. NEVER invent menu items or prices. Use only items from the menu provided to you.\n"
    "3. NEVER invent events, timings, or business information. If you don't have the data, say exactly: I don't have that info right now - call us on +91 90524 62424 🏁\n"
    "4. For food/drink recommendations: ask one short question about mood if unclear, then give 2-3 specific items with one-line descriptions, and suggest a drink to pair.\n"
    "5. For table bookings: collect only name, date, time, and number of guests. Ask for one missing field at a time.\n"
    "6. Never use markdown headers, bullet symbols, or asterisks unless it's for WhatsApp bold (*text*). Keep formatting native to WhatsApp.\n\n"
    f"BUSINESS FACTS (source of truth):\n"
    f"Name: {BUSINESS['name']}\n"
    f"Tagline: {BUSINESS['tagline']}\n"
    f"Founder: {BUSINESS['founder']}\n"
    f"Phone: {BUSINESS['phone']}\n"
    f"Website: {BUSINESS['website']}\n"
    f"Instagram: {BUSINESS['instagram']}\n"
    f"WhatsApp: {BUSINESS['whatsapp']}\n\n"
    f"EVENTS:\n{format_event_overview()}\n\n"
    f"MENU (use only these items):\n{format_menu_overview()}\n\n"
    "UPSELLING (only suggest real items):\n"
    "- With pasta -> suggest a cold brew or fresh juice\n"
    "- With pizza -> suggest garlic bread or a milkshake\n"
    "- With pancakes -> suggest hazelnut coffee\n"
    "- With grilled chicken -> suggest fresh juice or lemonade\n"
    "- After main course -> suggest tiramisu or tres leches"
)


def detect_language(text: str) -> str:
    if re.search(r'[\u0C00-\u0C7F]', text):
        return 'te'
    if re.search(r'[\u0900-\u097F]', text):
        return 'hi'
    lower = text.lower()
    if any(word in lower for word in HINGLISH_HINTS):
        return 'hinglish'
    return 'en'


def classify_intent(text: str) -> str:
    lower = text.lower()
    if any(keyword in lower for keyword in BOOKING_KEYWORDS):
        return 'booking'
    if any(keyword in lower for keyword in EVENT_KEYWORDS):
        return 'event inquiry'
    if any(keyword in lower for keyword in CORPORATE_KEYWORDS):
        return 'corporate inquiry'
    if any(keyword in lower for keyword in MENU_KEYWORDS):
        return 'menu recommendation'
    return 'general customer'


def detect_mood(text: str) -> str | None:
    lower = text.lower()
    mood_rules = {
        'coffee': ['coffee', 'cold brew', 'cappuccino', 'flat white', 'americano', 'hazelnut'],
        'sweet': ['sweet', 'dessert', 'tiramisu', 'leches', 'pancake', 'pastry'],
        'spicy': ['spicy', 'spice', 'chili', 'chilli', 'hot', 'arrabiata', 'satay'],
        'protein': ['protein', 'healthy', 'gym', 'workout', 'high protein', 'grilled chicken', 'prawns'],
        'light': ['light', 'snack', 'bite', 'salad', 'not too heavy', 'small'],
        'breakfast': ['breakfast', 'morning', 'brunch', 'croissant', 'eggs', 'pancake'],
        'hungry': ['hungry', 'full meal', 'meal', 'food', 'lunch', 'dinner'],
    }
    for mood, keywords in mood_rules.items():
        if any(keyword in lower for keyword in keywords):
            return mood
    return None


def recommend_items(text: str) -> tuple[str | None, list[str]]:
    mood = detect_mood(text)
    if mood is None:
        return None, []
    return mood, RECOMMENDATION_RULES.get(mood, [])


def extract_name(text: str) -> str | None:
    patterns = [
        r"\bmy name is\s+([A-Za-z][A-Za-z .'-]{1,50})",
        r"\bi am\s+([A-Za-z][A-Za-z .'-]{1,50})",
        r"\bi'm\s+([A-Za-z][A-Za-z .'-]{1,50})",
        r"\bname[:\-]\s*([A-Za-z][A-Za-z .'-]{1,50})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip('.,!')
    return None


def _extract_menu_mentions(text: str) -> list[str]:
    lower = text.lower()
    matches: list[str] = []
    for item, item_lower in ALL_MENU_ITEMS_LOWER:
        if item_lower in lower:
            matches.append(item)
    return matches


def _update_preferences_from_message(phone: str, text: str, profile: dict[str, Any]) -> None:
    mentions = _extract_menu_mentions(text)
    if not mentions:
        if any(word in text.lower() for word in {'coffee', 'drink', 'juice', 'lemonade', 'milkshake', 'cold brew'}):
            remember_preferences(phone, ['drinks'])
        return

    for item in mentions:
        lower = item.lower()
        bucket = 'drink' if any(keyword in lower for keyword in {'coffee', 'brew', 'cappuccino', 'americano', 'flat white', 'juice', 'lemonade', 'milkshake', 'tea'}) else 'dish'
        add_favorite(phone, item, bucket)

    if profile.get('name'):
        update_meta(phone, favorite_dishes=profile.get('favorite_dishes', []), favorite_drinks=profile.get('favorite_drinks', []))


def _booking_requested(text: str) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in BOOKING_KEYWORDS)


def _extract_booking_details(text: str, profile_name: str | None) -> dict[str, str | None]:
    lower = text.lower()
    date_value = None
    time_value = None
    guests_value = None

    date_patterns = [
        r'\b\d{4}-\d{2}-\d{2}\b',
        r'\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b',
        r'\btoday\b',
        r'\btomorrow\b',
        r'\btonight\b',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, lower, flags=re.IGNORECASE)
        if match:
            date_value = match.group(0)
            break

    time_patterns = [
        r'\b\d{1,2}:\d{2}\s?(?:am|pm)?\b',
        r'\b\d{1,2}\s?(?:am|pm)\b',
    ]
    for pattern in time_patterns:
        match = re.search(pattern, lower, flags=re.IGNORECASE)
        if match:
            time_value = match.group(0)
            break

    guests_match = re.search(r'\b(\d{1,2})\s*(?:guests?|people|persons?|pax|covers?)\b', lower, flags=re.IGNORECASE)
    if guests_match:
        guests_value = guests_match.group(1)

    name_value = extract_name(text) or profile_name
    return {
        'name': name_value,
        'date': date_value,
        'time': time_value,
        'guests': guests_value,
    }


def _booking_state(phone: str, text: str, profile: dict[str, Any]) -> dict[str, Any]:
    draft = dict(profile.get('booking_draft') or {})
    if _booking_requested(text):
        draft['active'] = True

    extracted = _extract_booking_details(text, profile.get('name'))
    for field, value in extracted.items():
        if value and not draft.get(field):
            draft[field] = value

    if draft.get('active') and not draft.get('name') and profile.get('name'):
        draft['name'] = profile.get('name')

    missing = [field for field in ('name', 'date', 'time', 'guests') if not draft.get(field)]
    complete = bool(draft.get('active')) and not missing
    update_meta(phone, booking_draft=draft)
    return {'draft': draft, 'missing': missing, 'complete': complete}


def _finalize_booking(phone: str, booking_state: dict[str, Any], text: str) -> dict[str, Any] | None:
    if not booking_state.get('complete'):
        return None

    draft = booking_state['draft']
    booking = record_booking(
        phone=phone,
        name=draft.get('name'),
        date=draft.get('date', ''),
        time=draft.get('time', ''),
        guests=str(draft.get('guests', '')),
        notes=text,
    )
    add_booking(phone, booking)
    set_meta(phone, 'booking_draft', {})
    return booking


def _fmt_location_both() -> str:
    jh = BUSINESS['locations']['jubilee_hills']
    hc = BUSINESS['locations']['hitec_city']
    return (
        '📍 *Ukusa Rhino - 2 Locations*\n\n'
        f"*Jubilee Hills*\n{jh['address']}\n🗺️ {jh['maps_url']}\n🕒 {jh['hours']}\n\n"
        f"*HITEC City*\n{hc['address']}\n🗺️ {hc['maps_url']}\n🕒 {hc['hours']}\n\n"
        f"📞 {BUSINESS['phone']}"
    )


def _fmt_location_single(area_key: str) -> str:
    loc = BUSINESS['locations'][area_key]
    return (
        f"📍 *Ukusa Rhino - {loc['area']}*\n\n"
        f"{loc['address']}\n\n"
        f"🗺️ {loc['maps_url']}\n\n"
        f"🕒 {loc['hours']}\n\n"
        f"📞 {BUSINESS['phone']}"
    )


def _fmt_hours() -> str:
    jh = BUSINESS['locations']['jubilee_hills']
    hc = BUSINESS['locations']['hitec_city']
    return (
        '🕒 *Opening Hours*\n\n'
        f"Jubilee Hills: {jh['hours']}\n"
        f"HITEC City: {hc['hours']}\n\n"
        f"📞 {BUSINESS['phone']}"
    )


def _fmt_menu_overview() -> str:
    lines = [
        '📖 *Ukusa Rhino Menu*',
        f"Full menu: {BUSINESS['menu_pdf_url']}",
        '━━━━━━━━━━━━━━━━━━━━',
    ]
    for data in UKUSA_MENU.values():
        items_preview = ', '.join(data['items'][:3])
        lines.append(f"{data['label']}\n{items_preview}...")
    lines.append('Reply with a category or tell me what you\'re in the mood for 🏁')
    return '\n'.join(lines)


def _fmt_contact() -> str:
    return (
        f"📞 *{BUSINESS['name']}*\n\n"
        f"Call / WhatsApp: {BUSINESS['phone']}\n"
        f"Instagram: {BUSINESS['instagram']}\n"
        f"Website: {BUSINESS['website']}"
    )


def _fmt_booking_confirm(draft: dict) -> str:
    return (
        '✅ *Table Booked!*\n\n'
        f"👤 Name: {draft.get('name')}\n"
        f"📅 Date: {draft.get('date')}\n"
        f"🕒 Time: {draft.get('time')}\n"
        f"👥 Guests: {draft.get('guests')}\n\n"
        "We'll see you on the grid 🏁\n"
        f"Questions? Call {BUSINESS['phone']}"
    )


def _fmt_booking_prompt(missing: list) -> str:
    field_labels = {
        'name': 'Your name',
        'date': 'Date (e.g. 31 May)',
        'time': 'Time (e.g. 7 PM)',
        'guests': 'Number of guests',
    }
    missing_lines = '\n'.join(f"• {field_labels.get(field, field)}" for field in missing)
    return (
        '🏁 *Table Booking*\n\n'
        'Just need a few details:\n\n'
        f'{missing_lines}'
    )


def _fmt_events() -> str:
    event_lines = '\n\n'.join(BUSINESS['events'])
    return (
        '🏆 *Events at Ukusa Rhino*\n\n'
        f'{event_lines}\n\n'
        f"📞 Private bookings: {BUSINESS['phone']}"
    )


def _slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(text or '').lower())


def _resolve_menu_item(identifier: str) -> str:
    cleaned = _slugify(identifier.replace('item_', ''))
    if cleaned in MENU_ITEM_LOOKUP:
        return MENU_ITEM_LOOKUP[cleaned]
    for item in ALL_MENU_ITEMS:
        if cleaned and cleaned in _slugify(item):
            return item
    return identifier.replace('_', ' ').strip()


def _send_main_menu_buttons(phone: str, table: str | None = None) -> None:
    from whatsapp_bot import send_interactive_message

    greeting = 'Welcome to Ukusa Rhino! 🏁'
    if table:
        greeting = f'Welcome to Ukusa Rhino! 🏁\nTable *{table}* locked in.'
    interactive = {
        'type': 'button',
        'body': {
            'text': greeting + '\n\nWhat can Rex get you?'
        },
        'action': {
            'buttons': [
                {'type': 'reply', 'reply': {'id': 'cat_food', 'title': '🍽️ Food Menu'}},
                {'type': 'reply', 'reply': {'id': 'cat_drinks', 'title': '☕ Drinks'}},
                {'type': 'reply', 'reply': {'id': 'view_order', 'title': '📋 My Order'}},
            ]
        }
    }
    send_interactive_message(phone, interactive)


def _send_category_list(phone: str, category: str) -> None:
    from whatsapp_bot import send_interactive_message

    keys = CATEGORY_TO_KEYS.get(category, ['full_throttle'])
    rows: list[dict[str, str]] = []
    for key in keys:
        section_data = UKUSA_MENU.get(key, {})
        for item in section_data.get('items', []):
            item_id = f"item_{_slugify(item)[:40]}"
            rows.append({
                'id': item_id,
                'title': str(item)[:24],
                'description': 'Tap to add to order',
            })
            if len(rows) >= 10:
                break
        if len(rows) >= 10:
            break

    if not rows:
        return

    interactive = {
        'type': 'list',
        'body': {
            'text': 'Select an item to add to your order 🏁'
        },
        'action': {
            'button': 'View Items',
            'sections': [{
                'title': 'Menu Items',
                'rows': rows[:10],
            }]
        }
    }
    send_interactive_message(phone, interactive)


def _send_order_actions(phone: str) -> None:
    from whatsapp_bot import send_interactive_message

    basket = get_basket(phone)
    count = len(basket)
    body = 'Item added! 🏁\n\n*Your order so far:*\n'
    for item in basket:
        body += f'• {item}\n'
    body += f'\n{count} item(s) in your order.'

    interactive = {
        'type': 'button',
        'body': {'text': body},
        'action': {
            'buttons': [
                {'type': 'reply', 'reply': {'id': 'cat_food', 'title': '➕ Add More Food'}},
                {'type': 'reply', 'reply': {'id': 'cat_drinks', 'title': '➕ Add Drinks'}},
                {'type': 'reply', 'reply': {'id': 'submit_order', 'title': '✅ Submit Order'}},
            ]
        }
    }
    send_interactive_message(phone, interactive)


def _submit_order(phone: str) -> str:
    from crm import save_order

    basket = get_basket(phone)
    table = get_table(phone) or 'Unknown'
    profile = get_meta(phone)
    name = profile.get('name') or 'Guest'

    if not basket:
        return 'Your order is empty! Tap *Food Menu* to start adding items 🏁'

    order_num = f"UK{random.randint(100, 999)}"
    save_order({
        'order_number': order_num,
        'phone': phone,
        'name': name,
        'table': table,
        'items': basket,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    })
    clear_basket(phone)

    items_text = '\n'.join(f'• {item}' for item in basket)
    return (
        f'✅ *Order Confirmed!*\n\n'
        f'Order: *#{order_num}*\n'
        f'Table: *{table}*\n\n'
        f'*Your order:*\n{items_text}\n\n'
        f'⏱️ Est. time: 15-20 mins\n\n'
        f'Need anything?\n'
        f'💧 Type *water* for water\n'
        f'🙋 Type *waiter* to call someone\n'
        f'🚻 Type *restroom* for directions\n\n'
        f'Thank you! Race on 🏁'
    )


def _call_claude(messages: list[dict]) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    if not response.content:
        return ''
    parts = []
    for block in response.content:
        text = getattr(block, 'text', '')
        if text:
            parts.append(text)
    return ''.join(parts).strip()


def _build_claude_messages(phone: str) -> list[dict]:
    history = get_history(phone)[-12:]
    return [{'role': msg['role'], 'content': msg['content']} for msg in history]


def _persist_reply(phone: str, user_message: str, reply: str, intent: str, name: str | None = None) -> None:
    record_lead(phone, name, intent, user_message)
    add_message(phone, 'assistant', reply)


def chat(phone: str, user_message: str) -> str:
    phone = normalize_phone(phone)
    user_message = (user_message or '').strip()
    if not phone:
        raise ValueError('phone is required')
    if not user_message:
        return 'Hi! I\'m Rex from Ukusa Rhino. What would you like to do today?'

    lower = user_message.lower().strip()
    language = detect_language(user_message)
    intent = classify_intent(user_message)

    if phone in ADMIN_PHONES:
        if lower == 'admin stats':
            stats = get_stats()
            return (
                '📊 *Rex Stats*\n\n'
                f"Total leads: {stats.get('total_leads', 0)}\n"
                f"Bookings today: {stats.get('bookings_today', 0)}\n"
                f"Total bookings: {stats.get('total_bookings', 0)}\n"
                f"Active conversations: {stats.get('active_conversations', 0)}"
            )
        if lower == 'admin bookings':
            bookings = get_recent_bookings(limit=5)
            if not bookings:
                return 'No bookings yet.'
            lines = ['📅 *Recent Bookings*\n']
            for booking in bookings:
                lines.append(
                    f"• {booking.get('name')} - {booking.get('date')} {booking.get('time')} ({booking.get('guests')} guests)"
                )
            return '\n'.join(lines)

    increment_visits(phone)
    add_message(phone, 'user', user_message)

    profile = get_meta(phone)
    detected_name = profile.get('name') or extract_name(user_message)
    if detected_name and not profile.get('name'):
        set_meta(phone, 'name', detected_name)
        profile = get_meta(phone)

    set_meta(phone, 'language', language)
    set_meta(phone, 'last_intent', intent)
    set_meta(phone, 'last_source_message', user_message)

    _update_preferences_from_message(phone, user_message, profile)
    booking_state = _booking_state(phone, user_message, profile)

    table_match = re.search(r'table\s*#?\s*(\w+)', lower, re.IGNORECASE)
    if table_match:
        table_num = table_match.group(1)
        set_table(phone, table_num)
        _send_main_menu_buttons(phone, table_num)
        return ''

    if lower in {'cat_food', 'cat_drinks', 'cat_desserts', '🍽️ food menu', '☕ drinks'}:
        cat = 'cat_food' if 'food' in lower else 'cat_drinks'
        if 'dessert' in lower:
            cat = 'cat_desserts'
        _send_category_list(phone, cat)
        return ''

    if lower == 'view_order':
        basket = get_basket(phone)
        if not basket:
            reply = 'Your order is empty! Tap *Food Menu* to add items 🏁'
        else:
            items = '\n'.join(f'• {item}' for item in basket)
            reply = f'📋 *Your current order:*\n\n{items}'
        _persist_reply(phone, user_message, reply, 'order view', profile.get('name'))
        return reply

    if lower == 'submit_order':
        reply = _submit_order(phone)
        _persist_reply(phone, user_message, reply, 'order submission', profile.get('name'))
        return reply

    if lower.startswith('item_'):
        item_name = _resolve_menu_item(lower)
        add_to_basket(phone, item_name)
        _send_order_actions(phone)
        return ''

    if lower == 'water':
        reply = '💧 A waiter will bring water to your table shortly!'
        _persist_reply(phone, user_message, reply, 'service request', profile.get('name'))
        return reply

    if lower == 'waiter':
        reply = '🙋 Calling a waiter to Table ' + (get_table(phone) or 'your table') + ' now!'
        _persist_reply(phone, user_message, reply, 'service request', profile.get('name'))
        return reply

    if lower == 'restroom':
        reply = '🚻 Restrooms are at the back of the cafe, past the bar on your right 🏁'
        _persist_reply(phone, user_message, reply, 'service request', profile.get('name'))
        return reply

    if any(kw in lower for kw in INTERCEPT_LOCATION):
        if any(x in lower for x in {'jubilee', 'jh', 'road no'}):
            reply = _fmt_location_single('jubilee_hills')
        elif any(x in lower for x in {'hitec', 'hitech', 'cyber', 'madhapur'}):
            reply = _fmt_location_single('hitec_city')
        else:
            reply = _fmt_location_both()
        _persist_reply(phone, user_message, reply, 'location inquiry', profile.get('name'))
        return reply

    if any(kw in lower for kw in INTERCEPT_HOURS):
        reply = _fmt_hours()
        _persist_reply(phone, user_message, reply, 'hours inquiry', profile.get('name'))
        return reply

    if lower in {'menu', 'show menu', 'full menu', 'send menu', 'food menu'}:
        _send_main_menu_buttons(phone)
        return ''

    if any(kw in lower for kw in INTERCEPT_CONTACT):
        reply = _fmt_contact()
        _persist_reply(phone, user_message, reply, 'contact request', profile.get('name'))
        return reply

    if any(kw in lower for kw in INTERCEPT_EVENTS):
        reply = _fmt_events()
        _persist_reply(phone, user_message, reply, 'event inquiry', profile.get('name'))
        return reply

    if booking_state.get('complete'):
        draft = booking_state['draft']
        _finalize_booking(phone, booking_state, user_message)
        reply = _fmt_booking_confirm(draft)
        _persist_reply(phone, user_message, reply, 'booking', draft.get('name') or profile.get('name'))
        return reply

    if booking_state.get('draft', {}).get('active') and booking_state.get('missing'):
        reply = _fmt_booking_prompt(booking_state['missing'])
        _persist_reply(phone, user_message, reply, 'booking', profile.get('name'))
        return reply

    history = _build_claude_messages(phone)
    try:
        reply = _call_claude(history)
    except Exception:
        logger.exception('Claude generation failed for phone %s', phone)
        mood, items = recommend_items(user_message)
        if items:
            reply = f"Try {items[0]}, {items[1]}, or {items[2]}."
        elif intent == 'event inquiry':
            reply = _fmt_events()
        elif language == 'hi':
            reply = 'Namaste! Main Rex hoon. Aap kya khana ya peena pasand karenge?'
        elif language == 'te':
            reply = 'హాయ్! నేను Rex. మీరు ఏమి తినాలనుకుంటున్నారు లేదా తాగాలనుకుంటున్నారు?'
        else:
            reply = 'Hi! I\'m Rex from Ukusa Rhino. What are you in the mood for today?'

    if not reply:
        mood, items = recommend_items(user_message)
        if items:
            reply = f"Try {items[0]}, {items[1]}, or {items[2]}."
        else:
            reply = 'Hi! I\'m Rex from Ukusa Rhino. What are you in the mood for today?'

    reply = re.sub(r'\n{3,}', '\n\n', reply).strip()
    _persist_reply(phone, user_message, reply, intent, profile.get('name'))
    return reply

