"""
Cent Beauty — Webhook TEST (không cần database).
Mục tiêu: xác nhận Meta gửi được tin nhắn + bắt được ad_id về máy anh.
In mọi sự kiện ra màn hình và ghi vào events.jsonl để soi lại.

Chạy:
  pip install fastapi "uvicorn[standard]"
  export VERIFY_TOKEN=cent_beauty_2026_secret
  uvicorn webhook_test:app --port 8000
"""
import os
import json
from datetime import datetime

from fastapi import FastAPI, Request, Response

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "cent_beauty_2026_secret")
LOG_FILE = "events.jsonl"

app = FastAPI(title="Cent Webhook TEST")


@app.get("/webhook")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        print("✅ Webhook verified OK")
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    print("❌ Verify token sai")
    return Response(status_code=403)


@app.post("/webhook")
async def receive(request: Request):
    data = await request.json()
    # ghi thô để soi lại
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")

    for entry in data.get("entry", []):
        for ev in entry.get("messaging", []):
            psid = ev.get("sender", {}).get("id")
            ref = (
                ev.get("referral")
                or ev.get("message", {}).get("referral")
                or ev.get("postback", {}).get("referral")
            )
            text = ev.get("message", {}).get("text")
            if ref:
                print(f"🎯 AD REFERRAL  psid={psid}  ad_id={ref.get('ad_id')}  "
                      f"ref={ref.get('ref')}  source={ref.get('source')}")
            if text:
                print(f"💬 MESSAGE      psid={psid}  text={text!r}")
    return Response(content="EVENT_RECEIVED", media_type="text/plain")


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}
