import os
import requests
import logging
from datetime import datetime

# -------- Config --------
MYFXBOOK_EMAIL = os.getenv("MYFXBOOK_EMAIL")
MYFXBOOK_PASSWORD = os.getenv("MYFXBOOK_PASSWORD")
MYFXBOOK_ACCOUNT_ID = os.getenv("MYFXBOOK_ACCOUNT_ID")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# -------- Myfxbook API --------
def login_myfxbook():
    url = "https://www.myfxbook.com/api/login.json"
    r = requests.get(url, params={"email": MYFXBOOK_EMAIL, "password": MYFXBOOK_PASSWORD})
    j = r.json()
    if not j.get("error", True):
        return j["session"]
    else:
        raise RuntimeError(f"Login failed: {j}")

def get_account_info(session, account_id):
    url = "https://www.myfxbook.com/api/get-my-accounts.json"
    r = requests.get(url, params={"session": session})
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"Cannot fetch accounts: {j}")
    for acc in j["accounts"]:
        if str(acc["id"]) == str(account_id):
            return acc
    raise RuntimeError("Account not found")

def get_roi_stats(session, account_id):
    url = "https://www.myfxbook.com/api/get-daily-gain.json"
    r = requests.get(url, params={"session": session, "id": account_id})
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"Cannot fetch ROI stats: {j}")

    today = datetime.utcnow().date()
    roi_day = 0.0
    for rec in j.get("dailyGain", []):
        if rec["date"] == str(today):
            roi_day = rec["value"]
            break

    return {
        "roi_day": roi_day,
        "roi_week": "N/A",
        "roi_month": "N/A",
        "roi_all": "N/A",
    }

# -------- Telegram --------
def send_tele(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=20
        )
    except Exception as e:
        logging.exception(f"Telegram error: {e}")

def main():
    session = None
    try:
        session = login()
        acc = get_account_performance(session, MYFXBOOK_ACCOUNT_ID)
        roi = get_roi(session, acc["id"])

        msg = (
            f"📊 ROI Report ({acc['name']})\n"
            f"💰 Balance: {roi['balance']:.2f} | Equity: {roi['equity']:.2f}\n"
            f"📅 Today: {roi['day']:.2f}%\n"
            f"🗓️ This Week: {roi['week']:.2f}%\n"
            f"📆 This Month: {roi['month']:.2f}%\n"
            f"🌍 All Time: {roi['all']:.2f}%\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        logging.info(msg)
        send_tele(msg)

    except Exception as e:
        logging.error(f"Main error: {e}")
        send_tele(f"❌ Bot error: {e}")
    finally:
        if session:
            logout(session)

if __name__ == "__main__":
    main()
