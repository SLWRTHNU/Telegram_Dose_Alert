"""Message formatting helpers for the Telegram group and parent private chats."""


def format_alert_message(action, data, *, is_repeat=False, is_override=False):
    """Return the full alert message string for the group chat."""
    bg = data["bg"]
    trend_arrow = data["trend_arrow"]
    delta = data["delta"]
    previous_bgs = data.get("previous_bgs", [])

    if action.startswith("jb:"):
        n = action.split(":")[1]
        label = f"[Low BG] Give {n}x 🍬"
        body_line = f"Senna needs {n}g"
    elif action == "water":
        label = "[High BG] Drink 💧"
        body_line = "Senna needs water"
    elif action == "juicebox":
        label = "[URGENT] Give 🧃"
        body_line = "Senna needs juice box immediately"
    else:
        label = f"[Alert] {action}"
        body_line = action

    if is_repeat:
        header = f"{label}\n(Repeat request)"
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

    return "\n".join(lines)
