# Hướng dẫn test webhook bằng Mac + ngrok (≈10 phút)

Mục tiêu: thấy tin nhắn + `ad_id` từ quảng cáo chảy về máy anh, trước khi gắn database.

## 1. Mở Terminal, vào thư mục dự án
```bash
cd ~/Claude/Projects/"Cent - Ads"/meta-inbox-attribution
```

## 2. Cài & chạy server test
```bash
python3 -m venv venv
source venv/bin/activate
pip install fastapi "uvicorn[standard]"
export VERIFY_TOKEN=cent_beauty_2026_secret
uvicorn webhook_test:app --port 8000
```
Thấy dòng `Uvicorn running on http://0.0.0.0:8000` là OK. **Để nguyên cửa sổ này chạy.**

## 3. Mở Terminal THỨ HAI — tạo HTTPS công khai
**Cách A — ngrok** (cần đăng ký free + authtoken 1 lần):
```bash
brew install ngrok
ngrok config add-authtoken <AUTHTOKEN_CUA_ANH>   # lấy ở dashboard.ngrok.com
ngrok http 8000
```
**Cách B — cloudflared** (KHÔNG cần đăng ký, em khuyên dùng nếu ngại tạo tài khoản):
```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```
Copy URL HTTPS hiện ra, ví dụ `https://abc-123.ngrok-free.app` hoặc `https://xyz.trycloudflare.com`.

## 4. Khai báo webhook trong App (anh tự bấm)
Trong **Cent Business 2026 → use case Messenger → tab Webhooks**:
- **Callback URL:** `<URL_HTTPS_ô_trên>/webhook`
- **Verify token:** `cent_beauty_2026_secret` (đúng giá trị ở bước 2)
- Bấm **Xác minh và lưu** → cửa sổ Terminal 1 phải in `✅ Webhook verified OK`.
- **Subscribe fields:** tích `messages`, `messaging_postbacks`, `messaging_referrals`.
- **Subscribe page:** chọn 1 fanpage để gắn webhook (chọn page test trước).

## 5. Bắn thử
- Cách nhanh: từ một tài khoản FB khác, nhắn tin vào fanpage → Terminal 1 in `💬 MESSAGE`.
- Test attribution thật: bấm vào **một quảng cáo Click-to-Messenger** đang chạy rồi nhắn → Terminal 1 in `🎯 AD REFERRAL ad_id=...`.

## 6. Báo em kết quả
Anh gửi em vài dòng in ra (che PSID nếu muốn) hoặc nội dung `events.jsonl`. Thấy `ad_id` về là mình chuyển sang bản chính thức (gắn Postgres + nối Ads + báo cáo ROAS).

---
**Lưu ý:** giữ token/authtoken bí mật, đừng dán lên chat công khai. Tắt máy/đóng terminal là tunnel mất — bình thường ở giai đoạn test.
