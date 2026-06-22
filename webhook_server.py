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
import re
import json
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()
VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]
DB_DSN = os.environ["DATABASE_URL"]
TZ = os.environ.get("REPORT_TZ", "Asia/Ho_Chi_Minh")   # khoảng ngày tính theo giờ VN
METRICS_KEY = os.environ.get("METRICS_API_KEY")        # tùy chọn: bảo vệ /api/* (đọc)
ORDERS_KEY = os.environ.get("ORDERS_API_KEY") or METRICS_KEY  # bảo vệ ingest đơn (ghi)

app = FastAPI(title="Cent Inbox Attribution Webhook")

# Cho phép dashboard (trình duyệt) gọi /api/metrics từ domain khác
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # có thể siết lại theo domain dashboard của anh
    allow_methods=["GET"],
    allow_headers=["*"],
)


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


# ---------- Tự nhận diện SĐT trong nội dung chat ----------
# Bắt chuỗi bắt đầu bằng +84 / 84 / 0, cho phép space . - giữa các số.
_PHONE_CAND = re.compile(r"(?:\+?84|0)[\d\s.\-]{8,13}\d")
_VN_MOBILE = re.compile(r"0(3|5|7|8|9)\d{8}")   # di động VN sau 2018 (10 số)


def extract_vn_phone(text):
    """Bóc SĐT di động VN đầu tiên hợp lệ trong text. Trả None nếu không có."""
    if not text:
        return None
    for m in _PHONE_CAND.finditer(text):
        d = re.sub(r"\D", "", m.group())
        if d.startswith("84"):
            d = "0" + d[2:]
        if _VN_MOBILE.fullmatch(d):
            return d
    return None


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
        # TỰ NHẬN DIỆN SĐT: chỉ từ tin KHÁCH gửi (in), và chỉ điền nếu chưa có
        # (không ghi đè SĐT đã được sale/POS xác nhận qua /api/link hoặc /api/orders)
        if direction == "in":
            phone = extract_vn_phone(msg.get("text"))
            if phone:
                cur.execute(
                    "UPDATE customers SET phone=%s WHERE psid=%s AND (phone IS NULL OR phone='')",
                    (phone, psid),
                )
                print(f"[PHONE] auto-detect psid={psid} phone={phone}")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ============================================================
# API METRICS — số liệu đối soát Inbox cho dashboard (tự động)
#   GET /api/metrics?since=YYYY-MM-DD&until=YYYY-MM-DD[&ad_id=...][&key=...]
#   Khoảng ngày tính theo giờ VN (REPORT_TZ). Trả JSON.
# ============================================================
@app.get("/api/metrics")
async def api_metrics(since: str, until: str, key: str | None = None):
    if METRICS_KEY and key != METRICS_KEY:
        return Response(status_code=401, content="unauthorized")
    try:
        with db() as conn, conn.cursor() as cur:
            # (1) Tổng cuộc hội thoại — khách có gửi tin trong kỳ
            cur.execute(
                f"""SELECT COUNT(DISTINCT psid) FROM messages
                    WHERE direction='in'
                      AND (sent_at AT TIME ZONE %s)::date BETWEEN %s AND %s""",
                (TZ, since, until))
            total = cur.fetchone()[0] or 0

            # (2) Người liên hệ MỚI — lần đầu gửi tin rơi vào kỳ
            cur.execute(
                f"""WITH firsts AS (
                        SELECT psid, MIN(sent_at) AS first_in
                        FROM messages WHERE direction='in' GROUP BY psid)
                    SELECT COUNT(*) FROM firsts
                    WHERE (first_in AT TIME ZONE %s)::date BETWEEN %s AND %s""",
                (TZ, since, until))
            new_contacts = cur.fetchone()[0] or 0

            # (3) Người liên hệ MỚI đến TỪ QUẢNG CÁO (có ad referral)
            cur.execute(
                f"""WITH firsts AS (
                        SELECT psid, MIN(sent_at) AS first_in
                        FROM messages WHERE direction='in' GROUP BY psid)
                    SELECT COUNT(DISTINCT f.psid) FROM firsts f
                    JOIN ad_referrals r ON r.psid=f.psid AND r.source='ADS'
                    WHERE (f.first_in AT TIME ZONE %s)::date BETWEEN %s AND %s""",
                (TZ, since, until))
            new_from_ads = cur.fetchone()[0] or 0

            # (4) Tách theo từng ad_id (last-touch qua v_conversation_ad)
            cur.execute(
                f"""SELECT r.ad_id, COUNT(DISTINCT m.psid) AS conversations
                    FROM messages m JOIN v_conversation_ad r ON r.psid=m.psid
                    WHERE m.direction='in'
                      AND (m.sent_at AT TIME ZONE %s)::date BETWEEN %s AND %s
                    GROUP BY r.ad_id ORDER BY conversations DESC""",
                (TZ, since, until))
            by_ad = [{"ad_id": a, "conversations": c} for a, c in cur.fetchall()]

        return {
            "since": since, "until": until, "tz": TZ,
            "total_conversations": total,          # Tổng cuộc hội thoại
            "new_contacts": new_contacts,          # Người liên hệ MỚI (lần đầu)
            "returning_contacts": max(total - new_contacts, 0),  # Người liên hệ CŨ
            "new_contacts_from_ads": new_from_ads, # MỚI từ quảng cáo (đối soát QLQC)
            "by_ad": by_ad,                        # tách theo ad_id
        }
    except Exception as e:
        return Response(
            status_code=500,
            content=json.dumps({"error": str(e)}, ensure_ascii=False),
            media_type="application/json",
        )


# ============================================================
# API ATTRIBUTION — Doanh thu/ROAS THẬT theo ad_id (last-touch)
#   GET /api/attribution?since=YYYY-MM-DD&until=YYYY-MM-DD[&key=...]
#   Nối: ad_referrals (ad_id↔psid) · messages · orders · ad_spend
# ============================================================
@app.get("/api/attribution")
async def api_attribution(since: str, until: str, key: str | None = None):
    if METRICS_KEY and key != METRICS_KEY:
        return Response(status_code=401, content="unauthorized")
    sql = f"""
    WITH conv_ad AS (                       -- last-touch: 1 ad_id / psid
        SELECT DISTINCT ON (psid) psid, ad_id
        FROM ad_referrals WHERE source='ADS'
        ORDER BY psid, referred_at DESC NULLS LAST
    ),
    firsts AS (
        SELECT psid, MIN(sent_at) AS fi FROM messages WHERE direction='in' GROUP BY psid
    ),
    spend AS (
        SELECT ad_id, SUM(spend) AS spend, SUM(impressions) AS impr,
               SUM(COALESCE(results_msg,0)) AS results_msg
        FROM ad_spend WHERE date BETWEEN %s AND %s GROUP BY ad_id
    ),
    conv AS (
        SELECT ca.ad_id, COUNT(DISTINCT m.psid) AS conversations
        FROM messages m JOIN conv_ad ca ON ca.psid=m.psid
        WHERE m.direction='in' AND (m.sent_at AT TIME ZONE %s)::date BETWEEN %s AND %s
        GROUP BY ca.ad_id
    ),
    newc AS (
        SELECT ca.ad_id, COUNT(DISTINCT f.psid) AS new_contacts
        FROM firsts f JOIN conv_ad ca ON ca.psid=f.psid
        WHERE (f.fi AT TIME ZONE %s)::date BETWEEN %s AND %s
        GROUP BY ca.ad_id
    ),
    rev AS (
        SELECT ca.ad_id, COUNT(DISTINCT o.order_id) AS orders,
               COALESCE(SUM(o.revenue),0) AS revenue
        FROM orders o
        LEFT JOIN customers c ON c.phone=o.phone
        JOIN conv_ad ca ON ca.psid = COALESCE(o.psid, c.psid)
        WHERE (o.ordered_at AT TIME ZONE %s)::date BETWEEN %s AND %s
        GROUP BY ca.ad_id
    ),
    ads AS (
        SELECT ad_id FROM spend
        UNION SELECT ad_id FROM conv
        UNION SELECT ad_id FROM newc
        UNION SELECT ad_id FROM rev
    )
    SELECT a.ad_id,
           COALESCE(s.spend,0)        AS spend,
           COALESCE(s.results_msg,0)  AS results_msg,
           COALESCE(cv.conversations,0) AS conversations,
           COALESCE(n.new_contacts,0) AS new_contacts,
           COALESCE(rv.orders,0)      AS orders,
           COALESCE(rv.revenue,0)     AS revenue,
           CASE WHEN COALESCE(s.spend,0)>0
                THEN ROUND(COALESCE(rv.revenue,0)::numeric / s.spend, 2) END AS roas
    FROM ads a
    LEFT JOIN spend s ON s.ad_id=a.ad_id
    LEFT JOIN conv  cv ON cv.ad_id=a.ad_id
    LEFT JOIN newc  n ON n.ad_id=a.ad_id
    LEFT JOIN rev   rv ON rv.ad_id=a.ad_id
    WHERE a.ad_id IS NOT NULL
    ORDER BY revenue DESC
    """
    params = (since, until, TZ, since, until, TZ, since, until, TZ, since, until)
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            cols = ["ad_id", "spend", "results_msg", "conversations",
                    "new_contacts", "orders", "revenue", "roas"]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        tot_spend = sum(r["spend"] or 0 for r in rows)
        tot_rev = sum(r["revenue"] or 0 for r in rows)
        return {
            "since": since, "until": until, "tz": TZ,
            "summary": {
                "spend": tot_spend, "revenue": tot_rev,
                "roas": round(tot_rev / tot_spend, 2) if tot_spend else None,
                "ads_count": len(rows),
            },
            "by_ad": rows,
        }
    except Exception as e:
        return Response(
            status_code=500,
            content=json.dumps({"error": str(e)}, ensure_ascii=False),
            media_type="application/json",
        )


# ============================================================
# ĐỊNH DANH & ĐƠN HÀNG — đấu nối POS/CRM (SĐT là khóa chính)
# ============================================================
def norm_phone(p):
    """Chuẩn hoá SĐT VN về dạng 0xxxxxxxxx để khớp định danh."""
    if not p:
        return None
    d = re.sub(r"\D", "", str(p))
    if not d:
        return None
    if d.startswith("84"):
        d = "0" + d[2:]
    elif not d.startswith("0"):
        d = "0" + d
    return d


def _auth(request, k):
    key = request.query_params.get("key") or request.headers.get("x-api-key")
    return (not k) or (key == k)


# ---- Nhận đơn realtime từ POS/CRM (webhook) ----
@app.post("/api/orders")
async def ingest_order(request: Request):
    if not _auth(request, ORDERS_KEY):
        return Response(status_code=401, content="unauthorized")
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="invalid json")
    items = body if isinstance(body, list) else [body]
    n = 0
    with db() as conn, conn.cursor() as cur:
        for o in items:
            oid = str(o.get("order_id") or o.get("id") or "").strip()
            if not oid:
                continue
            phone = norm_phone(o.get("phone"))
            psid = o.get("psid")
            try:
                revenue = int(round(float(o.get("revenue") or o.get("total") or 0)))
            except (TypeError, ValueError):
                revenue = 0
            products = o.get("products") or o.get("items_text")
            branch = o.get("branch")
            ordered_at = (o.get("ordered_at") or o.get("created_at")
                          or datetime.now(timezone.utc).isoformat())
            cur.execute(
                """INSERT INTO orders(order_id, phone, psid, revenue, products, branch, ordered_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (order_id) DO UPDATE SET
                     phone=EXCLUDED.phone, psid=EXCLUDED.psid, revenue=EXCLUDED.revenue,
                     products=EXCLUDED.products, branch=EXCLUDED.branch,
                     ordered_at=EXCLUDED.ordered_at""",
                (oid, phone, psid, revenue, products, branch, ordered_at))
            # Nếu POS gửi kèm psid + phone → nối định danh vào customers
            if psid and phone:
                cur.execute(
                    "UPDATE customers SET phone=%s WHERE psid=%s AND (phone IS NULL OR phone='')",
                    (phone, psid))
            n += 1
    return {"ingested": n}


# ---- Nối SĐT ↔ psid (sale/inbox tag hội thoại) — MẮT XÍCH SỐNG CÒN ----
@app.post("/api/link")
async def link_identity(request: Request):
    if not _auth(request, ORDERS_KEY):
        return Response(status_code=401, content="unauthorized")
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="invalid json")
    psid = body.get("psid")
    phone = norm_phone(body.get("phone"))
    name = body.get("name")
    if not psid or not phone:
        return Response(status_code=400, content="need psid + phone")
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO customers(psid, platform, page_id, phone, name, first_seen)
               VALUES (%s,'facebook','',%s,%s,now())
               ON CONFLICT (psid) DO UPDATE SET
                 phone=EXCLUDED.phone,
                 name=COALESCE(EXCLUDED.name, customers.name)""",
            (psid, phone, name))
    return {"linked": {"psid": psid, "phone": phone}}


# ---- Backfill: quét tin nhắn CŨ, tự điền SĐT cho khách chưa gắn ----
@app.post("/api/backfill-phones")
async def backfill_phones(request: Request):
    if not _auth(request, ORDERS_KEY):
        return Response(status_code=401, content="unauthorized")
    found = {}
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.psid, m.body FROM messages m
               JOIN customers c ON c.psid = m.psid
               WHERE m.direction='in' AND m.body IS NOT NULL
                 AND (c.phone IS NULL OR c.phone='')
               ORDER BY m.psid, m.sent_at ASC""")
        for psid, body in cur.fetchall():     # tin sớm nhất trước → lấy SĐT đầu tiên
            if psid in found:
                continue
            ph = extract_vn_phone(body)
            if ph:
                found[psid] = ph
        for psid, ph in found.items():
            cur.execute(
                "UPDATE customers SET phone=%s WHERE psid=%s AND (phone IS NULL OR phone='')",
                (ph, psid))
    return {"updated": len(found)}


# ============================================================
# CLV — first-touch (ad đầu tiên kéo khách về), khóa = SĐT
# ============================================================
_CLV_CTE = """
WITH ph_psid AS (
    SELECT phone, psid FROM customers WHERE phone IS NOT NULL AND phone<>''
),
first_ref AS (
    SELECT DISTINCT ON (psid) psid, ad_id, referred_at
    FROM ad_referrals WHERE source='ADS'
    ORDER BY psid, referred_at ASC NULLS LAST
),
ft AS (                                   -- first-touch ad theo từng SĐT
    SELECT DISTINCT ON (pp.phone) pp.phone, fr.ad_id AS first_ad, fr.referred_at
    FROM ph_psid pp JOIN first_ref fr ON fr.psid=pp.psid
    ORDER BY pp.phone, fr.referred_at ASC NULLS LAST
),
clv AS (
    SELECT phone, COUNT(*) AS orders, COALESCE(SUM(revenue),0) AS clv,
           MIN(ordered_at) AS first_order, MAX(ordered_at) AS last_order
    FROM orders WHERE phone IS NOT NULL AND phone<>'' GROUP BY phone
)
"""


@app.get("/api/clv")
async def api_clv(request: Request, limit: int = 500):
    if not _auth(request, METRICS_KEY):
        return Response(status_code=401, content="unauthorized")
    sql = _CLV_CTE + """
    SELECT c.phone, ft.first_ad, c.orders, c.clv, c.first_order, c.last_order
    FROM clv c LEFT JOIN ft ON ft.phone=c.phone
    ORDER BY c.clv DESC LIMIT %s
    """
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(sql, (limit,))
            cols = ["phone", "first_ad", "orders", "clv", "first_order", "last_order"]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            for r in rows:                       # ép datetime -> ISO cho JSON
                for k in ("first_order", "last_order"):
                    if r[k] is not None:
                        r[k] = r[k].isoformat()
        return {"customers": rows, "count": len(rows)}
    except Exception as e:
        return Response(status_code=500,
                        content=json.dumps({"error": str(e)}, ensure_ascii=False),
                        media_type="application/json")


@app.get("/api/clv/by-ad")
async def api_clv_by_ad(request: Request):
    if not _auth(request, METRICS_KEY):
        return Response(status_code=401, content="unauthorized")
    sql = _CLV_CTE + """
    SELECT COALESCE(ft.first_ad,'(organic/unknown)') AS first_ad,
           COUNT(DISTINCT c.phone) AS customers,
           COALESCE(SUM(c.clv),0)  AS clv,
           ROUND(AVG(c.clv))       AS avg_clv,
           COALESCE(SUM(c.orders),0) AS orders
    FROM clv c LEFT JOIN ft ON ft.phone=c.phone
    GROUP BY 1 ORDER BY clv DESC
    """
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(sql)
            cols = ["first_ad", "customers", "clv", "avg_clv", "orders"]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {"by_ad": rows, "count": len(rows)}
    except Exception as e:
        return Response(status_code=500,
                        content=json.dumps({"error": str(e)}, ensure_ascii=False),
                        media_type="application/json")
