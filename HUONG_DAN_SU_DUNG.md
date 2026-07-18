# Hướng dẫn sử dụng XSMB/XSMN Data Lake

Tài liệu này đi từ lúc cài project, tạo Cloudflare R2, kéo dữ liệu lịch sử, kiểm tra Gold cho đến lúc bật GitHub Actions để tự động lấy kết quả mỗi ngày.

## 1. Hiểu nhanh hệ thống

Project có hai data lake tách biệt:

| Vùng | Local mặc định | Cloudflare R2 | Dữ liệu Gold |
|---|---|---|---|
| XSMB | `output/` | `R2_BUCKET_NAME` | 27 kết quả/ngày |
| XSMN | `output-xsmn/` | `R2_XSMN_BUCKET_NAME` | 18 kết quả/đài/ngày |

Mỗi lake có cấu trúc riêng:

```text
bronze/      HTML gốc, JSON đã parse và metadata nguồn
silver/      Parquet theo tháng, dùng business key để chống duplicate
gold/latest/ CSV và Parquet dành cho phân tích/BI
quality/     Báo cáo kiểm tra chất lượng
manifests/   Trạng thái run và manifest phiên bản mới nhất
```

Không dùng Bronze trực tiếp cho dashboard. Sau khi có dữ liệu, hãy đọc các bảng trong `gold/latest/`.

## 2. Cài project

Yêu cầu Python 3.14 và `uv`.

```bash
cp .env.example .env
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

Không commit `.env`. Không gửi Access Key ID, Secret Access Key hoặc API token lên GitHub.

## 3. Tạo hai bucket Cloudflare R2

Đăng nhập Wrangler và tạo bucket:

```bash
npx wrangler login
npx wrangler r2 bucket create xsmb-data-lake
npx wrangler r2 bucket create xsmn-data-lake
npx wrangler r2 bucket list
```

Nếu bucket đã tồn tại thì không tạo lại. Hai biến bucket không được trỏ vào cùng một bucket.

Trong Cloudflare Dashboard:

1. Vào **Storage & databases → R2 → Overview**.
2. Chọn **Manage R2 API tokens**.
3. Tạo token có quyền **Object Read & Write**.
4. Giới hạn token vào `xsmb-data-lake` và `xsmn-data-lake`.
5. Lưu lại đúng cặp **Access Key ID** và **Secret Access Key**. Secret chỉ hiển thị một lần.

Wrangler dùng phiên đăng nhập Cloudflare để quản lý bucket; ứng dụng Python và GitHub Actions dùng cặp S3 Access Key/Secret Key.

## 4. Cấu hình `.env` trên máy local

Một token dùng chung cho cả hai bucket:

```dotenv
ETL_ENV=production
SOURCE_BASE_URL=https://xoso.com.vn

R2_ACCOUNT_ID=<cloudflare-account-id>
R2_ACCESS_KEY_ID=<r2-access-key-id>
R2_SECRET_ACCESS_KEY=<r2-secret-access-key>
R2_BUCKET_NAME=xsmb-data-lake
R2_XSMN_BUCKET_NAME=xsmn-data-lake
R2_REGION=auto
```

Để các biến `R2_XSMN_ACCOUNT_ID`, `R2_XSMN_ACCESS_KEY_ID`, `R2_XSMN_SECRET_ACCESS_KEY` và `R2_XSMN_ENDPOINT_URL` trống nếu XSMN dùng chung account/token. Chỉ điền chúng khi đã tạo một bộ credential riêng cho XSMN.

Lưu ý:

- `R2_ACCESS_KEY_ID` không phải API token value.
- `R2_SECRET_ACCESS_KEY` phải là secret đi kèm Access Key ID, không được giống Access Key ID.
- Thường phải để `R2_ENDPOINT_URL` trống; code tự tạo endpoint từ Account ID.
- R2 dùng region `auto`.

## 5. Chạy thử trước khi kéo lịch sử

Kiểm tra XSMN một ngày:

```bash
uv run lottery-etl run \
  --storage r2 \
  --region xsmn \
  --target-date 2026-07-16
```

Kiểm tra XSMB:

```bash
uv run lottery-etl run \
  --storage r2 \
  --region xsmb \
  --target-date 2026-07-16
```

Xem manifest bằng Wrangler:

```bash
npx wrangler r2 object get \
  xsmn-data-lake/manifests/latest.json \
  --pipe
```

Manifest phải có `region: "xsmn"`, `target_date` đúng và danh sách Gold objects.

## 6. Kéo dữ liệu lịch sử

Ví dụ kéo XSMN từ 01/07/2020 đến ngày chạy lệnh:

```bash
TODAY=$(TZ=Asia/Ho_Chi_Minh date +%F)

uv run lottery-etl backfill \
  --storage r2 \
  --region xsmn \
  --from 2020-07-01 \
  --to "$TODAY"
```

Hành vi khi chạy lại cùng khoảng ngày:

- `success` và `no_draw` được bỏ qua.
- `failed` và ngày còn thiếu được thử lại.
- Silver/Gold không tạo duplicate vì ghi theo business key.
- Không dùng `--force` trừ khi muốn thay thế dữ liệu đã thành công.
- Không chạy hai tiến trình backfill cùng một region đồng thời.

XSMN backfill chỉ kiểm tra và upsert Bronze/Silver theo từng ngày, sau đó rebuild Silver Loto và Gold đúng một lần ở cuối batch. Nếu tiến trình bị ngắt trước lúc publish, chạy lại đúng lệnh cũ: Bronze/Silver đã ghi được tái sử dụng và Gold chỉ đổi khi batch hoàn tất kiểm tra chất lượng.

Nguồn XSMN có dữ liệu từ cuối năm 2005 nhưng format cũ dùng giải đặc biệt 5 chữ số. Với schema hiện tại, nên lấy trọn năm từ `2010-01-01` trở đi.

Không chạy nhiều batch XSMN đồng thời. Với GitHub Actions, dùng workflow `xsmn-backfill.yml`; workflow chia range thành từng năm, chạy tuần tự và dùng cùng concurrency group với daily XSMN.

Nếu đã có file XSMB cũ `data/xsmb.json`, migrate bằng:

```bash
uv run lottery-etl migrate-legacy \
  --storage r2 \
  --input data/xsmb.json
```

## 7. Sau khi có dữ liệu, cần làm gì?

### 7.1 Kiểm tra chất lượng

```bash
uv run lottery-etl validate --storage r2 --region xsmn
uv run lottery-etl validate --storage r2 --region xsmb
```

Nếu lệnh kết thúc thành công thì toàn bộ critical checks đã pass. `no_draw` là ngày nguồn xác nhận “không mở thưởng”; lỗi mạng hoặc HTML bất thường được ghi `failed` và có thể backfill lại.

### 7.2 Tải Gold về máy

```bash
uv run lottery-etl download-gold \
  --storage r2 \
  --region xsmn \
  --download-output downloads/xsmn

uv run lottery-etl download-gold \
  --storage r2 \
  --region xsmb \
  --download-output downloads/xsmb
```

Các bảng quan trọng:

| File | Dùng để làm gì |
|---|---|
| `fact-draw-result` | Từng kết quả theo ngày, giải và đài |
| `fact-loto-daily` | Tần suất `00`–`99`, rolling 7/30/90 kỳ |
| `fact-special-prize` | Phân tích giải đặc biệt |
| `dim-date` | Ngày, tuần, tháng, trạng thái draw |
| `dim-number` | Thuộc tính số `00`–`99` |
| `dim-station` | Danh sách đài XSMN |

CSV phù hợp với Power BI/Tableau; Parquet phù hợp với Pandas và DuckDB. Xem thêm:

- [`docs/data-dictionary.md`](docs/data-dictionary.md)
- [`docs/power-bi.md`](docs/power-bi.md)
- [`docs/tableau.md`](docs/tableau.md)
- [`sql/analysis-examples.sql`](sql/analysis-examples.sql)
- [`sql/xsmn-data-quality-checks.sql`](sql/xsmn-data-quality-checks.sql)

### 7.3 Việc cần duy trì

1. Giữ `gold/latest` làm nguồn chính cho BI.
2. Kiểm tra `manifests/latest.json` trước khi refresh dashboard.
3. Theo dõi ngày `failed`; chạy lại backfill cho khoảng bị lỗi.
4. Không chạy full backfill mỗi ngày. Sau khi seed lịch sử, GitHub Actions chỉ cần chạy ngày mới nhất.
5. Không công khai Bronze, Silver, quality report hoặc credential. Chỉ publish các Gold objects cần thiết.

### 7.4 Xem dashboard frontend local

Frontend demo nằm trong `frontend/`. Bản này không kết nối R2 và không đọc credential; nó export dữ liệu XSMB lịch sử đang có trong repo thành payload tĩnh để mày xem model ngay:

```bash
cd frontend
npm ci
npm run data:refresh
npm run dev
```

Mở URL được in trong terminal, thường là `http://localhost:3000`. Dashboard hiện có heatmap `00`–`99`, model tần suất, model khoảng vắng, model cân bằng 60/40, backtest 90 kỳ và tín hiệu đà ngắn hạn. XSMN được đánh dấu `Chờ Gold` cho tới khi có đủ dữ liệu lịch sử; frontend không dựng dữ liệu XSMN giả.

Các model chỉ dùng để mô tả và so sánh lịch sử, không phải dự báo xác suất trúng hoặc khuyến nghị đặt cược. Hướng dẫn chi tiết nằm tại [`frontend/README.md`](frontend/README.md).

## 8. Setup GitHub Actions tự động cào mỗi ngày

Hai workflow production đã có sẵn:

- `.github/workflows/daily-etl.yml`: lấy ngày mới nhất cho XSMB và XSMN.
- `.github/workflows/xsmn-backfill.yml`: backfill XSMN theo từng năm từ 2010.

### 8.1 Đưa workflow lên default branch

Workflow manual và schedule phải có trong default branch của repository. Trước khi push:

```bash
git status
git diff --check
```

Đảm bảo `.env`, `.idea` và credential không được stage. Commit source, tests, tài liệu và hai file workflow rồi push lên `main`.

### 8.2 Tạo GitHub Actions Secrets

Trên GitHub mở:

**Repository → Settings → Secrets and variables → Actions → Secrets → New repository secret**

Tạo đủ năm secret bắt buộc:

| Secret | Giá trị |
|---|---|
| `R2_ACCOUNT_ID` | Cloudflare Account ID |
| `R2_ACCESS_KEY_ID` | R2 S3 Access Key ID |
| `R2_SECRET_ACCESS_KEY` | R2 S3 Secret Access Key |
| `R2_BUCKET_NAME` | Tên bucket XSMB, ví dụ `xsmb-data-lake` |
| `R2_XSMN_BUCKET_NAME` | Tên bucket XSMN, ví dụ `xsmn-data-lake` |

Nếu XSMN dùng credential riêng, thêm:

- `R2_XSMN_ACCOUNT_ID`
- `R2_XSMN_ACCESS_KEY_ID`
- `R2_XSMN_SECRET_ACCESS_KEY`
- `R2_XSMN_ENDPOINT_URL` chỉ khi endpoint thực sự khác chuẩn

### 8.3 Tạo GitHub Actions Variables tùy chọn

Vào tab **Variables → New repository variable**:

| Variable | Giá trị gợi ý |
|---|---|
| `SOURCE_BASE_URL` | `https://xoso.com.vn` |
| `R2_PUBLIC_BASE_URL` | URL public Gold XSMB, nếu có |
| `R2_XSMN_PUBLIC_BASE_URL` | URL public Gold XSMN, nếu có |

Credential luôn phải là Secret, không phải Variable, vì Variable có thể hiện rõ trong log.

### 8.4 Chạy thử bằng giao diện GitHub

1. Mở tab **Actions**.
2. Chọn **Daily Vietnam Lottery ETL**.
3. Chọn **Run workflow**.
4. Branch: `main`.
5. `region`: chọn `xsmn` để test riêng hoặc `all` để chạy cả hai lake.
6. `target_date`: điền một ngày đã có kết quả; để trống thì workflow tự chọn ngày mới nhất đã hoàn tất.
7. `force`: để `false`.
8. Chọn **Run workflow** và xem job summary.

Có thể chạy bằng GitHub CLI:

```bash
gh workflow run daily-etl.yml \
  --ref main \
  -f region=xsmn \
  -f target_date=2026-07-16 \
  -f force=false

gh run watch
```

### 8.5 Lịch tự động hiện tại

Workflow chạy:

```yaml
schedule:
  - cron: "35 11 * * *"
```

GitHub dùng UTC, nên `11:35 UTC` tương ứng `18:35 Asia/Ho_Chi_Minh`. Mỗi lịch chạy tạo hai matrix job độc lập:

- `ETL (xsmb)` ghi vào bucket XSMB.
- `ETL (xsmn)` ghi vào bucket XSMN.
- `fail-fast: false` bảo đảm một region lỗi không hủy job region còn lại.
- Concurrency theo region ngăn daily XSMN ghi chồng lên historical backfill nhưng không chặn daily XSMB.

Scheduled workflow trong public repository có thể bị GitHub tự tắt sau 60 ngày repository không hoạt động. Fork public cũng thường tắt schedule mặc định; vào tab Actions để bật lại khi cần.

### 8.6 Chạy historical XSMN backfill

Sau khi daily workflow chạy thử thành công, khởi động toàn bộ lịch sử sáu chữ số:

```bash
gh workflow run xsmn-backfill.yml \
  --ref main \
  -f from_year=2010 \
  -f to_year="$(TZ=Asia/Ho_Chi_Minh date +%Y)" \
  -f force=false
```

Mỗi năm là một matrix job, `max-parallel: 1`. Các ngày `success/no_draw` được skip; ngày `failed` được thử lại. Mỗi job chỉ publish Gold một lần sau khi toàn bộ ngày thành công trong năm đã được ingest. Nếu còn bất kỳ ngày `failed`, job đó chuyển đỏ và liệt kê ngày lỗi trong summary; các năm khác vẫn tiếp tục.

## 9. Xử lý lỗi thường gặp

### `SignatureDoesNotMatch`

Kiểm tra:

1. Access Key ID và Secret Access Key có đúng một cặp không.
2. Có dùng nhầm API token value làm Access Key hoặc Secret không.
3. Account ID có đúng account đã phát hành credential không.
4. Token có Object Read & Write cho đúng bucket không.
5. XSMN overrides có bị điền một nửa hay không; nếu dùng credential chung thì để toàn bộ override trống.

### `NoSuchBucket` hoặc `AccessDenied`

- So sánh tên bucket trong Cloudflare với GitHub Secret.
- Chạy `npx wrangler r2 bucket list`.
- Kiểm tra token được scope vào cả hai bucket.

### `PreconditionFailed` hoặc `object already exists` khi resume backfill

R2 trả `412 PreconditionFailed` khi conditional PUT gặp object đã được một run khác tạo trước. Code hiện tại sẽ đọc lại object đó:

- Cùng nội dung: tái sử dụng Bronze và tiếp tục pipeline.
- Khác nội dung: đánh dấu ngày đó `failed`, giữ nguyên Bronze cũ và tiếp tục ngày kế tiếp.

Chạy lại đúng lệnh backfill cũ, không thêm `--force`. Các ngày `success/no_draw` sẽ được skip, còn ngày `failed` hoặc chưa đủ dữ liệu sẽ được thử lại. Chỉ dùng `--force` sau khi đã kiểm tra source thực sự sửa kết quả và mày chủ động muốn thay Bronze.

### Workflow không có nút **Run workflow**

- Kiểm tra `daily-etl.yml` đã nằm trên default branch chưa.
- Kiểm tra repository có bật Actions không.
- Workflow phải có `workflow_dispatch`; file hiện tại đã có.

### Một ngày bị `failed`

Không dùng `--force` ngay. Chạy lại riêng region/ngày đó:

```bash
uv run lottery-etl run \
  --storage r2 \
  --region xsmn \
  --target-date YYYY-MM-DD
```

Hoặc chạy backfill một khoảng ngắn. Ngày thành công sẽ bị skip, ngày lỗi sẽ được thử lại và không tạo duplicate.

## 10. Checklist đưa vào vận hành

- [ ] Ruff và pytest pass.
- [ ] Hai R2 bucket tồn tại và có tên khác nhau.
- [ ] Token có Object Read & Write đúng bucket.
- [ ] Một ngày XSMB chạy thành công trên R2.
- [ ] Một ngày XSMN chạy thành công trên R2.
- [ ] Historical backfill hoàn tất hoặc có kế hoạch chạy theo từng chunk.
- [ ] `validate` pass cho từng region.
- [ ] Năm GitHub Secrets bắt buộc đã được tạo.
- [ ] Workflow đã được push lên `main`.
- [ ] Manual workflow run thành công.
- [ ] `manifests/latest.json` của mỗi bucket đúng region và target date.
- [ ] Dashboard chỉ đọc `gold/latest`.

## Tài liệu tham khảo chính thức

- [Cloudflare R2 CLI](https://developers.cloudflare.com/r2/get-started/cli/)
- [Cloudflare R2 API tokens](https://developers.cloudflare.com/r2/api/tokens/)
- [GitHub: manually run a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [GitHub: disable/enable workflows](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/disabling-and-enabling-a-workflow)
