import os

NIGHTSCOUT_URL = os.environ.get(
    "NIGHTSCOUT_URL", "https://sennaloop-673ad2782247.herokuapp.com"
)
NIGHTSCOUT_TOKEN = os.environ.get("NIGHTSCOUT_TOKEN", "")

# NOTE: The bot token default below is from the CONFIG spec section.
# Always set TELEGRAM_BOT_TOKEN in your .env to the token from BotFather.
TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", "8804180961:AAGFCUx_6oAjdLJyvXKQ7yFxWLSZaLwFfzY"
)
TELEGRAM_GROUP_ID = int(os.environ.get("TELEGRAM_GROUP_ID", "-1003940576515"))
TELEGRAM_PARENT_IDS = [
    int(x.strip())
    for x in os.environ.get("TELEGRAM_PARENT_IDS", "8708269794").split(",")
    if x.strip()
]

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "credentials.json")
GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "Dose Log")
GOOGLE_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", None)
