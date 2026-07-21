# Lôtô Lab dashboard

Dashboard cho ba data lake XSMB/XSMN/XSMT. Trình duyệt chỉ gọi Worker API và không nhận credential hoặc Gold Parquet.

## Chạy local

Từ thư mục `frontend/`:

```bash
npm ci
npm run dev
```

Mở URL do terminal in ra, thường là `http://localhost:3000` hoặc cổng kế tiếp nếu cổng đó đang bận.
Nếu bucket serving local đang trống, Worker tự đọc ba snapshot JSON đã bundle trong `public/data/`.

## Dashboard đang hiển thị gì?

- Kết quả mới nhất cho cả ba miền và bộ lọc theo đài ở XSMN/XSMT.
- Heatmap tần suất `00`–`99` theo 30/90/180/365 kỳ.
- Model tần suất, khoảng vắng và model cân bằng 60/40.
- Walk-forward backtest 90 kỳ, chạy độc lập trên từng đài để không nhìn trước kết quả đài khác cùng ngày.
- Tín hiệu nóng/lạnh và đà 7 kỳ so với 30 kỳ.
- Trạng thái nguồn R2/demo, dataset version và độ đồng bộ manifest.
- Explorer lịch sử theo miền, đài, khoảng ngày và số `00`–`99`; URL giữ nguyên filter và nút tải thêm dùng cursor.

Các model chỉ là heuristic mô tả, không phải dự báo xác suất trúng hoặc khuyến nghị đặt cược.

## Luồng dữ liệu

Trang tổng quan đọc `regions/xsmb.json`, `regions/xsmn.json`, `regions/xsmt.json` từ binding R2 `LOTTERY_DATA`.
Explorer đọc metadata `v2/regions/<region>/latest.json`, rồi lazy-load shard bất biến theo release/đài/năm. Nếu v2 tạm
lỗi, UI giữ dashboard compact thay vì giả vờ lịch sử đầy đủ. Nếu compact object chưa tồn tại, Worker fallback về:

```text
public/data/xsmb-demo.json
public/data/xsmn-demo.json
public/data/xsmt-demo.json
```

`npm run data:refresh` đọc `.env` ở root, kiểm tra các object Gold được `manifests/latest.json` tham chiếu và chỉ cập nhật
ba compact snapshot demo đã bundle. Bundle v2 đầy đủ không được nhét vào frontend; GitHub Action export rồi ingest shard
theo đài/năm trực tiếp vào R2. Payload compact giữ 455 kỳ gần nhất cho mỗi đài; v2 giữ toàn bộ lịch sử được manifest
publish. Analytics gắn version `heuristic-v2.0.0`, chỉ train trên các kỳ đứng trước kỳ đánh giá và không trộn đài.

GitHub Action `Publish Lottery Dashboard Data` tự chạy lúc 19:47 giờ Việt Nam, tránh thời điểm đầu giờ dễ bị GitHub scheduler trì hoãn. Sau cửa sổ Daily ETL, nó health-check ba lake, đối chiếu ngày quay theo cutoff 18:35, export JSON gọn rồi gọi endpoint ingest có Bearer token; frontend không bao giờ chứa R2 key.

API đọc không cần application token sau khi người gọi đã qua lớp access owner-only của Sites:

```text
GET /api/health/lottery
GET /api/v2/lottery?region=xsmn
GET /api/v2/results?region=xsmn&station=AG&from=2020-01-01&to=2026-07-21&number=63&limit=25
```

Results API trả tối đa 100 dòng/lần, cursor gắn với release/filter và bị từ chối khi stale. Worker cron chạy mỗi 15
phút trong cửa sổ tối: warning từ 20:00, critical từ 20:30, có dedupe/escalation/recovery và ledger R2. Đặt runtime
secret `ALERT_WEBHOOK_URL` (HTTPS) nếu muốn nhận alert; bỏ trống thì health/incident vẫn được ghi nhưng delivery tắt.
Automation headless gọi site riêng tư phải gửi `OAI-Sites-Authorization: Bearer …`; workflow publish dùng secret
`DASHBOARD_SITES_BYPASS_TOKEN` cho cả ingest và post-publish health/API smoke, còn trình duyệt không bao giờ nhận token này.

## Kiểm tra

```bash
npm run build
npm run lint
npm run typecheck
npm test
```
