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

Các model chỉ là heuristic mô tả, không phải dự báo xác suất trúng hoặc khuyến nghị đặt cược.

## Luồng dữ liệu

Worker đọc `regions/xsmb.json`, `regions/xsmn.json`, `regions/xsmt.json` từ binding R2 `LOTTERY_DATA`. Nếu object chưa tồn tại, Worker fallback về:

```text
public/data/xsmb-demo.json
public/data/xsmn-demo.json
public/data/xsmt-demo.json
```

`npm run data:refresh` đọc `.env` ở root, kiểm tra các object Gold được `manifests/latest.json` tham chiếu và cập nhật cả ba snapshot từ R2. Payload giữ 455 kỳ gần nhất cho mỗi đài: đủ cửa sổ huấn luyện 365 kỳ cộng 90 kỳ walk-forward.

GitHub Action `Publish Lottery Dashboard Data` tự chạy lúc 20:00 giờ Việt Nam sau cửa sổ Daily ETL. Nó health-check ba lake, bắt buộc đúng ngày hiện tại, export JSON gọn rồi gọi endpoint ingest có Bearer token; frontend không bao giờ chứa R2 key.

## Kiểm tra

```bash
npm run build
npm run lint
npm run typecheck
npm test
```
