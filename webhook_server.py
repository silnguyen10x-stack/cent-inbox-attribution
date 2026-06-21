"""
Cent Beauty — Webhook receiver (XƯƠNG SỐNG attribution).
Bắt `ad_id` từ tin nhắn đầu tiên của khách (object `referral`) và lưu vào DB.

Chạy:  uvicorn webhook_server:app --host 0.0.0.0 --port 8000
Cần HTTPS public (ngrok khi test, VPS+SSL khi production).

Đăng ký webhook trong App Dashboard:
  - Object Page:      messages, messaging_referrals, messaging_postbacks
  - Object Instagram: messages
  - Verify Token = VERIFY_TOKEN trong .env
"""
import os
import json
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
from fastapi import FastAPI, Request, Response
from dotenv import load_dotenv

load_dotenv()
VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]
DB_DSN = os.environ["DATABASE_URL"]

app = FastAPI(title="Cent Inbox Attribution Webhook")


def db():
    return psycopg2.connect(DB_DSN)


@app.on_event("startup")
def init_schema():
    """Tự chạy schema.sql khi khởi động (idempotent — CREATE ... IF NOT EXISTS)."""
    import os.path
    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(path):
        print("⚠️ Không thấy schema.sql, bỏ qua khởi tạo bảng.")
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            sql = f.read()
        with db() as conn, conn.cursor() as cur:
            cur.execute(sql)
        print("✅ Schema đã sẵn sàng (bảng + view).")
    except Exception as e:
        print(f"⚠️ Lỗi khởi tạo schema: {e}")


def ts(ms: int | None):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ---------- Xác minh webhook (Meta gọi GET 1 lần khi đăng ký) ----------
@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    return Response(status_code=403)


# ---------- Nhận sự kiện real-time ----------
@app.post("/webhook")
async def receive(request: Request):
    data = await request.json()
    platform = "instagram" if data.get("object") == "instagram" else "facebook"

    for entry in data.get("entry", []):
        page_id = entry.get("id")
        for ev in entry.get("messaging", []):
            psid = ev.get("sender", {}).get("id")
            # Dùng thời gian Meta; nếu thiếu/0 thì lấy giờ server (tránh mốc 1970)
            ts_event = ts(ev.get("timestamp")) or datetime.now(timezone.utc)

            # 1) REFERRAL — ad_id đến từ quảng cáo Click-to-Messenger/Direct
            #    Có thể nằm ở ev['referral'] (đã có hội thoại) hoặc
            #    ev['message']['referral'] / ev['postback']['referral'] (khách mới).
            ref = (
                ev.get("referral")
                or ev.get("message", {}).get("referral")
                or ev.get("postback", {}).get("referral")
            )
            if ref and (ref.get("ad_id") or ref.get("ref")):
                save_referral(page_id, psid, ref, ts_event)

            # 2) MESSAGE — lưu nội dung tin (cả in/out)
            msg = ev.get("message")
            if msg and not msg.get("is_echo"):
                save_message(page_id, platform, psid, msg, ts_event, direction="in")
            elif msg and msg.get("is_echo"):
                save_message(page_id, platform, psid, msg, ts_event, direction="out")

    return Response(content="EVENT_RECEIVED", media_type="text/plain")


def save_referral(page_id, psid, ref, when):
    with db() as conn, conn.cursor() as cur:
        # đảm bảo có customer
        cur.execute(
            """INSERT INTO customers(psid, platform, page_id, first_seen)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (psid) DO NOTHING""",
            (psid, "facebook", page_id, when),
        )
        cur.execute(
            """INSERT INTO ad_referrals
               (psid, page_id, ad_id, ref, source, type, ctwa_clid, referred_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                psid, page_id,
                ref.get("ad_id"), ref.get("ref"),
                ref.get("source"), ref.get("type"),
                ref.get("ctwa_clid"), when,
            ),
        )
        print(f"[REFERRAL] psid={psid} ad_id={ref.get('ad_id')} ref={ref.get('ref')}")


def save_message(page_id, platform, psid, msg, when, direction):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO customers(psid, platform, page_id, first_seen)
               VALUES (%s,%s,%s,%s) ON CONFLICT (psid) DO NOTHING""",
            (psid, platform, page_id, when),
        )
        # Meta thật luôn có 'mid'; fallback phòng trường hợp thiếu để không mất tin
        mid = msg.get("mid") or f"nomid_{psid}_{int(when.timestamp()) if when else 0}"
        cur.execute(
            """INSERT INTO messages(message_id, psid, direction, body, sent_at)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (message_id) DO NOTHING""",
            (mid, psid, direction, msg.get("text"), when),
        )


@app.get("/health")
async def health():
    return {"status": "ok"}
