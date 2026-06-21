# Cent Beauty — Hệ đo lường Inbox → Quảng cáo → Doanh thu

Mục tiêu: với mỗi khách nhắn tin vào fanpage/Instagram, biết được **đến từ quảng cáo nào (ad_id)**, **chốt bao nhiêu tiền**, **mua sản phẩm gì** — để tính ROAS thật ở cấp từng quảng cáo.

> Tài liệu này phục vụ anh Nguyễn Xuân Khánh (CEO Cent Beauty) tự build cùng Claude. Phạm vi: 3–5 fanpage + Instagram.

---

## ⚠️ Sự thật kỹ thuật quyết định kiến trúc (đọc trước)

**`ad_id` chỉ xuất hiện ở tin nhắn ĐẦU TIÊN qua Webhook real-time.** Khi khách bấm quảng cáo Click-to-Messenger và nhắn tin, Meta gửi kèm object `referral` chứa `ad_id`. Object này **KHÔNG lưu lại** trong Conversations API để lấy về sau.

Hệ quả:
- **Webhook là xương sống bắt buộc** — phải chạy 24/7 để không bỏ sót `ad_id` nào.
- **Backfill lịch sử (Conversations API) chỉ lấy được nội dung & số lượng tin**, KHÔNG lấy lại được `ad_id` của hội thoại cũ. Attribution chỉ đầy đủ kể từ ngày bật webhook.
- → **Bật webhook càng sớm càng tốt.** Mỗi ngày chậm = mất attribution của ngần ấy khách.

**Mắt xích "ra bao nhiêu tiền":** Meta chỉ cho tới bước hội thoại. Để nối sang doanh thu, cần sale/POS gắn **số điện thoại khách** vào hội thoại (PSID → SĐT → đơn hàng). Phần này nằm ở quy trình vận hành + bảng `orders`, không phải API Meta.

---

## Kiến trúc tổng thể

```
[Quảng cáo CTM/CTD]
        │ khách bấm + nhắn tin
        ▼
[Fanpage / Instagram] ──webhook (real-time)──► [webhook_server.py] ──► [PostgreSQL]
        │                                              (bắt ad_id)         ▲
        └──Conversations API (backfill)──► [fetch_conversations.py] ───────┘
                                                                            │
[Ads MCP / Graph API] ──chi tiêu theo ad_id──► [ad_spend] ─────────────────┤
[POS/CRM] ──đơn hàng + SĐT + sản phẩm──► [orders] ─────────────────────────┘
                                                                            │
                                                                            ▼
                                                          [Báo cáo ROAS theo ad_id]
```

---

## Bước 1 — Tạo App & quyền

1. `developers.facebook.com` → Create App → loại **Business** → gắn vào BM `170366164270567`.
2. Thêm sản phẩm: **Messenger**, **Instagram**, **Webhooks**.
3. Quyền cần xin (App Review cho Advanced Access):
   - `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`
   - `pages_messaging` ← đọc/nhận tin Messenger
   - `instagram_basic`, `instagram_manage_messages` ← tin nhắn Instagram
   - `business_management`

> Page anh là admin → test ngay ở chế độ Development. Production cần App Review (3–7 ngày, phải quay video mô tả use-case "đo lường chăm sóc khách hàng nội bộ").

## Bước 2 — Token vĩnh viễn (System User)

1. Business Settings → Users → **System Users** → tạo mới.
2. Gán 3–5 fanpage + IG account cho System User.
3. Generate Token với các quyền ở Bước 1 → token **không hết hạn**. Lưu vào `.env`.

## Bước 3 — Cài & chạy

```bash
pip install -r requirements.txt
cp .env.example .env      # điền token, page id, DB
psql < schema.sql          # tạo bảng
```

- **Webhook (bắt buộc, chạy nền):** `uvicorn webhook_server:app --host 0.0.0.0 --port 8000`
  Cần HTTPS public. Khởi đầu nhanh: `ngrok http 8000` (test) → sau dùng VPS + domain + SSL.
  Vào App → Webhooks → đăng ký `messages`, `messaging_referrals`, `messaging_postbacks` (object Page) và `messages` (object Instagram). Verify Token = giá trị `VERIFY_TOKEN` trong `.env`.
- **Backfill lịch sử:** `python fetch_conversations.py` (chạy 1 lần để nạp tin cũ).

## Bước 4 — Nối doanh thu

Khi sale chốt khách, ghi `psid` ↔ `phone` ↔ `order` vào bảng `orders` (qua POS hoặc form nội bộ). Báo cáo cuối JOIN: `ad_referrals.ad_id` → `conversations` → `orders.revenue/products` và `ad_spend.spend` để ra **ROAS theo ad_id**.

---

## File trong bộ này

| File | Vai trò |
|---|---|
| `webhook_server.py` | Nhận event real-time, bắt `ad_id`, lưu DB. **Xương sống.** |
| `fetch_conversations.py` | Backfill nội dung & số lượng hội thoại FB + IG. |
| `schema.sql` | Mô hình dữ liệu attribution (6 bảng). |
| `requirements.txt` / `.env.example` | Phụ thuộc & cấu hình. |

## Rủi ro & lưu ý
- **Webhook chết = mất dữ liệu** → cần giám sát uptime, có retry/log.
- **Chính sách dữ liệu Meta:** chỉ dùng nội bộ đo lường/CSKH, không bán/chia sẻ; tuân thủ lưu trữ & xoá theo yêu cầu.
- **IG attribution** hẹp hơn FB: ad_id qua Click-to-IG-Direct có thể thiếu trường so với Messenger — kiểm thử thực tế.
- **Rate limit Graph API**: backfill nhiều page nên giãn nhịp, xử lý phân trang.
