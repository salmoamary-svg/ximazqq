#!/usr/bin/env node
// Runs every sample in samples.json through the deployed ingest function in dry mode
// and prints one row per SMS. Nothing is written to the database.
//
//   INGEST_URL=https://<ref>.supabase.co/functions/v1/ingest INGEST_KEY=... node v2/test/run.mjs
//
// Add `--live` to actually ingest (writes rows) — used once for the transfer-matching check.

import { readFileSync } from "node:fs";

const url = process.env.INGEST_URL, key = process.env.INGEST_KEY;
if (!url || !key) { console.error("set INGEST_URL and INGEST_KEY"); process.exit(1); }
const live = process.argv.includes("--live");
const samples = JSON.parse(readFileSync(new URL("./samples.json", import.meta.url), "utf8"));

let fails = 0;
for (const s of samples) {
  const r = await fetch(url + (live ? "" : "?dry=1"), {
    method: "POST",
    headers: { "content-type": "application/json", "x-ingest-key": key },
    body: JSON.stringify({ text: s.text, sender: s.sender, received_at: s.received_at }),
  });
  const out = await r.json();
  const bad = Object.entries(s.expect ?? {}).filter(([k, v]) => {
    const got = k === "needs_tap" ? out.needs_tap ?? !out.confirmed : k === "account" ? out.account ?? out.account_id : out[k];
    return got !== v;
  });
  if (bad.length) fails++;
  console.log(`${bad.length ? "✗" : "✓"} ${s.name.padEnd(30)} ${String(out.kind).padEnd(13)} ${String(out.account ?? out.account_id).padEnd(4)} ${String(out.amount).padStart(9)}${out.fee ? ` +${out.fee}` : ""}  ${out.counterparty ?? "-"}  → ${out.budget_id ?? out.commitment_id ?? "-"}  conf ${out.confidence}${(out.needs_tap ?? !out.confirmed) ? "  [TAP]" : ""}`);
  console.log(`    ${out.occurred_at ?? ""}  ${out.reason ?? out.error ?? ""}`);
  for (const [k, v] of bad) console.log(`    expected ${k}=${JSON.stringify(v)}, got ${JSON.stringify(out[k])}`);
}
console.log(fails ? `\n${fails} sample(s) off` : "\nall samples as expected");
process.exit(fails ? 1 : 0);
