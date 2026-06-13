"""Message formatting helpers for the Telegram group and parent private chats."""

GROUP_LANG = "fr"
PARENT_LANG = "en"


def _to_12h(hour, minute):
    """Convert 24h hour/minute to a 12h display string like '8:15am' or '3:30pm'."""
    period = "am" if hour < 12 else "pm"
    h = hour % 12
    if h == 0:
        h = 12
    return f"{h}:{minute:02d}{period}"


def format_schedule_display(raw):
    """Convert 'MON-FRI 08:15-15:30' to 'Mon-Fri 8:15am-3:30pm'."""
    try:
        parts = raw.strip().upper().split()
        if len(parts) != 2:
            return raw

        day_part, time_part = parts
        start_str, end_str = time_part.split("-")
        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))

        return f"{day_part.title()} {_to_12h(start_h, start_m)}-{_to_12h(end_h, end_m)}"
    except Exception:
        return raw


def format_alert_message(action, data, *, is_repeat=False, is_override=False):
    """Return the full alert message string for the group chat."""
    bg = data["bg"]
    trend_arrow = data["trend_arrow"]
    delta = data["delta"]
    previous_bgs = data.get("previous_bgs", [])

    if action.startswith("jb:"):
        n = action.split(":")[1]
        label = f"[BG basse] Donner {n}x 🍬"
        body_line = f"Senna a besoin de {n}g"
    elif action == "juicebox":
        label = "[URGENT] Donner 🧃"
        body_line = "Senna a besoin d'un jus immédiatement"
    else:
        label = f"[Alert] {action}"
        body_line = action

    if is_repeat:
        header = f"{label}\n(Demande répétée)"
    else:
        header = label

    prev_parts = []
    for i in range(3):
        prev_parts.append(f"{previous_bgs[i]:.1f}" if i < len(previous_bgs) else "?")
    prev_str = " | ".join(prev_parts)

    body = (
        f"BG: {bg:.1f} mmol/L {trend_arrow}\n"
        f"Delta: {delta:+.1f} mmol/L\n"
        f"Previous: {prev_str}"
    )

    return f"{header}\n\n{body_line}\n\n{body}"


def format_parent_reply(key, **kwargs):
    """Return an English string for parent private chat replies."""
    strings = {
        "sending_alert": "Sending alert: {action}",
        "override_cleared": "Override cleared.",
        "no_bg_data": "No BG data available yet - waiting for first poll.",
        "please_tell": "Please tell me what Senna needs.",
        "dont_understand": "Sorry, I don't understand. Please reply with what Senna needs.",
        "override_set_no_data": "Override set but no BG data available yet - alert will fire on next poll.",
        "paused_until": "Alerts paused for {duration}. Send /resume to re-enable.",
        "paused_indefinite": "Alerts paused. Send /resume to re-enable.",
        "resumed": "Alerts resumed.",
        "not_paused": "Alerts are not currently paused.",
        "pause_invalid": "Invalid duration. Try /pause 2h or /pause 30m or /pause 1h30m.",
        "schedule_current": "Current schedule: {schedule}",
        "schedule_none": "No schedule set - alerts always active.",
        "schedule_set": "Schedule updated: {schedule}",
        "schedule_cleared": "Schedule cleared - reverted to default.",
        "schedule_invalid": "Sorry, I couldn't understand that schedule. Try something like: /schedule Monday to Friday 8:15am to 3:30pm",
        "schedule_save_failed": "Failed to save schedule. Please try again.",
        "override_not_actioned": "Manual dose request ({action}) was not actioned within 5 minutes.",
    }
    if "schedule" in kwargs:
        kwargs = {**kwargs, "schedule": format_schedule_display(kwargs["schedule"])}
    return strings[key].format(**kwargs)


def format_ack_message(action, display_name):
    """Return a French acknowledgement string for the group chat."""
    if action.startswith("jb:"):
        n = action.split(":")[1]
        return f"{n}g donné par {display_name}"
    elif action == "juicebox":
        return f"Jus donné par {display_name}"
    else:
        return f"Fait - {display_name}"


def format_status_message(state, data, cooldown_remaining_seconds=None):
    """Return a status summary string for the /status command."""
    lines = [
        f"BG: {data['bg']:.1f} mmol/L {data['trend_arrow']}",
    ]

    import time as _time
    age_seconds = int(_time.time() - data.get("timestamp", _time.time()))
    age_minutes = age_seconds // 60
    lines.append(f"Age: {age_minutes} min")

    lines += [
        f"Delta: {data['delta']:+.1f} mmol/L",
        f"Active action: {state['active_action'] or 'none'}",
    ]

    if cooldown_remaining_seconds is not None:
        m = cooldown_remaining_seconds // 60
        s = cooldown_remaining_seconds % 60
        lines.append(f"Cooldown: {m}m {s}s remaining")
    else:
        lines.append("Cooldown: inactive")

    if state["override"]:
        ov = state["override"]
        lines.append(f"Override: {ov['action']} (by {ov['triggered_by']})")
    else:
        lines.append("Override: none")

    if state.get("paused"):
        pause_until = state.get("pause_until")
        if pause_until is not None:
            import time
            remaining = max(0, int(pause_until - time.time()))
            m = remaining // 60
            s = remaining % 60
            lines.append(f"Alerts: PAUSED ({m}m {s}s remaining)")
        else:
            lines.append("Alerts: PAUSED (manual resume required)")
    else:
        lines.append("Alerts: active")

    from datetime import datetime
    from zoneinfo import ZoneInfo
    import config as _config
    now_local = datetime.now(tz=ZoneInfo(_config.TIMEZONE))
    lines.append(f"Time: {now_local.strftime('%-I:%M %p')}")

    schedule = state.get("schedule")
    if schedule:
        lines.append(f"Schedule: {format_schedule_display(schedule['raw'])}")
    else:
        lines.append("Schedule: always active")

    return "\n".join(lines)
