"""
Cent Beauty — Backfill hội thoại lịch sử qua Conversations API (FB + Instagram).
Lưu ý: API NÀY KHÔNG trả về ad_id của hội thoại cũ — ad_id chỉ bắt được qua webhook.
Script này dùng để nạp NỘI DUNG & SỐ LƯỢNG tin nhắn lịch sử (đo volume, QC).

Chạy:  python fetch_conversations.py
Cấu hình PAGE_TOKENS trong .env dạng JSON: {"<page_id>":"<token>", ...}
"""
import os
import json
import time
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()
GRAPH = "https://graph.facebook.com/v23.0"
PAGE_TOKENS = json.loads(os.environ["PAGE_TOKENS"])  # {page_id: page_access_token}
DB_DSN = os.environ["DATABASE_URL"]


def db():
    return psycopg2.connect(DB_DSN)


def get(url, params):
    """GET có xử lý phân trang + lùi nhịp khi dính rate limit."""
    while url:
        r = requests.get(url, params=params)
        if r.status_code == 429:
            print("  rate limited, chờ 60s...")
            time.sleep(60)
            continue
        r.raise_for_status()
        data = r.json()
        yield from data.get("data", [])
        url = data.get("paging", {}).get("next")
        params = None  # 'next' đã chứa đủ tham số


def fetch_page(page_id, token, platform):
    """Kéo toàn bộ hội thoại của 1 page cho 1 nền tảng (facebook|instagram)."""
    print(f"== {platform} page {page_id} ==")
    params = {
        "access_token": token,
        "platform": platform,
        "fields": "participants,updated_time,message_count,"
                  "messages.limit(50){message,from,created_time,id}",
        "limit": 50,
    }
    n = 0
    with db() as conn, conn.cursor() as cur:
        for conv in get(f"{GRAPH}/{page_id}/conversations", params):
            n += 1
            save_conversation(cur, page_id, platform, conv)
        conn.commit()
    print(f"  -> {n} hội thoại")


def save_conversation(cur, page_id, platform, conv):
    conv_id = conv["id"]
    msgs = conv.get("messages", {}).get("data", [])
    # participant không phải page = khách
    psid = None
    for p in conv.get("participants", {}).get("data", []):
        if str(p.get("id")) != str(page_id):
            psid = p.get("id")
            name = p.get("name")
            break
    if psid:
        cur.execute(
            """INSERT INTO customers(psid, platform, page_id, name)
               VALUES (%s,%s,%s,%s) ON CONFLICT (psid) DO NOTHING""",
            (psid, platform, page_id, locals().get("name")),
        )
    times = [m.get("created_time") for m in msgs if m.get("created_time")]
    cur.execute(
        """INSERT INTO conversations
           (conversation_id, psid, page_id, platform, message_count,
            first_message_at, last_message_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (conversation_id) DO UPDATE
             SET message_count = EXCLUDED.message_count,
                 last_message_at = EXCLUDED.last_message_at""",
        (conv_id, psid, page_id, platform, conv.get("message_count"),
         min(times) if times else None, max(times) if times else None),
    )
    for m in msgs:
        sender = m.get("from", {}).get("id")
        direction = "out" if str(sender) == str(page_id) else "in"
        cur.execute(
            """INSERT INTO messages(message_id, conversation_id, psid, direction, body, sent_at)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (message_id) DO NOTHING""",
            (m.get("id"), conv_id, psid, direction, m.get("message"), m.get("created_time")),
        )


if __name__ == "__main__":
    for page_id, token in PAGE_TOKENS.items():
        for platform in ("facebook", "instagram"):
            try:
                fetch_page(page_id, token, platform)
            except requests.HTTPError as e:
                print(f"  bỏ qua {platform} {page_id}: {e}")
    print("Xong backfill.")
