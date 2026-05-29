from dotenv import load_dotenv

load_dotenv()

from whatsapp_bot import app

if __name__ == "__main__":
    print("🏁 Starting Rex AI WhatsApp Server...")
    app.run(
        host="0.0.0.0",
        port=8000,
        debug=True
    )