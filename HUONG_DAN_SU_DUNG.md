# Hướng dẫn sử dụng XSMB/XSMN/XSMT Data Lake

Tài liệu này đi từ lúc cài project, tạo Cloudflare R2, kéo dữ liệu lịch sử, kiểm tra Gold cho đến lúc bật GitHub Actions để tự động lấy kết quả mỗi ngày.

## 1. Hiểu nhanh hệ thống

Project có ba data lake tách biệt:

| Vùng | Local mặc định | Cloudflare R2 | Dữ liệu Gold |
|---|---|---|---|
| XSMB | `output/` | `R2_BUCKET_NAME` | 27 kết quả/ngày |
| XSMN | `output-xsmn/` | `R2_XSMN_BUCKET_NAME` | 18 kết quả/đài/ngày |
| XSMT | `output-xsmt/` | `R2_XSMT_BUCKET_NAME` | 18 kết quả/đài/ngày |

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

## 3. Tạo ba bucket Cloudflare R2

Đăng nhập Wrangler và tạo bucket:

```bash
npx wrangler login
npx wrangler r2 bucket create xsmb-data-lake
npx wrangler r2 bucket create xsmn-data-lake
npx wrangler r2 bucket create xsmt-data-lake
npx wrangler r2 bucket list
```

Nếu bucket đã tồn tại thì không tạo lại. Ba biến bucket không được trỏ vào cùng một bucket.

Trong Cloudflare Dashboard:

1. Vào **Storage & databases → R2 → Overview**.
2. Chọn **Manage R2 API tokens**.
3. Tạo token có quyền **Object Read & Write**.
4. Giới hạn token vào `xsmb-data-lake`, `xsmn-data-lake` và `xsmt-data-lake`.
5. Lưu lại đúng cặp **Access Key ID** và **Secret Access Key**. Secret chỉ hiển thị một lần.

Wrangler dùng phiên đăng nhập Cloudflare để quản lý bucket; ứng dụng Python và GitHub Actions dùng cặp S3 Access Key/Secret Key.

## 4. Cấu hình `.env` trên máy local

Một token dùng chung cho cả ba bucket:

```dotenv
ETL_ENV=production
SOURCE_BASE_URL=https://xoso.com.vn
XSMN_FALLBACK_BASE_URL=https://xskt.com.vn
XSMT_FALLBACK_BASE_URL=https://xskt.com.vn

R2_ACCOUNT_ID=<cloudflare-account-id>
R2_ACCESS_KEY_ID=<r2-access-key-id>
R2_SECRET_ACCESS_KEY=<r2-secret-access-key>
R2_BUCKET_NAME=xsmb-data-lake
R2_XSMN_BUCKET_NAME=xsmn-data-lake
R2_XSMT_BUCKET_NAME=xsmt-data-lake
R2_REGION=auto
```

Để các biến override `R2_XSMN_*` và `R2_XSMT_*` trống nếu hai lake này dùng chung account/token với XSMB. Chỉ điền trọn một bộ override khi đã tạo credential riêng cho region tương ứng.

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

Kiểm tra XSMT:

```bash
uv run lottery-etl run \
  --storage r2 \
  --region xsmt \
  --target-date 2026-07-18
```

Xem manifest bằng Wrangler:

```bash
npx wrangler r2 object get \
  xsmn-data-lake/manifests/latest.json \
  --pipe
```

Manifest phải có `region: "xsmn"`, `target_date` đúng và danh sách Gold objects.
Bucket XSMT tương tự phải có `region: "xsmt"` và không dùng chung manifest với XSMN.

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

Backfill theo range của cả XSMB, XSMN và XSMT chỉ kiểm tra và upsert Bronze/Silver theo từng ngày, sau đó rebuild Silver Loto và Gold đúng một lần ở cuối batch. Nếu tiến trình bị ngắt trước lúc publish, chạy lại đúng lệnh cũ: Bronze/Silver đã ghi được tái sử dụng; manifest `no_draw` mới hơn Gold cũng được nhận ra để lần chạy lại hoàn tất publication.

Nguồn XSMN có dữ liệu từ cuối năm 2005 nhưng format cũ dùng giải đặc biệt 5 chữ số. Với schema hiện tại, nên lấy trọn năm từ `2010-01-01` trở đi.

Không chạy nhiều batch XSMN đồng thời. Với GitHub Actions, dùng workflow `xsmn-backfill.yml`; workflow chia range thành từng năm, chạy tuần tự và dùng cùng concurrency group với daily XSMN.

XSMT dùng cùng schema 18 giải/đài và phải khớp đúng lịch đài theo thứ, thay đổi lịch Chủ nhật từ `2022-01-02`, hoặc một trong sáu bộ đài lịch sử đặc biệt đã được version hóa trong tháng 7–9/2021. Để tránh các trang lịch sử cũ có giải đặc biệt 5 chữ số hoặc dữ liệu không thể đối chiếu, workflow `xsmt-backfill.yml` mặc định bắt đầu từ `2018-01-01`.

```bash
uv run lottery-etl backfill \
  --storage r2 \
  --region xsmt \
  --from 2018-01-01 \
  --to 2018-01-31
```

Không chạy nhiều batch XSMT đồng thời. Workflow XSMT chia theo năm, `max-parallel: 1` và dùng concurrency group `vietnam-lottery-xsmt` chung với daily XSMT.

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
uv run lottery-etl validate --storage r2 --region xsmt
uv run lottery-etl validate --storage r2 --region xsmb
```

Nếu lệnh kết thúc thành công thì toàn bộ critical checks đã pass. `no_draw` là ngày nguồn xác nhận “không mở thưởng”; lỗi mạng hoặc HTML bất thường được ghi `failed` và có thể backfill lại.

### 7.2 Audit toàn bộ lịch sử đã publish

Sau khi backfill xong, chạy audit cả ba lake:

```bash
uv run lottery-etl audit-history --storage r2 --region all
uv run lottery-etl audit-history --storage r2 --region all --json
```

Mặc định lệnh kiểm tra từ ngày đầu tiên được hỗ trợ (XSMB `2005-10-01`, XSMN `2010-01-01`, XSMT `2018-01-01`) tới kỳ gần nhất đã qua giờ cutoff của từng miền. Audit tải đúng các Gold Parquet được manifest trỏ tới, kiểm tra size/SHA-256 trước khi đọc, rồi đối chiếu ngày thiếu hoặc `failed`, grain của fact/loto và đúng bộ đài theo từng thứ của XSMN/XSMT. Có thể giới hạn khoảng ngày bằng `--from YYYY-MM-DD --to YYYY-MM-DD`.

Exit code `0` nghĩa là tất cả miền được chọn đều sạch. Exit code `1` nghĩa là report có finding; JSON phù hợp để lưu artifact hoặc làm CI gate sau khi các khoảng thiếu lịch sử đã được xử lý. Lệnh chỉ đọc, không tự đổi `missing` thành `no_draw` và không ghi lại dữ liệu.

### 7.3 Tải Gold về máy

```bash
uv run lottery-etl download-gold \
  --storage r2 \
  --region xsmn \
  --download-output downloads/xsmn

uv run lottery-etl download-gold \
  --storage r2 \
  --region xsmb \
  --download-output downloads/xsmb

uv run lottery-etl download-gold \
  --storage r2 \
  --region xsmt \
  --download-output downloads/xsmt
```

Các bảng quan trọng:

| File | Dùng để làm gì |
|---|---|
| `fact-draw-result` | Từng kết quả theo ngày, giải và đài |
| `fact-loto-daily` | Tần suất `00`–`99`, rolling 7/30/90 kỳ |
| `fact-special-prize` | Phân tích giải đặc biệt |
| `dim-date` | Ngày, tuần, tháng, trạng thái draw |
| `dim-number` | Thuộc tính số `00`–`99` |
| `dim-station` | Danh sách đài XSMN hoặc XSMT trong lake tương ứng |

CSV phù hợp với Power BI/Tableau; Parquet phù hợp với Pandas và DuckDB. Xem thêm:

- [`docs/data-dictionary.md`](docs/data-dictionary.md)
- [`docs/power-bi.md`](docs/power-bi.md)
- [`docs/tableau.md`](docs/tableau.md)
- [`sql/analysis-examples.sql`](sql/analysis-examples.sql)
- [`sql/xsmn-data-quality-checks.sql`](sql/xsmn-data-quality-checks.sql)
- [`sql/xsmt-data-quality-checks.sql`](sql/xsmt-data-quality-checks.sql)

### 7.4 Việc cần duy trì

1. Giữ `gold/latest` làm nguồn chính cho BI.
2. Kiểm tra `manifests/latest.json` trước khi refresh dashboard.
3. Theo dõi ngày `failed`; chạy lại backfill cho khoảng bị lỗi.
4. Không chạy full backfill mỗi ngày. Sau khi seed lịch sử, GitHub Actions chỉ cần chạy ngày mới nhất.
5. Không công khai Bronze, Silver, quality report hoặc credential. Chỉ publish các Gold objects cần thiết.

### 7.5 Xem dashboard frontend local

Frontend nằm trong `frontend/`. Worker đọc JSON gọn từ một bucket serving riêng; trình duyệt không nhận credential và không đọc trực tiếp Gold Parquet. Ba snapshot đã bundle giúp local dev chạy ngay:

```bash
cd frontend
npm ci
npm run dev
```

Mở URL được in trong terminal, thường là `http://localhost:3000`. Dashboard có đủ XSMB/XSMN/XSMT, chọn đài, heatmap `00`–`99`, ba heuristic và walk-forward backtest. Mỗi model chỉ chạy trên một đài nên không nhìn trước kết quả đài khác cùng ngày.

Muốn làm mới snapshot bundle từ ba lake R2 đang publish, chạy `npm run data:refresh`; lệnh này đọc credential từ `.env` ở root và kiểm tra checksum theo manifest trước khi ghi JSON.

Các model chỉ dùng để mô tả và so sánh lịch sử, không phải dự báo xác suất trúng hoặc khuyến nghị đặt cược. Hướng dẫn chi tiết nằm tại [`frontend/README.md`](frontend/README.md).

## 8. Setup GitHub Actions tự động cào mỗi ngày

Bốn workflow production định kỳ và một workflow repair thủ công đã có sẵn:

- `.github/workflows/daily-etl.yml`: lấy ngày mới nhất cho XSMB, XSMN và XSMT.
- `.github/workflows/xsmn-backfill.yml`: backfill XSMN theo từng năm từ 2010.
- `.github/workflows/xsmt-backfill.yml`: backfill XSMT theo từng năm từ 2018.
- `.github/workflows/dashboard-publish.yml`: 19:47 mỗi ngày, tránh đầu giờ cao tải của GitHub, kiểm tra metadata và toàn bộ lịch sử của cả ba miền theo cutoff 18:35 rồi mới publish JSON serving; các báo cáo JSON tạo được sẽ được giữ 30 ngày.
- `.github/workflows/xsmb-gap-repair.yml`: repair một range XSMB lịch sử, dùng chung concurrency với Daily, không `--force`, rồi audit và lưu JSON artifact 30 ngày.

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

Tạo đủ sáu secret bắt buộc:

| Secret | Giá trị |
|---|---|
| `R2_ACCOUNT_ID` | Cloudflare Account ID |
| `R2_ACCESS_KEY_ID` | R2 S3 Access Key ID |
| `R2_SECRET_ACCESS_KEY` | R2 S3 Secret Access Key |
| `R2_BUCKET_NAME` | Tên bucket XSMB, ví dụ `xsmb-data-lake` |
| `R2_XSMN_BUCKET_NAME` | Tên bucket XSMN, ví dụ `xsmn-data-lake` |
| `R2_XSMT_BUCKET_NAME` | Tên bucket XSMT, ví dụ `xsmt-data-lake` |

Nếu XSMN dùng credential riêng, thêm:

- `R2_XSMN_ACCOUNT_ID`
- `R2_XSMN_ACCESS_KEY_ID`
- `R2_XSMN_SECRET_ACCESS_KEY`
- `R2_XSMN_ENDPOINT_URL` chỉ khi endpoint thực sự khác chuẩn

Nếu XSMT dùng credential riêng, thêm `R2_XSMT_ACCOUNT_ID`, `R2_XSMT_ACCESS_KEY_ID`, `R2_XSMT_SECRET_ACCESS_KEY` và tùy chọn `R2_XSMT_ENDPOINT_URL`.

### 8.3 Tạo GitHub Actions Variables tùy chọn

Vào tab **Variables → New repository variable**:

| Variable | Giá trị gợi ý |
|---|---|
| `SOURCE_BASE_URL` | `https://xoso.com.vn` |
| `XSMN_FALLBACK_BASE_URL` | `https://xskt.com.vn`; chỉ dùng để đối chiếu lịch sử |
| `XSMT_FALLBACK_BASE_URL` | `https://xskt.com.vn`; chỉ dùng để đối chiếu lịch sử |
| `R2_PUBLIC_BASE_URL` | URL public Gold XSMB, nếu có |
| `R2_XSMN_PUBLIC_BASE_URL` | URL public Gold XSMN, nếu có |
| `R2_XSMT_PUBLIC_BASE_URL` | URL public Gold XSMT, nếu có |

Credential luôn phải là Secret, không phải Variable, vì Variable có thể hiện rõ trong log.

Fallback chỉ được gọi cho ba dạng hỏng lịch sử nhận diện chặt chẽ: giải đặc biệt có đúng 5 chữ số, placeholder `...`, hoặc giải 8/7 bị đảo chính xác. Pipeline đối chiếu tập đài và toàn bộ giá trị không bị hỏng trước khi dùng giá trị từ nguồn độc lập; không tự thêm số `0` và không chấp nhận mismatch tùy ý. Hai raw response, URL và hash được giữ trong Bronze để audit.

### 8.4 Chạy thử bằng giao diện GitHub

1. Mở tab **Actions**.
2. Chọn **Daily Vietnam Lottery ETL**.
3. Chọn **Run workflow**.
4. Branch: `main`.
5. `region`: chọn `xsmt` để test riêng hoặc `all` để chạy cả ba lake.
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
  - cron: "17 11 * * *"
```

GitHub dùng UTC, nên `11:17 UTC` tương ứng `18:17 Asia/Ho_Chi_Minh`. Workflow được queue sớm để giảm tác động của scheduler delay, nhưng một gate job sẽ chờ tới đúng 18:35 trước khi tạo ba matrix job độc lập:

- `ETL (xsmb)` ghi vào bucket XSMB.
- `ETL (xsmn)` ghi vào bucket XSMN.
- `ETL (xsmt)` ghi vào bucket XSMT.
- `fail-fast: false` bảo đảm một region lỗi không hủy job region còn lại.
- Concurrency theo region ngăn daily ghi chồng lên historical backfill cùng lake nhưng không chặn hai region còn lại.

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

### 8.7 Chạy historical XSMT backfill

```bash
gh workflow run xsmt-backfill.yml \
  --ref main \
  -f from_year=2018 \
  -f to_year="$(TZ=Asia/Ho_Chi_Minh date +%Y)" \
  -f force=false
```

Nên chạy thử một năm trước, kiểm tra `validate --region xsmt`, rồi mới mở rộng range. Current-year batch tự dừng ở ngày hôm qua; daily ETL chịu trách nhiệm ngày hiện tại sau giờ công bố.

## 9. Xử lý lỗi thường gặp

### `SignatureDoesNotMatch`

Kiểm tra:

1. Access Key ID và Secret Access Key có đúng một cặp không.
2. Có dùng nhầm API token value làm Access Key hoặc Secret không.
3. Account ID có đúng account đã phát hành credential không.
4. Token có Object Read & Write cho đúng bucket không.
5. XSMN/XSMT overrides có bị điền một nửa hay không; nếu dùng credential chung thì để toàn bộ override trống.

### `NoSuchBucket` hoặc `AccessDenied`

- So sánh tên bucket trong Cloudflare với GitHub Secret.
- Chạy `npx wrangler r2 bucket list`.
- Kiểm tra token được scope vào cả ba bucket.

### `PreconditionFailed` hoặc `object already exists` khi resume backfill

R2 trả `412 PreconditionFailed` khi conditional PUT gặp object đã được một run khác tạo trước. Code hiện tại sẽ đọc lại object đó:

- Cùng nội dung: tái sử dụng Bronze và tiếp tục pipeline.
- HTML khác nhưng một lần ghi dở đã có canonical JSON giống hệt kết quả mới: giữ nguyên raw cũ, hoàn tất metadata và ghi dấu `partial_recovery=canonical_result_match` để audit.
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
- [ ] Ba R2 bucket tồn tại và có tên khác nhau.
- [ ] Token có Object Read & Write đúng bucket.
- [ ] Một ngày XSMB chạy thành công trên R2.
- [ ] Một ngày XSMN chạy thành công trên R2.
- [ ] Một ngày XSMT chạy thành công trên R2.
- [ ] Historical backfill hoàn tất hoặc có kế hoạch chạy theo từng chunk.
- [ ] `validate` pass cho từng region.
- [ ] Sáu GitHub Secrets bắt buộc đã được tạo.
- [ ] Workflow đã được push lên `main`.
- [ ] Manual workflow run thành công.
- [ ] `manifests/latest.json` của mỗi bucket đúng region và target date.
- [ ] Dashboard chỉ đọc `gold/latest`.

## Tài liệu tham khảo chính thức

- [Cloudflare R2 CLI](https://developers.cloudflare.com/r2/get-started/cli/)
- [Cloudflare R2 API tokens](https://developers.cloudflare.com/r2/api/tokens/)
- [GitHub: manually run a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [GitHub: disable/enable workflows](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/disabling-and-enabling-a-workflow)
