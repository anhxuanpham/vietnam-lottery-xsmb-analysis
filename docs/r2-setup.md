# Cloudflare R2 setup for three data lakes

## 1. Authenticate Wrangler and create all buckets

Bucket creation is an administrative action; the ETL itself uses the S3-compatible API.

```bash
npx wrangler login
npx wrangler r2 bucket create xsmb-data-lake
npx wrangler r2 bucket create xsmn-data-lake
npx wrangler r2 bucket create xsmt-data-lake
npx wrangler r2 bucket create vietnam-lottery-backup
npx wrangler r2 bucket list
```

Bucket names are examples and may be changed. Keep all three regional buckets independent. The backup bucket should
preferably use a separate account/token failure domain.

## 2. Create least-privilege S3 credentials

In Cloudflare R2, create an API token with **Object Read & Write** permission and scope it to the three regional
buckets. Record the **Access Key ID** and **Secret Access Key** when Cloudflare displays them; the secret is shown
once.

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

For production release backup, create a second **Object Read & Write** token scoped only to the backup bucket and
configure `R2_BACKUP_BUCKET_NAME`, `R2_BACKUP_ACCOUNT_ID`, `R2_BACKUP_ACCESS_KEY_ID`, and
`R2_BACKUP_SECRET_ACCESS_KEY`. `R2_BACKUP_ENDPOINT_URL` is optional because it can be derived from the account ID.
The application can fall back to the primary connection when every override is blank, but then the primary token
must explicitly include the backup bucket as a fourth bucket. The scheduled DR workflow deliberately requires the
dedicated credential set so a primary-token failure does not remove the backup failure domain.

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

Confirm all three buckets independently contain Bronze, monthly Silver, immutable Gold Parquet under
`gold/releases/run-id=<run>/`, quality, run manifests, an immutable snapshot manifest, `control/latest.json`, and
`manifests/latest.json`. XSMN/XSMT releases must include `dim-station.parquet`.

Fixture runs use `test_fixture` lineage. Do not use a fixture to seed production history.

## 4. Configure GitHub Actions

Under repository Settings → Secrets and variables → Actions, create:

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `R2_XSMN_BUCKET_NAME`
- `R2_XSMT_BUCKET_NAME`
- `R2_BACKUP_BUCKET_NAME`
- `R2_BACKUP_ACCOUNT_ID`
- `R2_BACKUP_ACCESS_KEY_ID`
- `R2_BACKUP_SECRET_ACCESS_KEY`
- `R2_BACKUP_ENDPOINT_URL` only when an explicit endpoint is required

Only add optional `R2_XSMN_*` or `R2_XSMT_*` secrets when using a separate regional account/token. Optional repository variables include the public base URLs, fallback source URLs, and `SOURCE_BASE_URL`.

`XSMN_FALLBACK_BASE_URL` is also optional and defaults to `https://xskt.com.vn`. It is not a credential. Set it only when the independent historical reconciliation source must be overridden.

CI never receives production secrets. The scheduled/manual daily workflow receives them and has read-only repository permissions.

## 5. Publish curated serving data only

Keep Bronze, Silver, quality reports, ControlState, run manifests, and snapshot manifests private. Daily Gold paths
contain a run ID and are immutable. The weekly job converts the manifest-selected Parquet release into immutable CSV
and then publishes `exports/csv/latest.json`. A curated read-only router may expose only `manifests/latest.json` or
`exports/csv/latest.json` plus the immutable objects referenced by that pointer; otherwise consumers resolve the
pointer through authenticated access. Example public CSV routes:

```text
https://data.example.com/xsmb/exports/csv/run-id=<run>/fact-loto-daily.csv
https://data.example.com/xsmn/exports/csv/run-id=<run>/fact-loto-daily.csv
https://data.example.com/xsmt/exports/csv/run-id=<run>/fact-loto-daily.csv
```

Ensure every route targets the correct bucket. Do not use expiring presigned URLs for long-term BI scheduled refresh.

## 6. Activate the schedule

Run `Daily Vietnam Lottery ETL` manually with `region=all` and a known date. Verify all three Action results, latest
manifests, and checksums. Run `Export Lottery CSV Snapshots` once before enabling BI refresh. Run `Backup Published
Lottery Releases` and retain its backup/restore/status artifact. The restore drill is deliberately consumer-only:
it restores Gold/manifests/control into an isolated local lake, writes `recovery/consumer-only.json`, and blocks ETL
because Bronze/Silver are not part of this release backup.

For the recurring path, only Daily ETL owns the lottery-data cron. A successful scheduled Daily run triggers Dashboard
publication; a successful Dashboard run then triggers the backup/restore drill. Confirm both downstream workflow files
are present on the default branch. Dashboard validates the complete v2 shard set before uploading with concurrency `8`
and writes each region's metadata pointer only after every shard succeeds.

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
