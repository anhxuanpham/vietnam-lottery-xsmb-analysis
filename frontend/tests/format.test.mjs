import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

for (const timezone of ["America/New_York", "UTC", "Asia/Ho_Chi_Minh", "Pacific/Kiritimati"]) {
  test(`draw dates retain their Vietnam calendar day in ${timezone}`, () => {
    const result = spawnSync(process.execPath, ["--input-type=module", "-e", `
      import assert from "node:assert/strict";
      import { formatDate } from ${JSON.stringify(new URL("../app/components/format.ts", import.meta.url).href)};
      for (const [input, expected] of [
        ["2026-07-16", "16/07/2026"],
        ["2026-01-01", "01/01/2026"],
        ["2024-03-01", "01/03/2024"],
        ["2024-02-29", "29/02/2024"],
      ]) assert.equal(formatDate(input), expected);
      for (const input of ["", "not-a-date"]) {
        assert.throws(() => formatDate(input), RangeError);
      }
    `], { env: { ...process.env, TZ: timezone }, encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr || result.stdout);
  });
}
