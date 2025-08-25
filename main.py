# -*- coding: utf-8 -*-
"""
ROI Tracker via Myfxbook (FREE)
- Periods: Day / Week / Month / All
- Uses Equity for ROI
- Stores baselines in /mnt/data/roi_state.json
- Sends Telegram

ENV cần:
  MYFXBOOK_EMAIL
  MYFXBOOK_PASSWORD
  # optional: MYFXBOOK_ACCOUNT_ID (nếu bỏ trống sẽ lấy account đầu tiên)
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
  TZ=Asia/Ho_Chi_Minh (optional)
  HEADER="💵 TRADE GOODS" (optional)
"""

import os, json, logging, requests
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ---------- ENV ----------
EMAIL   = os.getenv("MYFXBOOK_EMAIL")
PASS    = os.getenv("MYFXBOOK_PASSWORD")
ACC_ID  = os.getenv("MYFXBOOK_ACCOUNT_ID")  # optional
BOT     = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT    = os.getenv("TELEGRAM_CHAT_ID")
TZ_NAME = os.getenv("TZ", "Asia/Ho_Chi_Minh")
HEADER  = os.getenv("HEADER", "💵 TRADE GOODS")

if not EMAIL or not PASS:
    raise SystemExit("❌ Missing MYFXBOOK_EMAIL or MYFXBOOK_PASSWORD")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
TZ = ZoneInfo(TZ_NAME)

STATE_FILE = Path("/mnt/data/roi_state.json")

# ---------- Time helpers ----------
def now_local():
    return datetime.now(TZ)

def start_of_day(dt):   return dt.replace(hour=0, minute=0, second=0, microsecond=0)
def start_of_week(dt):  return start_of_day(dt) - timedelta(days=start_of_day(dt).weekday())
def start_of_month(dt): return start_of_day(dt).replace(day=1)

# ---------- Persist ----------
def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text())
        except Exception: pass
    return {}

def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def fmt_money(x):
    try: return f"{float(x):,.2f}"
    except: return str(x)

def fmt_pct(x):
    if x is None: return "N/A"
    s = "+" if x >= 0 else ""
    return f"{s}{x:.2f}%"

def roi_pct(cur, base):
    try:
        cur = float(cur); base = float(base)
        if base <= 0: return None
        return (cur - base) / base * 100.0
    except: return None

def ensure_baseline(state, key, period_start, equity_now, balance_now):
    rec = state.get(key)
    if rec:
        ts = datetime.fromisoformat(rec["ts"])
        if ts < period_start:
            state[key] = {"ts": period_start.isoformat(),
                          "equity": equity_now, "balance": balance_now}
    else:
        state[key] = {"ts": period_start.isoformat(),
                      "equity": equity_now, "balance": balance_now}

# ---------- Myfxbook API ----------
BASE = "https://www.myfxbook.com/api"

def myfx_login():
    r = requests.get(f"{BASE}/login.json", params={"email": EMAIL, "password": PASS}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if not j.get("error") and j.get("session"):
        return j["session"]
	raise RuntimeError(f"Login failed: {j}")

def myfx_logout(session):
    try:
        requests.get(f"{BASE}/logout.json", params={"session": session}, timeout=10)
    except: pass

def myfx_accounts(session):
    r = requests.get(f"{BASE}/get-my-accounts.json", params={"session": session}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(j)
    return j.get("accounts", [])

def pick_account(accounts, prefer_id=None):
    if prefer_id:
        for a in accounts:
            if str(a.get("id")) == str(prefer_id):
                return a
    return accounts[0] if accounts else None

# ---------- Telegram ----------
def send_tele(text):
    if not BOT or not CHAT: 
        logging.info("No TELEGRAM env set; skip Telegram.")
        return
    full = f"{HEADER}\n{text}"
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT}/sendMessage",
            json={"chat_id": CHAT, "text": full},
            timeout=20
        )
    except Exception as e:
        logging.exception(f"Telegram error: {e}")

# ---------- Main ----------
def main():
    session = myfx_login()
    try:
        accs = myfx_accounts(session)
        if not accs:
            raise RuntimeError("No accounts linked to Myfxbook.")
        acc = pick_account(accs, ACC_ID)
        if not acc:
            raise RuntimeError("Account id not found.")

        # Myfxbook fields: balance, equity, profit, name, broker, server, id, ...
        balance_now = float(acc.get("balance", 0))
        equity_now  = float(acc.get("equity", 0))
        name        = acc.get("name", "")
        broker      = acc.get("broker", "")
        server      = acc.get("server", "")
        acc_id      = acc.get("id")

        t0  = now_local()
        sod = start_of_day(t0)
        sow = start_of_week(t0)
        som = start_of_month(t0)
        soa = sod  # all from first run

        state = load_state()
        ensure_baseline(state, "day",   sod, equity_now, balance_now)
        ensure_baseline(state, "week",  sow, equity_now, balance_now)
        ensure_baseline(state, "month", som, equity_now, balance_now)
        ensure_baseline(state, "all",   soa, equity_now, balance_now)
        save_state(state)

        roi_day   = roi_pct(equity_now, state["day"]["equity"])
        roi_week  = roi_pct(equity_now, state["week"]["equity"])
        roi_month = roi_pct(equity_now, state["month"]["equity"])
        roi_all   = roi_pct(equity_now, state["all"]["equity"])

        lines = [
            "==== ROI (Myfxbook / Exness) ====",
            f"Account: {name} | {broker} {server} | ID: {acc_id}",
            f"Equity: ${fmt_money(equity_now)} | Balance: ${fmt_money(balance_now)}",
            f"Day:   {fmt_pct(roi_day)}  (since {state['day']['ts']})",
            f"Week:  {fmt_pct(roi_week)} (since {state['week']['ts']})",
            f"Month: {fmt_pct(roi_month)} (since {state['month']['ts']})",
			f"All:   {fmt_pct(roi_all)}  (since {state['all']['ts']})",
        ]
        msg = "\n".join(lines)
        logging.info(msg.replace("\n", " | "))
        send_tele(msg)

    finally:
        myfx_logout(session)

if __name__ == "__main__":
    main()
