"""
Senna Dose Alert Bot - main entry point.

State machine: polls Nightscout every 5 minutes, sends Telegram alerts to the
family group chat, logs to Google Sheets, and handles inline Done acknowledgements
and parent override commands.
"""

import asyncio
import logging
import time

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
}

# ---------------------------------------------------------------------------
# Severity ordering for escalation checks
# ---------------------------------------------------------------------------
_SEVERITY = {"": 0, "water": 1, "jb:2": 2, "jb:3": 3, "jb:4": 4, "jb:5": 5, "juicebox": 6}

_VALID_OVERRIDE_ACTIONS = {"water", "jb:2", "jb:3", "jb:4", "jb:5", "juicebox"}


def _sev(action):
    return _SEVERITY.get(action or "", 0)


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
        [[InlineKeyboardButton("✅ Done", callback_data="done")]]
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

    chart_action = chart.get_action(data["bg"], data["direction"])
    effective_action = (
        state["override"]["action"] if state["override"] else chart_action
    )

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
    if action.startswith("jb:"):
        n = action.split(":")[1]
        reply_text = f"{n}g given by {display_name}"
    elif action == "water":
        reply_text = f"Water given by {display_name}"
    elif action == "juicebox":
        reply_text = f"Juice box given by {display_name}"
    else:
        reply_text = f"Done - {display_name}"
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
# Parent private chat commands
# ---------------------------------------------------------------------------

def _is_parent(update):
    return update.effective_chat.id in config.TELEGRAM_PARENT_IDS


async def on_override(update, context):
    """Handle /override <action|clear> from authorized parents."""
    if not _is_parent(update):
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /override <action> or /override clear\n"
            "Valid actions: water, jb:2, jb:3, jb:4, jb:5, juicebox"
        )
        return

    arg = args[0].strip().lower()
    user = update.effective_user
    username = user.username or user.first_name or str(user.id)

    if arg == "clear":
        state["override"] = None
        await update.message.reply_text("Override cleared.")
        log.info(f"Override cleared by {username}")
    elif arg in _VALID_OVERRIDE_ACTIONS:
        state["override"] = {"action": arg, "triggered_by": username}
        await update.message.reply_text(f"Override set: {arg}")
        log.info(f"Override set to {arg!r} by {username}")
    else:
        await update.message.reply_text(
            f"Unknown action '{arg}'.\n"
            "Valid: water, jb:2, jb:3, jb:4, jb:5, juicebox"
        )


async def on_status(update, context):
    """Handle /status from authorized parents."""
    if not _is_parent(update):
        return

    data = state["last_bg_data"]
    if data is None:
        await update.message.reply_text("No BG data available yet - waiting for first poll.")
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
        "/override <action> - force an action (water, jb:2, jb:3, jb:4, jb:5, juicebox)\n"
        "/override clear - remove manual override\n"
        "/help - this message"
    )


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

def build_app():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CallbackQueryHandler(on_done, pattern="^done$"))
    app.add_handler(CommandHandler("override", on_override))
    app.add_handler(CommandHandler("status", on_status))
    app.add_handler(CommandHandler("help", on_help))

    # First poll after 10 seconds, then every 5 minutes
    app.job_queue.run_repeating(bg_poll, interval=300, first=10)

    return app


if __name__ == "__main__":
    log.info("Starting Senna Dose Alert Bot")
    app = build_app()
    app.run_polling(drop_pending_updates=True)
