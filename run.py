import os
from dotenv import load_dotenv

load_dotenv()

from whatsapp_bot import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print("Rex WhatsApp server starting...")
    app.run(host="0.0.0.0", port=port, debug=False)
