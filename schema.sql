-- Cent Beauty — Schema đo lường Inbox → Quảng cáo → Doanh thu (PostgreSQL)
-- Khóa nối chuỗi attribution: ad_id  ↔  psid  ↔  phone  ↔  order

-- 1) Khách hàng (định danh trên nền tảng + nối sang SĐT thật khi sale chốt)
CREATE TABLE IF NOT EXISTS customers (
    psid            TEXT PRIMARY KEY,           -- Page-scoped ID / IG-scoped ID
    platform        TEXT NOT NULL,              -- 'facebook' | 'instagram'
    page_id         TEXT NOT NULL,
    name            TEXT,
    phone           TEXT,                        -- điền khi sale lấy được SĐT (mắt xích doanh thu)
    first_seen      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);

-- 2) Hội thoại (1 khách có thể nhiều hội thoại)
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,           -- t_xxx (FB) hoặc id Conversations API
    psid            TEXT REFERENCES customers(psid),
    page_id         TEXT NOT NULL,
    platform        TEXT NOT NULL,
    first_message_at TIMESTAMPTZ,
    last_message_at  TIMESTAMPTZ,
    message_count   INT DEFAULT 0,
    is_new_contact  BOOLEAN DEFAULT FALSE,      -- TRUE nếu đây là khách MỚI (tin đầu tiên)
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 3) Tin nhắn (lưu thô để truy vết & QC)
CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    conversation_id TEXT,
    psid            TEXT,
    direction       TEXT,                        -- 'in' (khách gửi) | 'out' (page gửi)
    body            TEXT,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

-- 4) Attribution quảng cáo — bắt từ webhook 'referral' (BẢNG QUAN TRỌNG NHẤT)
CREATE TABLE IF NOT EXISTS ad_referrals (
    id              BIGSERIAL PRIMARY KEY,
    psid            TEXT,
    conversation_id TEXT,
    page_id         TEXT,
    ad_id           TEXT,                        -- ID quảng cáo sinh ra hội thoại
    ref             TEXT,                        -- tham số ref tùy biến (m.me?ref=...)
    source          TEXT,                        -- 'ADS' | 'SHORTLINK' | ...
    type            TEXT,                        -- 'OPEN_THREAD' | ...
    ctwa_clid       TEXT,                        -- click id (nếu có)
    referred_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ref_ad ON ad_referrals(ad_id);
CREATE INDEX IF NOT EXISTS idx_ref_psid ON ad_referrals(psid);

-- 5) Đơn hàng (đổ từ POS/CRM; nối qua phone hoặc psid)
CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    phone           TEXT,
    psid            TEXT,                        -- nếu POS lưu được psid thì nối thẳng
    revenue         NUMERIC(14,0),               -- tiền thực thu (VND)
    products        TEXT,                        -- liệt kê sản phẩm/dịch vụ
    branch          TEXT,                        -- cơ sở
    ordered_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_orders_phone ON orders(phone);

-- 6) Chi tiêu quảng cáo (đổ từ Ads MCP/Graph API theo ngày × ad_id)
CREATE TABLE IF NOT EXISTS ad_spend (
    ad_id           TEXT,
    date            DATE,
    spend           NUMERIC(14,0),
    impressions     BIGINT,
    PRIMARY KEY (ad_id, date)
);

-- ============================================================
-- VIEW 1 — LAST-TOUCH: ad_id GẦN NHẤT của mỗi hội thoại
--   (1 hội thoại có nhiều ad_id → lấy referral mới nhất theo referred_at)
-- ============================================================
CREATE OR REPLACE VIEW v_conversation_ad AS
SELECT DISTINCT ON (psid)
       psid, ad_id, ref, source, referred_at
FROM ad_referrals
WHERE source = 'ADS'
ORDER BY psid, referred_at DESC NULLS LAST;

-- ============================================================
-- VIEW 2 — TỈ LỆ GHI NHẬN theo ngày
--   = (hội thoại có ad_id) / (tổng hội thoại có tin nhắn), cùng ngày
-- ============================================================
CREATE OR REPLACE VIEW v_daily_coverage AS
SELECT date(m.sent_at)                                              AS ngay,
       count(DISTINCT m.psid)                                       AS tong_hoithoai,
       count(DISTINCT m.psid) FILTER (WHERE ca.psid IS NOT NULL)    AS co_adid,
       round(100.0 * count(DISTINCT m.psid) FILTER (WHERE ca.psid IS NOT NULL)
             / NULLIF(count(DISTINCT m.psid), 0), 1)                AS ty_le_ghi_nhan_pct
FROM messages m
LEFT JOIN v_conversation_ad ca ON ca.psid = m.psid
WHERE m.sent_at IS NOT NULL
GROUP BY date(m.sent_at)
ORDER BY 1 DESC;

-- ============================================================
-- VIEW 3 — ROAS theo từng ad_id (dùng LAST-TOUCH)
--   tin nhắn mới  ·  khách chốt  ·  doanh thu  ·  chi tiêu  ·  ROAS
-- ============================================================
CREATE OR REPLACE VIEW v_ad_attribution AS
SELECT
    r.ad_id,
    COUNT(DISTINCT r.psid)                               AS tin_nhan_moi,
    COUNT(DISTINCT o.order_id)                           AS khach_chot,
    COALESCE(SUM(o.revenue), 0)                          AS doanh_thu,
    COALESCE(s.spend, 0)                                 AS chi_tieu,
    CASE WHEN COALESCE(s.spend,0) > 0
         THEN ROUND(COALESCE(SUM(o.revenue),0)::NUMERIC / s.spend, 2)
         ELSE NULL END                                   AS roas
FROM v_conversation_ad r                       -- last-touch: 1 ad_id / hội thoại
LEFT JOIN customers c ON c.psid = r.psid
LEFT JOIN orders    o ON (o.psid = r.psid OR o.phone = c.phone)
LEFT JOIN (SELECT ad_id, SUM(spend) spend FROM ad_spend GROUP BY ad_id) s
       ON s.ad_id = r.ad_id
GROUP BY r.ad_id, s.spend
ORDER BY doanh_thu DESC;
