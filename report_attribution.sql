-- Báo cáo đo lường ad_id — chạy: psql ... -f report_attribution.sql
-- (hoặc copy từng câu vào Terminal database trong Coolify)

-- 1) TỈ LỆ GHI NHẬN theo ngày = hội thoại có ad_id / tổng hội thoại có tin nhắn
SELECT * FROM v_daily_coverage;

-- 2) TỈ LỆ GHI NHẬN tổng (toàn bộ dữ liệu)
SELECT
  count(DISTINCT m.psid)                                    AS tong_hoithoai,
  count(DISTINCT m.psid) FILTER (WHERE ca.psid IS NOT NULL) AS co_adid,
  round(100.0*count(DISTINCT m.psid) FILTER (WHERE ca.psid IS NOT NULL)
        / NULLIF(count(DISTINCT m.psid),0),1)               AS ty_le_ghi_nhan_pct
FROM messages m
LEFT JOIN v_conversation_ad ca ON ca.psid = m.psid;

-- 3) Số hội thoại theo từng quảng cáo (last-touch)
SELECT ad_id, count(*) AS so_hoithoai
FROM v_conversation_ad
GROUP BY ad_id
ORDER BY 2 DESC;

-- 4) ĐỘ TRUNG THỰC BẮT (capture fidelity) — so với Meta:
--    Thay <N> = số "Messaging conversations started" Meta Ads báo cho các trang
--    trong CÙNG ngày, rồi tính: (co_adid của ngày đó) / <N> * 100.
--    (Cần đối chiếu thủ công hoặc nối ad_spend + results từ Ads MCP.)

-- 5) Chi tiết hội thoại có ad_id (kiểm tra)
SELECT ca.psid, ca.ad_id, ca.referred_at,
       (SELECT left(body,50) FROM messages mm WHERE mm.psid=ca.psid ORDER BY sent_at LIMIT 1) AS tin_dau
FROM v_conversation_ad ca
ORDER BY ca.referred_at DESC;
