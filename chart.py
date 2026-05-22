# BG range boundaries (mmol/L). Gaps in the spec (7.0-8.0 and 10.0-11.0)
# are absorbed into the adjacent lower range so every reading maps to an action.

_TREND_CATEGORY = {
    "DoubleUp": "rising_rapidly",
    "SingleUp": "rising",
    "FortyFiveUp": "slow_rise",
    "Flat": "stable",
    "FortyFiveDown": "slow_fall",
    "SingleDown": "falling_rapidly",
    "DoubleDown": "falling_very_rapidly",
}

# (trend_category, bg_range) -> action string
# Empty string means no action needed.
_ACTION_TABLE = {
    ("rising_rapidly", "very_low"): "",
    ("rising_rapidly", "low"): "",
    ("rising_rapidly", "target"): "",
    ("rising_rapidly", "high"): "",
    ("rising_rapidly", "very_high"): "water",
    ("rising_rapidly", "critical"): "water",

    ("rising", "very_low"): "jb:2",
    ("rising", "low"): "",
    ("rising", "target"): "",
    ("rising", "high"): "",
    ("rising", "very_high"): "water",
    ("rising", "critical"): "water",

    ("slow_rise", "very_low"): "jb:2",
    ("slow_rise", "low"): "",
    ("slow_rise", "target"): "",
    ("slow_rise", "high"): "",
    ("slow_rise", "very_high"): "water",
    ("slow_rise", "critical"): "water",

    ("stable", "very_low"): "jb:3",
    ("stable", "low"): "jb:2",
    ("stable", "target"): "",
    ("stable", "high"): "",
    ("stable", "very_high"): "",
    ("stable", "critical"): "",

    ("slow_fall", "very_low"): "jb:4",
    ("slow_fall", "low"): "jb:2",
    ("slow_fall", "target"): "",
    ("slow_fall", "high"): "",
    ("slow_fall", "very_high"): "",
    ("slow_fall", "critical"): "",

    ("falling_rapidly", "very_low"): "juicebox",
    ("falling_rapidly", "low"): "jb:4",
    ("falling_rapidly", "target"): "jb:2",
    ("falling_rapidly", "high"): "",
    ("falling_rapidly", "very_high"): "",
    ("falling_rapidly", "critical"): "",

    ("falling_very_rapidly", "very_low"): "juicebox",
    ("falling_very_rapidly", "low"): "jb:5",
    ("falling_very_rapidly", "target"): "jb:3",
    ("falling_very_rapidly", "high"): "",
    ("falling_very_rapidly", "very_high"): "",
    ("falling_very_rapidly", "critical"): "",
}


def get_bg_range(bg_mmol):
    if bg_mmol <= 4.0:
        return "very_low"
    elif bg_mmol <= 4.8:
        return "low"
    elif bg_mmol <= 7.0:
        return "target"
    elif bg_mmol <= 10.0:
        return "high"
    elif bg_mmol <= 13.0:
        return "very_high"
    else:
        return "critical"


def get_trend_category(direction):
    return _TREND_CATEGORY.get(direction, "stable")


def get_action(bg_mmol, direction):
    """Return the action string for the given BG and Nightscout direction."""
    bg_range = get_bg_range(bg_mmol)
    trend = get_trend_category(direction)
    return _ACTION_TABLE.get((trend, bg_range), "")
