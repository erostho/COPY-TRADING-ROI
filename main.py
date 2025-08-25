import os
import sys
import time
import math
import logging
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
import requests

# ---------- Config ----------
API_BASE = "https://www.myfxbook.com/api"
EMAIL = os.getenv("MYFXBOOK_EMAIL")
PASSWORD = os.getenv("MYFXBOOK_PASSWORD")
PREFERRED_ACCOUNT_ID = os.getenv("MYFXBOOK_ACCOUNT_ID")  # optional
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TZ = os.getenv("TZ", "Asia/Ho_Chi_Minh")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ---------- Helpers ----------
def dt_today():
    # Myfxbook API expects YYYY-MM-DD in account timezone; we use local date
    return date.today()

def ymd(d: date) -> str:
    return d.strftime("%Y-%m-%d")

def compound_gain_percent(daily_list):
    """
    daily_list: [{'date':'YYYY-MM-DD','value': <percent>}, ...]
    Return compounded % gain over the list.
    """
    total = 1.0
    for item in daily_list:
        try:
            r = float(item.get("value", 0.0))
        except Exception:
            r = 0.0
        total *= (1.0 + r / 100.0)
    return (total - 1.0) * 100.0

def api_login():
    if not EMAIL or not PASSWORD:
        raise RuntimeError("Missing MYFXBOOK_EMAIL or MYFXBOOK_PASSWORD")
    url = f"{API_BASE}/login.json"
    r = requests.get(url, params={"email": EMAIL, "password": PASSWORD}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("error"):
        session = data.get("session")
        if not session:
            raise RuntimeError("Login ok but no session returned")
        return session
    raise RuntimeError(f"Login error: {data.get('message')}")

def api_call(path, params):
    url = f"{API_BASE}/{path}"
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data.get("message"))
    return data

def pick_account_id(session):
    # If user specified, validate it exists; else pick the first account
    data = api_call("get-my-accounts.json", {"session": session})
    accounts = data.get("accounts", [])
    if not accounts:
        raise RuntimeError("No accounts found in Myfxbook.")
    if PREFERRED_ACCOUNT_ID:
        for acc in accounts:
            if str(acc.get("id")) == str(PREFERRED_ACCOUNT_ID):
                return acc.get("id"), acc
        raise RuntimeError(f"Account id {PREFERRED_ACCOUNT_ID} not found in Myfxbook.")
    # Default: choose the one with latest update time
    accounts_sorted = sorted(
        accounts,
        key=lambda a: a.get("lastUpdateDate", ""),
        reverse=True,
    )
    acc = accounts_sorted[0]
    return acc.get("id"), acc

def get_daily_gain_range(session, account_id, start_date: date, end_date: date):
    # Myfxbook daily-gain endpoint INCLUSIVE range
    data = api_call(
        "get-daily-gain.json",
        {
			"session": session,
            "id": account_id,
            "start": ymd(start_date),
            "end": ymd(end_date),
        },
    )
    return data.get("dailyGain", []) or []

def fetch_account_overview(session, account_id):
    data = api_call("get-account.json", {"session": session, "id": account_id})
    return data.get("account")

def send_tele(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        logging.info("Telegram env missing; skip send.")
        return
    full = f"💹 ROI REPORT\n{msg}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": full},
            timeout=20,
        )
    except Exception as e:
        logging.exception(f"Telegram send error: {e}")

# ---------- Main compute ----------
def main():
    try:
        session = api_login()
        logging.info("Myfxbook login OK")
    except Exception as e:
        logging.exception("Login failed")
        sys.exit(1)

    try:
        account_id, acc_brief = pick_account_id(session)
    except Exception as e:
        logging.exception("Pick account failed")
        sys.exit(1)

    logging.info(f"Use account: {acc_brief.get('name')} | id={account_id} | broker={acc_brief.get('broker')} | server={acc_brief.get('server')}")

    # Date ranges
    today = dt_today()
    start_day = today  # today only
    start_week = today - timedelta(days=today.weekday())  # Monday of this week
    start_month = date(today.year, today.month, 1)

    # Pull daily gains
    try:
        dg_day = get_daily_gain_range(session, account_id, start_day, today)
        dg_week = get_daily_gain_range(session, account_id, start_week, today)
        dg_month = get_daily_gain_range(session, account_id, start_month, today)
    except Exception as e:
        logging.exception("Daily gain fetch failed")
        sys.exit(1)

    roi_day = compound_gain_percent(dg_day)
    roi_week = compound_gain_percent(dg_week)
    roi_month = compound_gain_percent(dg_month)

    # Overview for ALL-time gain/drawdown/equity
    try:
        acc_full = fetch_account_overview(session, account_id)
    except Exception as e:
        logging.exception("Overview fetch failed")
        sys.exit(1)

    gain_all = float(acc_full.get("gain", 0.0))  # % all-time gain
    dd = float(acc_full.get("drawdown", 0.0)) if acc_full.get("drawdown") is not None else None
    balance = acc_full.get("balance")
    equity = acc_full.get("equity")
    currency = acc_full.get("currency")
    last_update = acc_full.get("lastUpdateDate")

    # Build message
    lines = []
    lines.append(f"🏦 {acc_full.get('name')} (id {account_id})")
    lines.append(f"Broker: {acc_full.get('broker')} | Server: {acc_full.get('server')}")
    lines.append(f"Balance: {balance:.2f} {currency} | Equity: {equity:.2f} {currency}")
    if dd is not None:
        lines.append(f"Max DD: {dd:.2f}%")
    lines.append("— ROI (%) —")
	lines.append(f"Day:   {roi_day:+.2f}%")
    lines.append(f"Week:  {roi_week:+.2f}%")
    lines.append(f"Month: {roi_month:+.2f}%")
    lines.append(f"All:   {gain_all:+.2f}%")
    lines.append(f"Updated: {last_update}")
    msg = "\n".join(lines)

    logging.info("\n" + msg)
    send_tele(msg)

if __name__ == "__main__":
    main()
