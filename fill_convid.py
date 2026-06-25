"""
Cent Beauty — fill_convid.py
Điền conversation_id cho các tin nhắn còn TRỐNG (NULL). Chạy bằng CRON (mỗi 5 phút).

Vì sao tách khỏi webhook: webhook phải trả lời Meta < vài giây; chèn gọi Graph đồng bộ
= rủi ro Meta ngừng đẩy tin. Script này chỉ tra PSID đang NULL nên nhẹ.

Cấu hình .env / Coolify Environment:
  DATABASE_URL = postgres://...   (đã có sẵn trong container)
  SYS_TOKEN    = <System User token VĨNH VIỄN>   <-- chỉ cần 1 biến này
  PAGE_IDS     = 100481071995511,116557994635275 (tùy chọn; mặc định 2 page Cent)
Script tự mint page token từ SYS_TOKEN mỗi lần chạy (page token system-user vĩnh viễn).

Chạy:  python fill_convid.py
Cron (5 phút): */5 * * * * cd /app && python fill_convid.py >> /var/log/fill_convid.log 2>&1
"""
import os
import json
import time
import requests
import psycopg2
from psycopg2.extras import execute_values

GRAPH = "https://graph.facebook.com/v23.0"
DB_DSN = os.environ["DATABASE_URL"]
LOOKBACK_DAYS = int(os.environ.get("FILL_LOOKBACK_DAYS", "7"))
PAGE_IDS = [p.strip() for p in os.environ.get(
    "PAGE_IDS", "100481071995511,116557994635275").split(",") if p.strip()]


def build_page_tokens():
    """Ưu tiên SYS_TOKEN (mint page token). Fallback: PAGE_TOKENS dạng JSON."""
    sys_token = os.environ.get("SYS_TOKEN")
    if sys_token:
        toks = {}
        for pid in PAGE_IDS:
            r = requests.get(f"{GRAPH}/{pid}",
                             params={"fields": "access_token", "access_token": sys_token},
                             timeout=20).json()
            pt = r.get("access_token")
            if pt:
                toks[pid] = pt
            else:
                print(f"  [!] khong mint duoc page token cho {pid}: {r.get('error')}")
        return toks
    return json.loads(os.environ["PAGE_TOKENS"])


def main():
    page_tokens = build_page_tokens()
    if not page_tokens:
        print("[fill_convid] Khong co page token. Kiem tra SYS_TOKEN/PAGE_TOKENS.")
        return
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT psid FROM messages
           WHERE conversation_id IS NULL
             AND sent_at >= now() - (%s || ' days')::interval""",
        (LOOKBACK_DAYS,),
    )
    psids = [r[0] for r in cur.fetchall()]
    if not psids:
        print("[fill_convid] Khong co PSID nao trong. Xong.")
        cur.close(); conn.close()
        return
    print(f"[fill_convid] {len(psids)} PSID can dien conversation_id")

    found = {}
    for page_id, token in page_tokens.items():
        for psid in psids:
            if psid in found:
                continue
            try:
                r = requests.get(
                    f"{GRAPH}/{page_id}/conversations",
                    params={"user_id": psid, "fields": "id", "access_token": token},
                    timeout=20,
                )
                if r.status_code == 429:
                    time.sleep(30)
                    continue
                data = r.json().get("data") or []
            except Exception as e:
                print(f"  [!] loi psid={psid} page={page_id}: {e}")
                continue
            if data:
                found[psid] = data[0]["id"]

    if not found:
        print("[fill_convid] Khong tim thay hoi thoai cho PSID nao.")
        cur.close(); conn.close()
        return

    rows = list(found.items())
    execute_values(
        cur,
        "UPDATE messages AS m SET conversation_id = v.cid "
        "FROM (VALUES %s) AS v(psid, cid) "
        "WHERE m.psid = v.psid AND m.conversation_id IS NULL",
        rows, page_size=1000,
    )
    conn.commit()
    cur.execute("SELECT count(*) FILTER (WHERE conversation_id IS NULL) FROM messages")
    con_trong = cur.fetchone()[0]
    print(f"[fill_convid] Tra duoc {len(found)} PSID. Con trong sau khi chay: {con_trong}")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
