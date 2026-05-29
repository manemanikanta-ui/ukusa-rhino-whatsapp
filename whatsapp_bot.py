import os
import requests
from flask import Flask, request, jsonify
from agent import chat

app = Flask(__name__)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "ukusa_rhino_rex")

def send_message(to: str, text: str):
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }
    requests.post(url, json=payload, headers=headers)

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        entry = data["entry"][0]["changes"][0]["value"]

        # Ignore status updates
        if "messages" not in entry:
            return jsonify({"status": "ok"}), 200

        message = entry["messages"][0]
        phone = message["from"]

        # Handle text only
        if message["type"] == "text":
            user_text = message["text"]["body"]
            reply = chat(phone, user_text)
            send_message(phone, reply)
        else:
            send_message(phone, 
                "Hey! 🏍️ I can read text messages. "
                "Type *menu*, *book a table*, or tell me what you're in the mood for!")

    except Exception as e:
        print(f"Error: {e}")

    return jsonify({"status": "ok"}), 200  # Always 200 to Meta

if __name__ == "__main__":
    app.run(port=8000, debug=False)