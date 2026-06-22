# RUNBOOK DEPLOY — Cent Inbox Attribution (bản cập nhật API báo cáo)

Mục tiêu: deploy bản mới (API metrics/attribution/CLV + tự nhận diện SĐT + ads_sync) rồi chạy backfill để thấy số thật.

> Em (Claude) không deploy hộ được (môi trường chặn GitHub + VPS). Đây là các lệnh anh chạy.

---

## 0. Trước khi deploy — bảo mật (LÀM NGAY)
- [ ] **Revoke PAT GitHub `cent-deploy`** đã lộ trước đây.
- [ ] Đặt **chuỗi mạnh** cho `METRICS_API_KEY` và `ORDERS_API_KEY` (vd 32 ký tự ngẫu nhiên).

## 1. Tạo System User token (cho ads_sync)
1. business.facebook.com → **Business Settings → Users → System Users**.
2. Tạo/mở System User → **Add Assets**: gán 2 tài khoản quảng cáo (Cent Beauty-01, Celvia Derma 01) quyền *Manage/View*.
3. **Generate token** → chọn app **Cent Beauty 2023** → quyền **`ads_read`** → chọn **không hết hạn** (System User token mặc định không hết hạn). Copy token.

## 2. Khai báo biến môi trường (Coolify → app web → Environment)
```
REPORT_TZ=Asia/Ho_Chi_Minh
METRICS_API_KEY=<chuỗi mạnh tự đặt>
ORDERS_API_KEY=<chuỗi mạnh tự đặt>
META_ACCESS_TOKEN=<token vừa tạo>
AD_ACCOUNT_IDS=672539241340824,1574799437088902
GRAPH_API_VERSION=v21.0
```
(VERIFY_TOKEN và DATABASE_URL giữ nguyên như đang chạy.)

## 3. Push code → Coolify tự build lại
```bash
cd cent-inbox-attribution
git pull            # nếu cần đồng bộ
git push origin main
```
Coolify nhận webhook GitHub → build Dockerfile (đã thêm COPY ads_sync.py) → redeploy.

## 4. Kiểm tra sống
```bash
DOMAIN="https://<domain-cua-anh>"
curl "$DOMAIN/health"
curl "$DOMAIN/api/metrics?since=2026-06-01&until=2026-06-21&key=$METRICS_API_KEY"
```

## 5. Backfill SĐT từ tin nhắn CŨ (chạy 1 lần)
```bash
curl -X POST "$DOMAIN/api/backfill-phones?key=$ORDERS_API_KEY"
# -> {"updated": N}  (số khách được tự gắn SĐT từ nội dung chat cũ)
```

## 6. Sync quảng cáo lần đầu + đặt cron
Chạy 1 lần cho dải ngày mong muốn (Coolify → app → **Terminal/Exec** vào container web):
```bash
python ads_sync.py 2026-05-01 2026-06-21
```
Đặt lịch tự động (Coolify → **Scheduled Tasks**): lệnh `python ads_sync.py`, cron `0 6 * * *` (6h sáng mỗi ngày).

## 7. Nghiệm thu số thật
```bash
curl "$DOMAIN/api/clv?key=$METRICS_API_KEY"
curl "$DOMAIN/api/clv/by-ad?key=$METRICS_API_KEY"
curl "$DOMAIN/api/attribution?since=2026-05-01&until=2026-06-21&key=$METRICS_API_KEY"
```

## 8. Đấu nối POS/CRM (khi sẵn sàng) — bắn đơn về:
```bash
curl -X POST "$DOMAIN/api/orders?key=$ORDERS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"order_id":"HD123","phone":"0909123456","revenue":5000000,
       "products":"Triệt fullbody","branch":"Cầu Giấy","ordered_at":"2026-06-21T10:30:00+07:00"}'
```

---

## Lưu ý quan trọng
- **`ad_referrals` chỉ có `ad_id` khi khách bấm Click-to-Messenger.** Tin organic không có ad → CLV-by-ad rơi vào `(organic/unknown)`. Phải duy trì ≥1 chiến dịch CTM.
- **Cột ROAS/CLV chỉ có số khi bảng `orders` có dữ liệu** (Bước 8). Không có đơn = ROAS rỗng.
- Nên chuyển domain sslip.io → **domain riêng + SSL** trước khi mở cho nhiều người dùng.
- Backfill (Bước 5) có thể chạy lại nhiều lần, an toàn (chỉ điền khi SĐT đang trống).
