# Cloudflare R2 setup for three data lakes

## 1. Authenticate Wrangler and create all buckets

Bucket creation is an administrative action; the ETL itself uses the S3-compatible API.

```bash
npx wrangler login
npx wrangler r2 bucket create xsmb-data-lake
npx wrangler r2 bucket create xsmn-data-lake
npx wrangler r2 bucket create xsmt-data-lake
npx wrangler r2 bucket list
```

Bucket names are examples and may be changed. Keep all three buckets independent.

## 2. Create least-privilege S3 credentials

In Cloudflare R2, create an API token with **Object Read & Write** permission and scope it to the three selected buckets. Record the **Access Key ID** and **Secret Access Key** when Cloudflare displays them; the secret is shown once.

One token can cover all three buckets:

```dotenv
ETL_ENV=production
R2_ACCOUNT_ID=<account-id>
R2_ACCESS_KEY_ID=<access-key-id>
R2_SECRET_ACCESS_KEY=<secret-access-key>
R2_BUCKET_NAME=xsmb-data-lake
R2_XSMN_BUCKET_NAME=xsmn-data-lake
R2_XSMT_BUCKET_NAME=xsmt-data-lake
```

For stricter credential isolation, create a second token scoped only to XSMN and add:

```dotenv
R2_XSMN_ACCOUNT_ID=<account-id-if-different>
R2_XSMN_ACCESS_KEY_ID=<xsmn-access-key-id>
R2_XSMN_SECRET_ACCESS_KEY=<xsmn-secret-access-key>
R2_XSMN_ENDPOINT_URL=<only-if-an-explicit-endpoint-is-required>
```

Blank XSMN/XSMT overrides fall back to the shared account and credentials. The standard endpoint is derived as `https://<account-id>.r2.cloudflarestorage.com`, and the S3 region is `auto`.

Never put credentials in source, documentation, screenshots, fixtures, shell history, or command output. `.env` is ignored by Git.

## 3. Verify each bucket independently

Use a disposable bucket or local fixture date first. Each command must publish only to the selected bucket:

```bash
uv run lottery-etl run \
  --storage r2 \
  --region xsmb \
  --target-date 2026-07-16 \
  --fixture tests/fixtures/valid-result-page.html

uv run lottery-etl run \
  --storage r2 \
  --region xsmn \
  --target-date 2026-07-16 \
  --fixture tests/fixtures/valid-xsmn-result-page.html

uv run lottery-etl run \
  --storage r2 \
  --region xsmt \
  --target-date 2026-07-18 \
  --fixture tests/fixtures/valid-xsmt-result-page.html
```

Confirm all three buckets independently contain Bronze, monthly Silver, Gold CSV/Parquet, quality, run manifests, a snapshot manifest, and `manifests/latest.json`. The XSMN/XSMT buckets should also contain `gold/latest/dim-station.*`.

Fixture runs use `test_fixture` lineage. Do not use a fixture to seed production history.

## 4. Configure GitHub Actions

Under repository Settings → Secrets and variables → Actions, create:

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `R2_XSMN_BUCKET_NAME`
- `R2_XSMT_BUCKET_NAME`

Only add optional `R2_XSMN_*` or `R2_XSMT_*` secrets when using a separate regional account/token. Optional repository variables include the public base URLs, fallback source URLs, and `SOURCE_BASE_URL`.

`XSMN_FALLBACK_BASE_URL` is also optional and defaults to `https://xskt.com.vn`. It is not a credential. Set it only when the independent historical reconciliation source must be overridden.

CI never receives production secrets. The scheduled/manual daily workflow receives them and has read-only repository permissions.

## 5. Publish curated Gold only

Keep Bronze, Silver, quality reports, and manifests private. Route only the intended Gold objects through custom domains, for example:

```text
https://data.example.com/xsmb/gold/latest/fact-loto-daily.csv
https://data.example.com/xsmn/gold/latest/fact-loto-daily.csv
https://data.example.com/xsmt/gold/latest/fact-loto-daily.csv
```

Ensure every route targets the correct bucket. Do not use expiring presigned URLs for long-term BI scheduled refresh.

## 6. Activate the schedule

Run `Daily Vietnam Lottery ETL` manually with `region=all` and a known date. Verify all three Action results, latest manifests, listed checksums, and public CSV routes. The cron queues at 18:17 Asia/Ho_Chi_Minh, and a gate job holds scheduled extraction until the safe 18:35 draw cutoff.

## Troubleshooting S3 signatures

`SignatureDoesNotMatch` occurs before ETL logic can list the bucket. Check all of the following:

1. `R2_ACCESS_KEY_ID` contains the Access Key ID, not the API token value.
2. `R2_SECRET_ACCESS_KEY` contains the corresponding Secret Access Key and is not identical to the access key.
3. `R2_ACCOUNT_ID` belongs to the same account that issued the credentials.
4. The endpoint is the R2 S3 endpoint for that account; leave `R2_ENDPOINT_URL` blank unless an explicit compatible endpoint is required.
5. The token has Object Read & Write access to the selected bucket.
6. XSMN overrides, when set, form one matching credential set; otherwise leave all overrides blank to use the shared set.

References:

- [Cloudflare: create R2 buckets](https://developers.cloudflare.com/r2/buckets/create-buckets/)
- [Cloudflare: R2 authentication](https://developers.cloudflare.com/r2/api/tokens/)
- [Cloudflare: Wrangler R2 commands](https://developers.cloudflare.com/r2/reference/wrangler-commands/)
- [Cloudflare: R2 S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/)
- [Cloudflare: public buckets and custom domains](https://developers.cloudflare.com/r2/buckets/public-buckets/)
