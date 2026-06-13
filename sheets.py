import datetime
import logging

import gspread

import config

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Alert Timestamp",
    "Trigger Type",
    "Triggered By",
    "Action Requested",
    "BG at Alert (mmol/L)",
    "Trend Arrow",
    "Delta (mmol/L)",
    "BG -5min (mmol/L)",
    "BG -10min (mmol/L)",
    "BG -15min (mmol/L)",
    "Acknowledged By",
    "Acknowledged At",
    "Response Time (min)",
    "Cooldown Triggered",
    "Was Repeat Alert",
    "Notes",
]

_sheet_cache = None


def _get_sheet():
    global _sheet_cache
    if _sheet_cache is not None:
        return _sheet_cache

    gc = gspread.service_account(filename=config.GOOGLE_CREDENTIALS_JSON)

    try:
        spreadsheet = gc.open(config.GOOGLE_SHEET_NAME)
        log.info(f"Opened existing spreadsheet: {config.GOOGLE_SHEET_NAME}")
    except gspread.exceptions.SpreadsheetNotFound:
        spreadsheet = gc.create(
            config.GOOGLE_SHEET_NAME,
            folder_id=config.GOOGLE_DRIVE_FOLDER_ID,
        )
        log.info(f"Created new spreadsheet: {config.GOOGLE_SHEET_NAME}")

    sheet = spreadsheet.sheet1

    # Write headers if the sheet is empty
    if not sheet.row_values(1):
        sheet.insert_row(HEADERS, 1)
        log.info("Wrote column headers to sheet")

    _sheet_cache = sheet
    return sheet


def _fmt_ts(unix_seconds):
    return datetime.datetime.fromtimestamp(unix_seconds).strftime("%Y-%m-%d %H:%M:%S")


def _invalidate_cache():
    global _sheet_cache
    _sheet_cache = None


def log_alert(data, action, is_repeat, trigger_type, triggered_by):
    """Append a new alert row. Returns the 1-based sheet row index of the new row."""
    try:
        sheet = _get_sheet()
    except Exception:
        _invalidate_cache()
        raise

    prev = data.get("previous_bgs", [])
    notes = f"Override by {triggered_by}" if trigger_type == "override" else ""

    row = [
        _fmt_ts(data["timestamp"]),
        trigger_type,
        triggered_by,
        action,
        f"{data['bg']:.1f}",
        data["trend_arrow"],
        f"{data['delta']:+.1f}",
        f"{prev[0]:.1f}" if len(prev) > 0 else "",
        f"{prev[1]:.1f}" if len(prev) > 1 else "",
        f"{prev[2]:.1f}" if len(prev) > 2 else "",
        "",  # Acknowledged By
        "",  # Acknowledged At
        "",  # Response Time
        "",  # Cooldown Triggered
        "yes" if is_repeat else "no",
        notes,
    ]

    try:
        sheet.append_row(row, value_input_option="USER_ENTERED")
        row_index = len(sheet.get_all_values())
        log.info(f"Logged alert to sheet row {row_index}: action={action}")
        return row_index
    except Exception:
        _invalidate_cache()
        raise


def acknowledge_alert(row_index, username, ack_time, response_minutes, cooldown_triggered):
    """Fill acknowledgement columns in an existing alert row."""
    try:
        sheet = _get_sheet()
    except Exception:
        _invalidate_cache()
        raise

    try:
        sheet.update_cell(row_index, 11, username)
        sheet.update_cell(row_index, 12, _fmt_ts(ack_time))
        sheet.update_cell(row_index, 13, f"{response_minutes:.1f}")
        sheet.update_cell(row_index, 14, "yes" if cooldown_triggered else "no")
        log.info(f"Acknowledged sheet row {row_index} by {username} ({response_minutes:.1f} min)")
    except Exception:
        _invalidate_cache()
        raise


def mark_not_actioned(row_index):
    """Mark an alert row as not actioned (sets Cooldown Triggered column to 'no')."""
    try:
        sheet = _get_sheet()
    except Exception:
        _invalidate_cache()
        raise

    try:
        sheet.update_cell(row_index, 14, "no")
        log.info(f"Marked sheet row {row_index} as not actioned")
    except Exception:
        _invalidate_cache()
        raise
