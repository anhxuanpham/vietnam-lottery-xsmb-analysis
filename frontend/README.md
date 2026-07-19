# Lôtô Lab dashboard

Frontend demo cho data lake XSMB/XSMN/XSMT. Bản hiện tại chạy hoàn toàn local, không đọc `.env`, không kết nối R2 và không cần credential.

## Chạy local

Từ thư mục `frontend/`:

```bash
npm ci
npm run data:refresh
npm run dev
```

Mở URL do terminal in ra, thường là `http://localhost:3000` hoặc cổng kế tiếp nếu cổng đó đang bận.

## Dashboard đang hiển thị gì?

- Kết quả XSMB mới nhất và 27 loto trong kỳ.
- Heatmap tần suất `00`–`99` theo 30/90/180/365 kỳ.
- Model tần suất, khoảng vắng và model cân bằng 60/40.
- Backtest 90 kỳ với coverage top 10 và baseline 10%.
- Tín hiệu nóng/lạnh và đà 7 kỳ so với 30 kỳ.
- Trạng thái nguồn dữ liệu; XSMN và XSMT được đánh dấu chờ Gold thay vì dùng dữ liệu giả.

Các model chỉ là heuristic mô tả, không phải dự báo xác suất trúng hoặc khuyến nghị đặt cược.

## Dữ liệu demo

`npm run data:refresh` chạy `scripts/export_frontend_demo.py` ở root repository và tạo:

```text
public/data/xsmb-demo.json
```

Payload gồm thống kê toàn bộ 7.493 kỳ trong file lịch sử và 730 kỳ gần nhất để chạy model trong browser. Khi frontend chuyển sang R2 Gold, giữ cùng nguyên tắc: chỉ đọc `gold/latest`, kiểm tra `manifests/latest.json` trước khi refresh và không đưa R2 secret vào frontend.

## Kiểm tra

```bash
npm run build
npm run lint
npm test
```
