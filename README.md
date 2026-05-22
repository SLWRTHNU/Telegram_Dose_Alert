# Senna Dose Alert Bot

A Telegram bot that polls Nightscout every 5 minutes and sends dose action alerts
to a family group chat when Senna's blood glucose needs attention. Actions are logged
to Google Sheets for review.

---

## Quick start

### 1. Clone and set up a virtual environment

```bash
git clone https://github.com/SLWRTHNU/Telegram_Dose_Alert.git /opt/dose-alert
cd /opt/dose-alert
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Create the .env file

```bash
cp .env.example .env   # or create from scratch - see below
nano /opt/dose-alert/.env
```

Minimum required content:

```
NIGHTSCOUT_TOKEN=your_nightscout_token
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_GROUP_ID=-1003940576515
TELEGRAM_PARENT_IDS=8708269794
GOOGLE_CREDENTIALS_JSON=/opt/dose-alert/credentials.json
```

Optional overrides (defaults shown):

```
NIGHTSCOUT_URL=https://sennaloop-673ad2782247.herokuapp.com
GOOGLE_SHEET_NAME=Dose Log
GOOGLE_DRIVE_FOLDER_ID=
```

> **Note on the Telegram bot token:** The token in config.py is a placeholder.
> Always set TELEGRAM_BOT_TOKEN in your .env to the real token from BotFather.
> The first 10 digits in the spec had two slightly different versions (8084... vs 8804...) -
> use whatever BotFather gave you.

---

## Telegram setup

### Create a bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the token BotFather gives you - this is your `TELEGRAM_BOT_TOKEN`.

### Get the group chat ID

1. Add the bot to your family group.
2. Send a message in the group.
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser.
4. Find the `"chat": {"id": ...}` field - the group ID will be a negative number.

### Get parent chat IDs

1. Start a private chat with your bot and send any message.
2. Check `getUpdates` as above - each user's `"from": {"id": ...}` is their chat ID.
3. List multiple parents as comma-separated values: `TELEGRAM_PARENT_IDS=111,222`

### Parent commands (private chat only)

| Command | Description |
|---|---|
| `/status` | Current BG, trend, active action, cooldown and override |
| `/override water` | Force a water alert |
| `/override jb:2` | Force a jellybean alert (2g) |
| `/override juicebox` | Force a juice box alert |
| `/override clear` | Remove manual override |
| `/help` | List commands |

---

## Google Sheets setup

### Create a Google Cloud project

1. Go to <https://console.cloud.google.com> and create a new project.
2. Enable the **Google Sheets API** and **Google Drive API** for the project.

### Create a service account

1. In the Cloud Console go to **IAM & Admin > Service Accounts**.
2. Click **Create Service Account**, give it a name (e.g. `dose-alert-bot`).
3. No role is needed at the project level - click through to finish.

### Download credentials.json

1. Click the service account you just created.
2. Go to the **Keys** tab and click **Add Key > Create new key**.
3. Choose **JSON** - download and save it as `credentials.json` in `/opt/dose-alert/`.
4. Set `GOOGLE_CREDENTIALS_JSON=/opt/dose-alert/credentials.json` in your .env.

### Create and share the Dose Log sheet

The bot creates the spreadsheet automatically on first run. To view it yourself:

1. Note the service account email (it looks like `dose-alert-bot@your-project.iam.gserviceaccount.com`).
2. After the bot creates the sheet, open Google Drive and find **Dose Log**.
3. Share it with your personal Google account (Editor or Viewer).

Alternatively, create the sheet manually:
1. Create a new Google Sheet named exactly **Dose Log** (or whatever `GOOGLE_SHEET_NAME` is set to).
2. Share it with the service account email (Editor access).
3. The bot will add the column headers on first write.

### Sheet columns

| Col | Name | Notes |
|---|---|---|
| 1 | Alert Timestamp | When alert was sent |
| 2 | Trigger Type | `chart` or `override` |
| 3 | Triggered By | `chart`, or parent username |
| 4 | Action Requested | e.g. `jb:3`, `water`, `juicebox` |
| 5 | BG at Alert | mmol/L |
| 6 | Trend Arrow | Arrow character |
| 7 | Delta | mmol/L, signed |
| 8 | BG -5min | mmol/L |
| 9 | BG -10min | mmol/L |
| 10 | BG -15min | mmol/L |
| 11 | Acknowledged By | Telegram username |
| 12 | Acknowledged At | Timestamp |
| 13 | Response Time | Minutes |
| 14 | Cooldown Triggered | yes/no |
| 15 | Was Repeat Alert | yes/no |
| 16 | Notes | Override reason if applicable |

---

## Deploy as a systemd service

```bash
sudo cp /opt/dose-alert/dose-alert.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable dose-alert
sudo systemctl start dose-alert
```

### Check status and logs

```bash
sudo systemctl status dose-alert
journalctl -u dose-alert -f
```

### Restart after code changes

```bash
sudo systemctl restart dose-alert
```

---

## Alert format

### Jellybean alert

```
Senna needs 3g
(3x jellybeans)

BG: 4.2 mmol/L
Delta: -0.3 mmol/L
Previous: 4.5 | 4.8 | 5.0

[Done button]
```

### Juice box alert (urgent)

```
Senna needs juice box - urgent

BG: 3.7 mmol/L
Delta: -0.6 mmol/L
Previous: 4.3 | 4.7 | 5.1

[Done button]
```

Repeat alerts append `(repeat)` to the first line. Manual override alerts append `(manual)`.

---

## Cooldown and repeat logic

- After Done is pressed: 15-minute cooldown. No new alerts for the same or lower severity.
- If BG escalates during cooldown (e.g. jb:3 -> juicebox), cooldown is cancelled immediately.
- If an alert is not acknowledged within 10 minutes: a repeat alert is sent automatically.

---

## Known limitations

- **State is in memory only.** If the bot restarts while an alert is active, the Done button
  on the old Telegram message will not register an acknowledgement (no active alert in state).
  The next BG poll (within 5 minutes) will send a fresh alert if needed.
- **Python 3.9+ required** (`asyncio.to_thread` used for blocking I/O).
