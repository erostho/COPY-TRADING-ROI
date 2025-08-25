# -*- coding: utf-8 -*-
"""
Exness ROI Tracker (via MetaApi SDK)
- Periods: Day / Week / Month / All
- Uses Equity for ROI (mark-to-market)
- Persists baselines in /mnt/data/roi_state.json
- Sends summary to Telegram

ENV required:
  METAAPI_TOKEN
  METAAPI_ACCOUNT_ID
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

ENV optional:
  LOG_LEVEL=INFO|DEBUG (default INFO)
  TZ=Asia/Ho_Chi_Minh      # timezone for period roll
  HEADER="💵 TRADE GOODS"  # Telegram header
"""

import os, json, asyncio, logging
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
from metaapi_cloud_sdk import MetaApi

# ---------- ENV / CONFIG ----------
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID    = os.getenv("METAAPI_ACCOUNT_ID")
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID")
LOG_LEVEL     = os.getenv("LOG_LEVEL", "INFO").upper()
TZ_NAME       = os.getenv("TZ", "Asia/Ho_Chi_Minh")
HEADER        = os.getenv("HEADER", "💵 TRADE GOODS")

if not METAAPI_TOKEN or not ACCOUNT_ID:
    raise SystemExit("❌ Missing METAAPI_TOKEN or METAAPI_ACCOUNT_ID")
if not BOT_TOKEN or not CHAT_ID:
    print("⚠️ Missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID — script runs but cannot send Telegram.")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
TZ = ZoneInfo(TZ_NAME)

STATE_FILE = Path("/mnt/data/roi_state.json")  # persisted baselines

# ---------- Helpers ----------
def now_local():
    return datetime.now(TZ)

def start_of_day(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

def start_of_week(dt):
    # ISO week: Monday is 0
    sod = start_of_day(dt)
    return sod - timedelta(days=sod.weekday())

def start_of_month(dt):
    sod = start_of_day(dt)
    return sod.replace(day=1)

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            logging.warning("State file corrupted, resetting.")
    return {}

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def fmt_money(x):
    try:
        return f"{x:,.2f}"
    except Exception:
        return str(x)

def fmt_pct(x):
    if x is None:
        return "N/A"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.2f}%"

def roi_pct(current_equity, base_equity):
    if base_equity is None or base_equity <= 0:
        return None
    return (current_equity - base_equity) / base_equity * 100.0

def ensure_period_baseline(state, key, base_dt, equity_now, balance_now):
    """
    Ensure baseline exists for 'key' with timestamp >= base_dt (roll if period passed).
    Keys: day/week/month/all
    """
    rec = state.get(key)
    if rec:
        ts = datetime.fromisoformat(rec["ts"])
	# roll if baseline is before current period start
        if ts < base_dt:
            logging.info(f"Rolling baseline for {key} to {base_dt.isoformat()}")
            state[key] = {"ts": base_dt.isoformat(),
                          "equity": equity_now, "balance": balance_now}
    else:
        logging.info(f"Init baseline for {key} at {base_dt.isoformat()}")
        state[key] = {"ts": base_dt.isoformat(),
                      "equity": equity_now, "balance": balance_now}

def send_tele(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    full_message = f"{HEADER}\n{text}"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": full_message},
            timeout=20
        )
        if r.status_code != 200:
            logging.warning(f"Telegram send failed: {r.status_code} {r.text}")
    except Exception as e:
        logging.exception(f"Telegram error: {e}")

# ---------- Core (MetaApi) ----------
async def get_account_info():
    metaapi = MetaApi(METAAPI_TOKEN)
    account = await metaapi.metatrader_account_api.get_account(ACCOUNT_ID)

    # deploy/connect if needed
    if account.state not in ("DEPLOYED", "DEPLOYING"):
        logging.info("Deploying account on MetaApi...")
        await account.deploy()

    connection = await account.connect()
    await connection.wait_synchronized()

    info = await connection.get_account_information()
    # info includes: balance, equity, margin, freeMargin, etc.
    balance = float(info.get("balance", 0.0))
    equity  = float(info.get("equity", 0.0))

    return {
        "login": account.login,
        "server": account.server,
        "balance": balance,
        "equity": equity
    }

# ---------- App ----------
async def main_async():
    t0 = now_local()
    try:
        acc = await get_account_info()
    except Exception as e:
        logging.exception("MetaApi error")
        send_tele(f"❌ ROI: lỗi kết nối MetaApi: {e}")
        return

    equity_now  = acc["equity"]
    balance_now = acc["balance"]

    # load / roll baselines
    state = load_state()
    sod = start_of_day(t0)
    sow = start_of_week(t0)
    som = start_of_month(t0)
    soa = start_of_day(t0)  # 'all' starts first time you run

    ensure_period_baseline(state, "day",   sod, equity_now, balance_now)
    ensure_period_baseline(state, "week",  sow, equity_now, balance_now)
    ensure_period_baseline(state, "month", som, equity_now, balance_now)
    ensure_period_baseline(state, "all",   soa, equity_now, balance_now)

    save_state(state)  # persist any new baselines

    # compute ROI per period (based on Equity)
    base_day   = state["day"]["equity"]
    base_week  = state["week"]["equity"]
    base_month = state["month"]["equity"]
    base_all   = state["all"]["equity"]

    roi_day   = roi_pct(equity_now, base_day)
    roi_week  = roi_pct(equity_now, base_week)
    roi_month = roi_pct(equity_now, base_month)
    roi_all   = roi_pct(equity_now, base_all)

    # compose message
    lines = []
    lines.append("==== ROI (Exness) ====")
    lines.append(f"Account: {acc['login']} @ {acc['server']}")
    lines.append(f"Equity: ${fmt_money(equity_now)} | Balance: ${fmt_money(balance_now)}")
    lines.append(f"Day:   {fmt_pct(roi_day)}  (since {state['day']['ts']})")
    lines.append(f"Week:  {fmt_pct(roi_week)} (since {state['week']['ts']})")
    lines.append(f"Month: {fmt_pct(roi_month)} (since {state['month']['ts']})")
    lines.append(f"All:   {fmt_pct(roi_all)}  (since {state['all']['ts']})")

    msg = "\n".join(lines)
    logging.info(msg.replace("\n", " | "))
    send_tele(msg)

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()