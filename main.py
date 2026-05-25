"""
Senna Dose Alert Bot - main entry point.

State machine: polls Nightscout every 5 minutes, sends Telegram alerts to the
family group chat, logs to Google Sheets, and handles inline Done acknowledgements
and parent override commands.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime

import anthropic
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import chart
import config
import nightscout
import sheets
import telegram_bot as tg

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state - mutated only inside the asyncio event loop (no locking needed)
# ---------------------------------------------------------------------------
state = {
    "active_action": None,           # str | None - current unacknowledged action
    "active_alert_message_id": None, # int | None - Telegram msg ID of live alert
    "cooldown_until": None,          # float | None - unix timestamp
    "override": None,                # dict | None - {action, triggered_by}
    "last_alerted_action": None,     # str | None - for cooldown severity comparison
    "current_sheet_row": None,       # int | None - 1-based sheet row of live alert
    "last_alert_time": None,         # float | None - when last alert was sent
    "last_bg_data": None,            # dict | None - most recent fetch result
    "paused": False,                 # bool - whether alerts are paused
    "pause_until": None,             # float | None - unix timestamp to auto-resume
    "schedule": None,                # loaded on startup
}

# ---------------------------------------------------------------------------
# Severity ordering for escalation checks
# ---------------------------------------------------------------------------
_SEVERITY = {"": 0, "water": 1, "jb:2": 2, "jb:3": 3, "jb:4": 4, "jb:5": 5, "juicebox": 6}

_VALID_OVERRIDE_ACTIONS = {"water", "jb:2", "jb:3", "jb:4", "jb:5", "juicebox"}


def _sev(action):
    return _SEVERITY.get(action or "", 0)


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

_DAY_MAP = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


def parse_schedule_string(raw):
    """Parse 'MON-FRI 08:15-15:30' or 'MON,WED,FRI 08:15-15:30'.

    Returns dict with keys: enabled, days, start, end, raw
    Returns None if parsing fails.
    """
    try:
        parts = raw.strip().upper().split()
        if len(parts) != 2:
            return None

        day_part, time_part = parts

        if "-" in day_part:
            start_day, end_day = day_part.split("-")
            start_idx = _DAY_MAP[start_day]
            end_idx = _DAY_MAP[end_day]
            days = list(range(start_idx, end_idx + 1))
        elif "," in day_part:
            days = [_DAY_MAP[d] for d in day_part.split(",")]
        else:
            days = [_DAY_MAP[day_part]]

        start_str, end_str = time_part.split("-")
        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))

        return {
            "enabled": True,
            "days": days,
            "start": (start_h, start_m),
            "end": (end_h, end_m),
            "raw": raw.strip(),
        }
    except Exception:
        log.error(f"Failed to parse schedule string: {raw!r}", exc_info=True)
        return None


def load_schedule():
    """Load schedule from schedule.json if it exists, else fall back to config.SCHEDULE.

    Returns a parsed schedule dict, or None if scheduling is disabled.
    """
    raw = None
    if os.path.exists(config.SCHEDULE_FILE):
        try:
            with open(config.SCHEDULE_FILE) as f:
                data = json.load(f)
                raw = data.get("schedule")
                log.info(f"Loaded schedule from file: {raw}")
        except Exception:
            log.error("Failed to load schedule.json - falling back to config", exc_info=True)

    if raw is None:
        raw = config.SCHEDULE

    if not raw or not raw.strip():
        return None

    return parse_schedule_string(raw)


def save_schedule(raw):
    """Save schedule string to schedule.json."""
    try:
        with open(config.SCHEDULE_FILE, "w") as f:
            json.dump({"schedule": raw}, f)
        log.info(f"Schedule saved to file: {raw}")
    except Exception:
        log.error("Failed to save schedule.json", exc_info=True)
        raise


def is_within_schedule(schedule):
    """Return True if current time falls within the schedule window."""
    if schedule is None:
        return True

    now = datetime.now()
    current_day = now.weekday()
    current_minutes = now.hour * 60 + now.minute
    start_minutes = schedule["start"][0] * 60 + schedule["start"][1]
    end_minutes = schedule["end"][0] * 60 + schedule["end"][1]

    return current_day in schedule["days"] and start_minutes <= current_minutes < end_minutes


# ---------------------------------------------------------------------------
# Alert delivery
# ---------------------------------------------------------------------------

async def _send_alert(bot, action, data, *, is_repeat=False):
    """Send an alert to the group and log it to Sheets. Updates shared state."""
    is_override = state["override"] is not None
    trigger_type = "override" if is_override else "chart"
    triggered_by = state["override"]["triggered_by"] if is_override else "chart"

    text = tg.format_alert_message(
        action, data, is_repeat=is_repeat, is_override=is_override
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Fait", callback_data="done")]]
    )

    # Telegram send is critical - let exception propagate so the caller can log it
    sent = await bot.send_message(
        chat_id=config.TELEGRAM_GROUP_ID,
        text=text,
        reply_markup=keyboard,
    )

    # Update state immediately after successful send
    state["active_action"] = action
    state["active_alert_message_id"] = sent.message_id
    state["last_alerted_action"] = action
    state["last_alert_time"] = time.time()
    state["current_sheet_row"] = None  # Set below after sheet write

    log.info(
        f"Alert sent: action={action} repeat={is_repeat} override={is_override} "
        f"msg_id={sent.message_id}"
    )

    # Sheet logging is non-critical - failure must not suppress dose delivery
    try:
        row_index = await asyncio.to_thread(
            sheets.log_alert, data, action, is_repeat, trigger_type, triggered_by
        )
        state["current_sheet_row"] = row_index
    except Exception:
        log.error(
            "ALERT SENT TO TELEGRAM but Google Sheets logging FAILED - "
            "acknowledgement will not be recorded in sheet",
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Scheduled BG poll (runs every 5 minutes via job_queue)
# ---------------------------------------------------------------------------

async def bg_poll(context):
    """Fetch BG and evaluate whether a dose action is needed."""
    try:
        data = await asyncio.to_thread(nightscout.fetch_bg)
        state["last_bg_data"] = data
    except Exception:
        log.error("Failed to fetch BG from Nightscout", exc_info=True)
        return

    # Check if timed pause has expired
    if state["paused"] and state["pause_until"] is not None:
        if time.time() >= state["pause_until"]:
            state["paused"] = False
            state["pause_until"] = None
            log.info("Pause expired - resuming alerts")

    # Check schedule - out-of-schedule silently skips unless manually overridden
    if not state["paused"] and not is_within_schedule(state["schedule"]):
        return

    # Skip alert logic if paused
    if state["paused"]:
        return

    chart_action = chart.get_action(data["bg"], data["direction"])
    effective_action = (
        state["override"]["action"] if state["override"] else chart_action
    )

    if effective_action == "water":
        effective_action = ""

    now = time.time()
    cooldown_active = (
        state["cooldown_until"] is not None and now < state["cooldown_until"]
    )

    if cooldown_active:
        if _sev(effective_action) > _sev(state["last_alerted_action"]):
            log.info(
                f"Cooldown broken by escalation: "
                f"{state['last_alerted_action']} -> {effective_action}"
            )
            state["cooldown_until"] = None
            # Fall through to send new alert
        else:
            return  # Stay quiet during cooldown

    if effective_action != state["active_action"]:
        if effective_action:
            try:
                await _send_alert(context.bot, effective_action, data)
            except Exception:
                log.error(
                    f"Failed to send alert for action={effective_action}", exc_info=True
                )
        else:
            if state["active_action"]:
                log.info(
                    f"Action '{state['active_action']}' resolved without acknowledgement"
                )
            state["active_action"] = None
            state["active_alert_message_id"] = None
    else:
        # Same action - check for repeat
        if state["active_action"] and state["last_alert_time"]:
            elapsed = now - state["last_alert_time"]
            if elapsed >= 300:
                log.info(
                    f"Repeat alert for {state['active_action']} "
                    f"({elapsed:.0f}s since last alert)"
                )
                try:
                    await _send_alert(context.bot, effective_action, data, is_repeat=True)
                except Exception:
                    log.error(
                        f"Failed to send repeat alert for action={effective_action}",
                        exc_info=True,
                    )


# ---------------------------------------------------------------------------
# Inline keyboard - Done button
# ---------------------------------------------------------------------------

async def on_done(update, context):
    """Handle the Done inline keyboard button press."""
    query = update.callback_query
    await query.answer()

    msg_id = query.message.message_id

    if msg_id != state["active_alert_message_id"]:
        # Old or already-cleared alert
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        log.info(f"Done pressed on non-active message {msg_id} - ignored")
        return

    user = query.from_user
    display_name = (
        f"@{user.username}" if user.username else (user.first_name or str(user.id))
    )
    sheet_name = user.username or user.first_name or str(user.id)

    now = time.time()
    alert_time = state["last_alert_time"] or now
    response_minutes = (now - alert_time) / 60

    # Update sheet row (non-critical)
    row_index = state["current_sheet_row"]
    if row_index:
        try:
            await asyncio.to_thread(
                sheets.acknowledge_alert,
                row_index, sheet_name, now, response_minutes, True,
            )
        except Exception:
            log.error("Failed to record acknowledgement in Google Sheets", exc_info=True)
    else:
        log.warning("No sheet row index available - acknowledgement not recorded in sheet")

    # Remove the Done button from the alert message
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        log.warning("Failed to remove Done button from alert message", exc_info=True)

    # Send confirmation reply in the group
    action = state["last_alerted_action"] or ""
    reply_text = tg.format_ack_message(action, display_name)
    try:
        await context.bot.send_message(
            chat_id=config.TELEGRAM_GROUP_ID,
            text=reply_text,
            reply_to_message_id=msg_id,
        )
    except Exception:
        log.error("Failed to send acknowledgement reply to group", exc_info=True)

    log.info(f"Alert acknowledged by {display_name} after {response_minutes:.1f} min")

    # Update state - order matters: clear active before setting cooldown
    state["active_action"] = None
    state["active_alert_message_id"] = None
    state["override"] = None
    state["cooldown_until"] = now + 900


# ---------------------------------------------------------------------------
# Natural language intent parsing
# ---------------------------------------------------------------------------

async def parse_dose_intent(text):
    """Use Claude API to parse a natural language dose request.

    Returns a valid action string: jb:2, jb:3, jb:4, jb:5, water, juicebox, clear
    Returns None if intent cannot be determined confidently.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    system_prompt = """You are a parser for a medical glucose management system for a diabetic child named Senna.
Your job is to parse natural language requests from parents into one of these exact action codes:
- jb:2 (give 2 jellybeans / 2g)
- jb:3 (give 3 jellybeans / 3g)
- jb:4 (give 4 jellybeans / 4g)
- jb:5 (give 5 jellybeans / 5g)
- water (give water, for high blood sugar)
- juicebox (give juice box, urgent low blood sugar)
- clear (cancel/clear the current override)

Requests may be in English or French.
Respond with ONLY the action code and nothing else.
If you cannot confidently determine the intent, respond with UNKNOWN."""

    try:
        response = await asyncio.to_thread(
            lambda: client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=10,
                system=system_prompt,
                messages=[{"role": "user", "content": text}],
            )
        )
        result = response.content[0].text.strip()
        valid = {"jb:2", "jb:3", "jb:4", "jb:5", "water", "juicebox", "clear"}
        return result if result in valid else None
    except Exception:
        log.error("Failed to parse dose intent via Anthropic API", exc_info=True)
        return None


async def trigger_immediate_alert(bot, action, username):
    """Set an override and immediately send an alert using last known BG data."""
    state["override"] = {"action": action, "triggered_by": username}

    data = state["last_bg_data"]
    if data is None:
        log.warning("Immediate alert requested but no BG data available yet")
        return False

    # Cancel cooldown - parent override always goes through
    state["cooldown_until"] = None

    try:
        await _send_alert(bot, action, data)
        return True
    except Exception:
        log.error(f"Failed to send immediate alert for action={action}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Parent private chat commands
# ---------------------------------------------------------------------------

def _is_parent(update):
    return update.effective_chat.id in config.TELEGRAM_PARENT_IDS


async def on_dose(update, context):
    """Handle /dose <natural language> from authorized parents."""
    if not _is_parent(update):
        return

    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(tg.format_parent_reply("please_tell"))
        return

    user = update.effective_user
    username = user.username or user.first_name or str(user.id)

    action = await parse_dose_intent(text)

    if action is None:
        await update.message.reply_text(tg.format_parent_reply("dont_understand"))
        return

    if action == "clear":
        state["override"] = None
        state["active_action"] = None
        await update.message.reply_text(tg.format_parent_reply("override_cleared"))
        log.info(f"Override cleared by {username} via /dose")
        return

    success = await trigger_immediate_alert(update.get_bot(), action, username)
    if success:
        await update.message.reply_text(tg.format_parent_reply("sending_alert", action=action))
    else:
        await update.message.reply_text(tg.format_parent_reply("override_set_no_data"))
        state["override"] = {"action": action, "triggered_by": username}


async def on_override(update, context):
    """Handle /override <natural language> from authorized parents.
    Identical behaviour to /dose."""
    if not _is_parent(update):
        return

    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(tg.format_parent_reply("please_tell"))
        return

    user = update.effective_user
    username = user.username or user.first_name or str(user.id)

    action = await parse_dose_intent(text)

    if action is None:
        await update.message.reply_text(tg.format_parent_reply("dont_understand"))
        return

    if action == "clear":
        state["override"] = None
        state["active_action"] = None
        await update.message.reply_text(tg.format_parent_reply("override_cleared"))
        log.info(f"Override cleared by {username} via /override")
        return

    success = await trigger_immediate_alert(update.get_bot(), action, username)
    if success:
        await update.message.reply_text(tg.format_parent_reply("sending_alert", action=action))
    else:
        await update.message.reply_text(tg.format_parent_reply("override_set_no_data"))
        state["override"] = {"action": action, "triggered_by": username}


async def on_pause(update, context):
    """Handle /pause [duration] from authorized parents."""
    if not _is_parent(update):
        return

    import re
    args = context.args
    user = update.effective_user
    username = user.username or user.first_name or str(user.id)

    pause_until = None
    duration_str = None

    if args:
        raw = args[0].strip().lower()
        match = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m)?', raw)
        if match and (match.group(1) or match.group(2)):
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2) or 0)
            total_seconds = hours * 3600 + minutes * 60
            if total_seconds > 0:
                pause_until = time.time() + total_seconds
                if hours and minutes:
                    duration_str = f"{hours}h {minutes}m"
                elif hours:
                    duration_str = f"{hours}h"
                else:
                    duration_str = f"{minutes}m"

        if pause_until is None:
            await update.message.reply_text(tg.format_parent_reply("pause_invalid"))
            return

    state["paused"] = True
    state["pause_until"] = pause_until

    if duration_str:
        await update.message.reply_text(
            tg.format_parent_reply("paused_until", duration=duration_str)
        )
        log.info(f"Alerts paused for {duration_str} by {username}")
    else:
        await update.message.reply_text(tg.format_parent_reply("paused_indefinite"))
        log.info(f"Alerts paused indefinitely by {username}")


async def on_resume(update, context):
    """Handle /resume from authorized parents."""
    if not _is_parent(update):
        return

    user = update.effective_user
    username = user.username or user.first_name or str(user.id)

    if not state["paused"]:
        await update.message.reply_text(tg.format_parent_reply("not_paused"))
        return

    state["paused"] = False
    state["pause_until"] = None
    await update.message.reply_text(tg.format_parent_reply("resumed"))
    log.info(f"Alerts resumed by {username}")


async def parse_schedule_intent(text):
    """Use Claude API to parse a natural language schedule request.

    Returns an internal schedule string like 'MON-FRI 08:15-15:30',
    'clear' to disable the saved schedule, or None if intent cannot be determined.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    system_prompt = """You are a parser for a scheduling system. Convert natural language schedule descriptions into a strict format.

Output format: DAY_RANGE HH:MM-HH:MM
- Days use 3-letter abbreviations: MON TUE WED THU FRI SAT SUN
- Day range uses hyphen: MON-FRI
- Non-consecutive days use comma: MON,WED,FRI
- Times use 24-hour format: 08:15-15:30

Examples:
- "Monday to Friday 8:15am to 3:30pm" -> MON-FRI 08:15-15:30
- "weekdays 8h15 à 15h30" -> MON-FRI 08:15-15:30
- "lundi au vendredi 8h15 à 15h30" -> MON-FRI 08:15-15:30
- "Monday Wednesday Friday 9am to 4pm" -> MON,WED,FRI 09:00-16:00
- "every day 7am to 8pm" -> MON-SUN 07:00-20:00

If the user wants to clear/disable the custom schedule, output exactly: clear
If you cannot parse the request confidently, output exactly: UNKNOWN

Respond with ONLY the output string, nothing else."""

    try:
        response = await asyncio.to_thread(
            lambda: client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=20,
                system=system_prompt,
                messages=[{"role": "user", "content": text}],
            )
        )
        result = response.content[0].text.strip()

        if result == "clear":
            return "clear"
        if result == "UNKNOWN":
            return None

        parsed = parse_schedule_string(result)
        return result if parsed is not None else None
    except Exception:
        log.error("Failed to parse schedule intent via Anthropic API", exc_info=True)
        return None


async def on_schedule(update, context):
    """Handle /schedule [natural language] from authorized parents."""
    if not _is_parent(update):
        return

    user = update.effective_user
    username = user.username or user.first_name or str(user.id)
    text = " ".join(context.args).strip()

    if not text:
        schedule = state["schedule"]
        if schedule:
            await update.message.reply_text(
                tg.format_parent_reply("schedule_current", schedule=schedule["raw"])
            )
        else:
            await update.message.reply_text(tg.format_parent_reply("schedule_none"))
        return

    result = await parse_schedule_intent(text)

    if result is None:
        await update.message.reply_text(tg.format_parent_reply("schedule_invalid"))
        return

    if result == "clear":
        try:
            if os.path.exists(config.SCHEDULE_FILE):
                os.remove(config.SCHEDULE_FILE)
            state["schedule"] = parse_schedule_string(config.SCHEDULE) if config.SCHEDULE else None
            await update.message.reply_text(tg.format_parent_reply("schedule_cleared"))
            log.info(f"Schedule cleared by {username} - reverted to config default")
        except Exception:
            await update.message.reply_text(tg.format_parent_reply("schedule_save_failed"))
        return

    parsed = parse_schedule_string(result)
    try:
        save_schedule(result)
        state["schedule"] = parsed
        await update.message.reply_text(
            tg.format_parent_reply("schedule_set", schedule=result)
        )
        log.info(f"Schedule set to {result!r} by {username}")
    except Exception:
        await update.message.reply_text(tg.format_parent_reply("schedule_save_failed"))


async def on_status(update, context):
    """Handle /status from authorized parents."""
    if not _is_parent(update):
        return

    data = state["last_bg_data"]
    if data is None:
        await update.message.reply_text(tg.format_parent_reply("no_bg_data"))
        return

    now = time.time()
    cooldown_remaining = None
    if state["cooldown_until"] and now < state["cooldown_until"]:
        cooldown_remaining = int(state["cooldown_until"] - now)

    await update.message.reply_text(
        tg.format_status_message(state, data, cooldown_remaining)
    )


async def on_help(update, context):
    """Handle /help from authorized parents."""
    if not _is_parent(update):
        return

    await update.message.reply_text(
        "/status - current BG, trend, active action, cooldown and override status\n"
        "/dose <what Senna needs> - send a dose request (e.g. /dose 2 jellybeans)\n"
        "/override <what Senna needs> - same as /dose\n"
        "/dose clear - cancel current override\n"
        "/pause - pause all alerts indefinitely\n"
        "/pause 2h - pause alerts for 2 hours (supports 30m, 1h, 1h30m etc.)\n"
        "/resume - resume alerts\n"
        "/schedule - show current schedule\n"
        "/schedule <when> - set schedule in plain English or French\n"
        "/schedule clear - revert to default schedule\n"
        "/help - this message"
    )


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

def build_app():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CallbackQueryHandler(on_done, pattern="^done$"))
    app.add_handler(CommandHandler("dose", on_dose))
    app.add_handler(CommandHandler("override", on_override))
    app.add_handler(CommandHandler("pause", on_pause))
    app.add_handler(CommandHandler("resume", on_resume))
    app.add_handler(CommandHandler("schedule", on_schedule))
    app.add_handler(CommandHandler("status", on_status))
    app.add_handler(CommandHandler("help", on_help))

    # First poll after 10 seconds, then every 5 minutes
    app.job_queue.run_repeating(bg_poll, interval=300, first=10)

    state["schedule"] = load_schedule()
    if state["schedule"]:
        log.info(f"Schedule loaded: {state['schedule']['raw']}")
    else:
        log.info("No schedule configured - alerts always active")

    return app


if __name__ == "__main__":
    log.info("Starting Senna Dose Alert Bot")
    app = build_app()
    app.run_polling(drop_pending_updates=True)
