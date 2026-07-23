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
- Benchmark Integrity v1 walk-forward 90 kỳ, chạy độc lập trên từng đài và chỉ train bằng dữ liệu đứng trước kỳ đánh giá.
- Tín hiệu nóng/lạnh và đà 7 kỳ so với 30 kỳ.
- Trạng thái nguồn R2/demo, dataset version, health thật của ba miền và trạng thái watchdog đã được lược bỏ định danh incident.
- Explorer lịch sử theo miền, đài, khoảng ngày và số `00`–`99`; deep link hợp lệ tự tra một lần sau khi metadata sẵn sàng.
  Bộ lọc ngày/số chỉ được ghi vào URL khi bấm **Tra kết quả**; sửa nháp sẽ hủy cursor cũ. **Tải thêm kết quả** nối
  tiếp, loại trùng và giữ thứ tự ngày giảm dần thay vì thay thế trang trước.

Benchmark gắn dataset/đài/model/window, training range, evaluation range và fingerprint xác định. Mỗi model hiển thị
coverage, hit rate, bootstrap 95% CI xác định và lift so với baseline `topK / 100`; báo cáo JSON tải xuống giữ cùng
lineage/fingerprint. Ba model nhân bốn cửa sổ là 12 cấu hình exploratory, không phải thử nghiệm xác nhận. Các model chỉ
là heuristic mô tả, không phải dự báo xác suất trúng hoặc khuyến nghị đặt cược.

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
publish. Analytics gắn version `heuristic-v2.1.0`, chỉ train trên các kỳ đứng trước kỳ đánh giá, không trộn đài, và
dùng fingerprint làm seed cho bootstrap nên cùng input luôn tạo cùng benchmark report.

GitHub Action `Publish Lottery Dashboard Data` tự chạy ngay sau khi scheduled `Daily Vietnam Lottery ETL` thành công
và vẫn cho phép dispatch thủ công. Nó health-check ba lake, đối chiếu ngày quay theo cutoff 18:35, audit toàn bộ lịch
sử rồi export JSON gọn. Toàn bộ đường dẫn/release shard được kiểm tra trước; shard upload song song tối đa 8 luồng và
metadata v2 chỉ được publish cuối cùng khi không có shard lỗi. `Backup Published Lottery Releases` tiếp tục tự chạy
sau khi Dashboard publish thành công. Frontend không bao giờ chứa R2 key.

API đọc không cần application token sau khi người gọi đã qua lớp access owner-only của Sites:

```text
GET /api/health/lottery
GET /api/ops/lottery
GET /api/v2/lottery?region=xsmn
GET /api/v2/results?region=xsmn&station=AG&from=2020-01-01&to=2026-07-21&number=63&limit=25
```

Results API trả tối đa 100 dòng/lần, cursor gắn với release/filter và bị từ chối khi stale. Worker cron chạy mỗi 15
phút trong cửa sổ tối: warning từ 20:00, critical từ 20:30, có dedupe/escalation/recovery và ledger R2. Đặt runtime
secret `ALERT_WEBHOOK_URL` (HTTPS) nếu muốn nhận alert; bỏ trống thì health/incident vẫn được ghi nhưng delivery tắt.
`/api/ops/lottery` chỉ trả trạng thái, target, thời điểm quan sát, severity và cờ incident; không trả incident ID,
webhook URL hoặc secret. Nếu chưa có state, endpoint vẫn trả `200` với `available=false`.
Automation headless gọi site riêng tư phải gửi `OAI-Sites-Authorization: Bearer …`; workflow publish dùng secret
`DASHBOARD_SITES_BYPASS_TOKEN` cho cả ingest và post-publish health/API smoke, còn trình duyệt không bao giờ nhận token này.

## Kiểm tra

```bash
npm run build
npm run lint
npm run typecheck
npm test
```
