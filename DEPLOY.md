# Triển khai production — Cent Inbox Attribution

## Khuyến nghị hạ tầng (đọc phần tư vấn trong chat)

**Chọn cloud Việt Nam (BizFly Cloud / FPT Cloud)** vì dữ liệu chứa **PII khách hàng** (SĐT, tin nhắn) → tuân thủ **Luật Bảo vệ dữ liệu cá nhân (PDPL, hiệu lực 01/01/2026)**, tránh thủ tục chuyển dữ liệu xuyên biên giới. Cài **Coolify** để quản lý như một PaaS riêng cho mọi hệ thống của Cent.

## Spec khởi điểm đề xuất
- **2 vCPU · 4 GB RAM · 80 GB SSD**, Ubuntu 24.04.
- Đủ cho: webhook + Postgres + Coolify + 1 app báo cáo BI. Scale lên khi tải tăng.

---

## Cách A — Coolify (khuyên dùng, dễ như Render nhưng trên server của anh)

1. Tạo VPS Ubuntu 24.04 ở BizFly/FPT.
2. SSH vào, cài Coolify 1 lệnh:
   ```bash
   curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
   ```
3. Mở `http://<IP-VPS>:8000`, tạo tài khoản admin Coolify.
4. New Resource → Docker/Git → trỏ tới thư mục này (có `Dockerfile`).
5. Add Database → PostgreSQL (Coolify tạo sẵn), copy connection string vào biến `DATABASE_URL`.
6. Đặt domain cho app → Coolify tự cấp HTTPS. Callback URL = `https://<domain>/webhook`.

## Cách B — docker-compose thuần (nếu không dùng Coolify)

1. Trỏ **A-record** domain (vd `webhook.centbeauty.vn`) về IP VPS.
2. Sửa `Caddyfile` (domain) + đổi mật khẩu DB trong `docker-compose.yml`.
3. Cài Docker rồi chạy:
   ```bash
   docker compose up -d --build
   docker compose logs -f web
   ```
4. Callback URL webhook = `https://<domain>/webhook`, verify token = `cent_beauty_2026_secret`.

---

## Sau khi server thật chạy
1. Vào Meta → app Cent Business 2026 → Webhooks → đổi **Callback URL** sang domain production (thay URL cloudflared tạm).
2. Subscribe page (cần Page Access Token — anh tự tạo/copy).
3. Backfill lịch sử: chạy `fetch_conversations.py` (điền `PAGE_TOKENS` trong `.env`).
4. Nối chi tiêu Ads theo `ad_id` (script `sync_ad_spend.py` — em viết bước sau) → báo cáo ROAS.

## Lưu ý bảo mật
- KHÔNG commit `.env`, token, mật khẩu lên git.
- Bật firewall: chỉ mở 22 (SSH), 80, 443.
- Sao lưu Postgres định kỳ (Coolify có sẵn scheduled backup).
