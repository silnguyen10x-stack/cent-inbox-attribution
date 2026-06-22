"""
Cent Beauty — Đồng bộ chi tiêu quảng cáo từ Meta Graph API vào bảng `ad_spend`.

Mục đích: web báo cáo riêng KHÔNG dùng Ads MCP của Cowork, nên lấy số ads
trực tiếp từ Graph API (Ads Insights), đổ vào DB theo ad_id × ngày.
Sau đó endpoint /api/attribution tính ROAS thật theo ad_id.

Chạy định kỳ (cron, vd 6h sáng mỗi ngày):
    python ads_sync.py                 # mặc định: 3 ngày gần nhất
    python ads_sync.py 2026-06-01 2026-06-20   # khoảng tùy chọn

Cần biến môi trường (.env):
    DATABASE_URL        = postgres://...
    META_ACCESS_TOKEN   = token System User (quyền ads_read), KHÔNG hết hạn
    AD_ACCOUNT_IDS      = 672539241340824,1574799437088902   (mặc định 2 TK Cent)
    GRAPH_API_VERSION   = v21.0 (tùy chọn)
"""
import os
import sys
from datetime import date, timedelta

import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

DB_DSN = os.environ["DATABASE_URL"]
TOKEN = os.environ.get("META_ACCESS_TOKEN")
ACCOUNTS = [a.strip() for a in os.environ.get(
    "AD_ACCOUNT_IDS", "672539241340824,1574799437088902").split(",") if a.strip()]
GV = os.environ.get("GRAPH_API_VERSION", "v21.0")

# Action type "tin nhắn bắt đầu" (messaging conversations started, 7 ngày)
MSG_ACTION = "onsite_conversion.messaging_conversation_started_7d"


def ensure_columns():
    """Thêm cột mở rộng cho ad_spend nếu chưa có (idempotent)."""
    sql = """
    ALTER TABLE ad_spend ADD COLUMN IF NOT EXISTS clicks       BIGINT;
    ALTER TABLE ad_spend ADD COLUMN IF NOT EXISTS reach        BIGINT;
    ALTER TABLE ad_spend ADD COLUMN IF NOT EXISTS results_msg  BIGINT;  -- tin nhắn bắt đầu
    ALTER TABLE ad_spend ADD COLUMN IF NOT EXISTS ad_name      TEXT;
    ALTER TABLE ad_spend ADD COLUMN IF NOT EXISTS synced_at    TIMESTAMPTZ DEFAULT now();
    """
    with psycopg2.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql)


def fetch_insights(account_id: str, since: str, until: str):
    """Gọi Graph API Ads Insights, level=ad, time_increment=1 (theo ngày)."""
    url = f"https://graph.facebook.com/{GV}/act_{account_id}/insights"
    params = {
        "level": "ad",
        "fields": "ad_id,ad_name,spend,impressions,reach,clicks,actions",
        "time_range": f'{{"since":"{since}","until":"{until}"}}',
        "time_increment": 1,
        "limit": 500,
        "access_token": TOKEN,
    }
    rows = []
    while url:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        j = r.json()
        rows.extend(j.get("data", []))
        # phân trang
        url = j.get("paging", {}).get("next")
        params = None  # 'next' đã chứa full query
    return rows


def parse_msg(actions):
    if not actions:
        return 0
    for a in actions:
        if a.get("action_type") == MSG_ACTION:
            try:
                return int(float(a.get("value", 0)))
            except (TypeError, ValueError):
                return 0
    return 0


def upsert(records):
    """records: list of tuple (ad_id, date, spend, impressions, clicks, reach, results_msg, ad_name)"""
    if not records:
        return 0
    sql = """
        INSERT INTO ad_spend (ad_id, date, spend, impressions, clicks, reach, results_msg, ad_name)
        VALUES %s
        ON CONFLICT (ad_id, date) DO UPDATE SET
            spend       = EXCLUDED.spend,
            impressions = EXCLUDED.impressions,
            clicks      = EXCLUDED.clicks,
            reach       = EXCLUDED.reach,
            results_msg = EXCLUDED.results_msg,
            ad_name     = EXCLUDED.ad_name,
            synced_at   = now()
    """
    with psycopg2.connect(DB_DSN) as conn, conn.cursor() as cur:
        execute_values(cur, sql, records)
    return len(records)


def run(since: str, until: str):
    if not TOKEN:
        sys.exit("❌ Thiếu META_ACCESS_TOKEN trong .env")
    ensure_columns()
    total = 0
    for acc in ACCOUNTS:
        print(f"→ Account {acc}: kéo insights {since}…{until}")
        data = fetch_insights(acc, since, until)
        recs = []
        for d in data:
            recs.append((
                d.get("ad_id"),
                d.get("date_start"),                 # time_increment=1 → mỗi dòng 1 ngày
                round(float(d.get("spend", 0) or 0)),
                int(d.get("impressions", 0) or 0),
                int(d.get("clicks", 0) or 0),
                int(d.get("reach", 0) or 0),
                parse_msg(d.get("actions")),
                d.get("ad_name"),
            ))
        n = upsert(recs)
        total += n
        print(f"  ✓ upsert {n} dòng (ad × ngày)")
    print(f"✅ Hoàn tất: {total} dòng vào ad_spend.")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        s, u = sys.argv[1], sys.argv[2]
    else:
        u = date.today() - timedelta(days=1)        # hôm qua
        s = u - timedelta(days=2)                    # 3 ngày gần nhất
        s, u = s.isoformat(), u.isoformat()
    run(s, u)
