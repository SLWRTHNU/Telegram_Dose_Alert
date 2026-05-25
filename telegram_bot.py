"""Message formatting helpers for the Telegram group and parent private chats."""

GROUP_LANG = "fr"
PARENT_LANG = "en"


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
    }
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

    return "\n".join(lines)
