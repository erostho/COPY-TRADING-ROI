#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import logging
from datetime import datetime, timedelta, timezone
import requests

# ============== Logging ==============
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)

# ============== ENV ==============
EMAIL = os.getenv("MYFXBOOK_EMAIL", "")
PASSWORD = os.getenv("MYFXBOOK_PASSWORD", "")
PREF_ACC_ID = os.getenv("MYFXBOOK_ACCOUNT_ID", "")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TITLE_PREFIX = "💵 TRADE GOODS"

# ============== CONST ==============
API_BASE = "https://www.myfxbook.com/api"
HTTP_HEADERS = {"User-Agent": "roi-bot/1.0"}
HTTP_TIMEOUT = 30


# ============== Utils ==============
def now_vn():
    """Trả về datetime theo múi giờ từ ENV (mặc định +7)."""
    tzname = os.getenv("TZ", "Asia/Ho_Chi_Minh")
    try:
        if tzname == "Asia/Ho_Chi_Minh":
            return datetime.now(timezone(timedelta(hours=7)))
        # fallback: giờ local của container
        return datetime.now()
    except Exception:
        return datetime.utcnow()


def http_get(url: str, params: dict | None = None) -> dict:
    """GET JSON, raise nếu không 2xx."""
    r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        logging.debug("Raw response: %s", r.text[:500])
        raise


def myfx_login() -> str:
    if not EMAIL or not PASSWORD:
        raise RuntimeError("Thiếu MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD")
    url = f"{API_BASE}/login.json"
    params = {"email": EMAIL, "password": PASSWORD}
    logging.info("Myfxbook: login...")
    j = http_get(url, params)
    if j.get("error") is False and "session" in j:
        logging.info("Myfxbook: login OK")
        return j["session"]
    raise RuntimeError(f"Login failed: {j}")


def myfx_logout(session: str) -> None:
    try:
        http_get(f"{API_BASE}/logout.json", {"session": session})
    except Exception:
        pass


def myfx_get_accounts(session: str) -> list:
    j = http_get(f"{API_BASE}/get-my-accounts.json", {"session": session})
    if j.get("error") is False and isinstance(j.get("accounts"), list):
        return j["accounts"]
    raise RuntimeError(f"get-my-accounts failed: {j}")


def pick_account(accounts: list) -> tuple[str, dict]:
    """Chọn account theo MYFXBOOK_ACCOUNT_ID; mặc định lấy cái đầu tiên."""
    if PREF_ACC_ID:
        for a in accounts:
            if str(a.get("id")) == str(PREF_ACC_ID):
                return str(a.get("id")), a
        logging.warning("MYFXBOOK_ACCOUNT_ID không khớp – dùng account đầu tiên.")
    if not accounts:
        raise RuntimeError("Không có account nào trong Myfxbook.")
    a = accounts[0]
    return str(a.get("id")), a
def myfx_daily_gain(session: str, acc_id: str, start: str, end: str) -> list:
    """
    Lấy daily gain % theo ngày.
    Trả list các dict có 'date' và 'value' (hoặc 'gain' tuỳ API version).
    """
    j = http_get(
        f"{API_BASE}/get-daily-gain.json",
        {"session": session, "id": acc_id, "start": start, "end": end},
    )
    if j.get("error") is False:
        if isinstance(j.get("dailyGain"), list):
            return j["dailyGain"]
        if isinstance(j.get("data"), list):  # một số bản trả 'data'
            return j["data"]
    raise RuntimeError(f"get-daily-gain failed: {j}")


def sum_simple_pct(rows: list) -> float:
    """Cộng % đơn giản (xấp xỉ)."""
    s = 0.0
    for r in rows:
        try:
            v = float(r.get("value") if "value" in r else r.get("gain", 0.0))
        except Exception:
            v = 0.0
        s += v
    return s


def sum_compound_pct(rows: list) -> float:
    """Cộng dồn theo lãi kép từ daily % (chính xác hơn)."""
    acc = 1.0
    for r in rows:
        try:
            v = float(r.get("value") if "value" in r else r.get("gain", 0.0))
        except Exception:
            v = 0.0
        acc *= (1.0 + v / 100.0)
    return (acc - 1.0) * 100.0


def fmt_pct(x: float | None) -> str:
    try:
        return f"{float(x):+.2f}%"
    except Exception:
        return "N/A"


def ranges_today_week_month() -> dict:
    """Trả các khoảng ngày (YYYY-MM-DD)."""
    today = now_vn().date()
    start_week = today - timedelta(days=today.weekday())  # Monday
    start_month = today.replace(day=1)
    dd = lambda d: d.strftime("%Y-%m-%d")
    return {
        "day": (dd(today), dd(today)),
        "week": (dd(start_week), dd(today)),
        "month": (dd(start_month), dd(today)),
    }


def telegram_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        logging.warning("Thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID – bỏ qua gửi Telegram.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        logging.info("Telegram: sent")
    except Exception as e:
        logging.exception(f"Telegram error: {e}")


# ============== Main ==============
def main():
    session = None
    try:
        session = myfx_login()

        accounts = myfx_get_accounts(session)
        acc_id, acc_meta = pick_account(accounts)
        logging.info("Using account id=%s, name=%s, broker=%s",
                     acc_id, acc_meta.get("name"), acc_meta.get("broker"))

        # ALL (tổng) – lấy luôn trường 'gain' tổng của account nếu có
        roi_all = None
        try:
            roi_all = float(acc_meta.get("gain"))
        except Exception:
            roi_all = None

        # Day/Week/Month: lấy daily và cộng dồn (lãi kép)
        rg = ranges_today_week_month()
		results = {}

        for key, (start, end) in rg.items():
            try:
                rows = myfx_daily_gain(session, acc_id, start, end)
                roi_simple = sum_simple_pct(rows)
                roi_comp = sum_compound_pct(rows)
                results[key] = {"simple": roi_simple, "compound": roi_comp, "n": len(rows)}
                logging.info("ROI %s %s→%s | days=%s | simple=%s | compound=%s",
                             key, start, end, len(rows), fmt_pct(roi_simple), fmt_pct(roi_comp))
            except Exception as e:
                logging.warning("Fetch ROI %s fail: %s", key, e)
                results[key] = None
                time.sleep(1)

        # Build message
        lines = [f"{TITLE_PREFIX}", "📊 ROI (Myfxbook)"]
        lines.append(f"• Account: {acc_meta.get('name','N/A')} | ID: {acc_id} | Broker: {acc_meta.get('broker','N/A')}")
        lines.append(f"• Time: {now_vn().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        def pick(v):
            if not v:
                return "N/A"
            # ưu tiên compound
            return fmt_pct(v.get("compound"))

        lines.append(f"Day:   {pick(results.get('day'))}")
        lines.append(f"Week:  {pick(results.get('week'))}")
        lines.append(f"Month: {pick(results.get('month'))}")
        lines.append(f"All:   {fmt_pct(roi_all) if roi_all is not None else 'N/A'}")

        msg = "\n".join(lines)
        logging.info("Message:\n%s", msg)

        telegram_send(msg)

    except Exception as e:
        logging.exception("Fatal error: %s", e)
        # báo lỗi về Telegram cho dễ debug
        try:
            telegram_send(f"{TITLE_PREFIX}\n❌ ERROR: {e}")
        except Exception:
            pass
        sys.exit(1)
    finally:
        if session:
            myfx_logout(session)


if __name__ == "__main__":
    main()
