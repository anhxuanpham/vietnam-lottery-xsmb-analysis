/** Bindings injected by Sites rather than represented in the generated Wrangler config. */
interface Env {
  ASSETS: Fetcher;
  DASHBOARD_INGEST_TOKEN?: string;
  ALERT_WEBHOOK_URL?: string;
}
