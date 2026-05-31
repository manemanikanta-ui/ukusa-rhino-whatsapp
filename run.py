from dotenv import load_dotenv

load_dotenv()

from whatsapp_bot import app

if __name__ == '__main__':
    print('Rex WhatsApp server starting...')
    app.run(
        host='0.0.0.0',
        port=8000,
        debug=False,
    )
