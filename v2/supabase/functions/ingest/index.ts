// ingest — receives one bank SMS from the iPhone Shortcut, stores it verbatim,
// classifies it with Claude, and writes a transaction row.
//
// POST /functions/v1/ingest            body: {"text": "...", "sender": "BSF"}   (or text/plain body)
// POST /functions/v1/ingest?dry=1      classify only, write nothing (used by the test harness)
// Header: x-ingest-key: <INGEST_KEY>
//
// Secrets (Supabase → Edge Functions → Secrets): ANTHROPIC_API_KEY, INGEST_KEY.
// SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are injected by the platform.

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import Anthropic from "npm:@anthropic-ai/sdk";
import { createClient } from "npm:@supabase/supabase-js@2";

const MODEL = "claude-opus-5";
const TZ = "Asia/Riyadh";
const TRANSFER_WINDOW_MIN = 30;
const CONFIRM_AT = 0.8;

type Kind = "salary" | "income" | "spend" | "commitment" | "transfer_out" | "transfer_in" | "ignore";

interface Classified {
  kind: Kind;
  account: string | null;
  amount: number;
  fee: number;
  counterparty: string | null;
  counterparty_is_owner: boolean;
  budget_id: string | null;
  commitment_id: string | null;
  occurred_at: string | null;
  confidence: number;
  reason: string;
}

const SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["kind","account","amount","fee","counterparty","counterparty_is_owner","budget_id","commitment_id","occurred_at","confidence","reason"],
  properties: {
    kind: { type: "string", enum: ["salary","income","spend","commitment","transfer_out","transfer_in","ignore"] },
    account: { type: ["string","null"] },
    amount: { type: "number" },
    fee: { type: "number" },
    counterparty: { type: ["string","null"] },
    counterparty_is_owner: { type: "boolean" },
    budget_id: { type: ["string","null"] },
    commitment_id: { type: ["string","null"] },
    occurred_at: { type: ["string","null"], description: "ISO-8601 with offset, or null if the SMS has no usable date" },
    confidence: { type: "number", description: "0 to 1" },
    reason: { type: "string", description: "one short sentence" },
  },
};

const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const anthropic = new Anthropic({ apiKey: Deno.env.get("ANTHROPIC_API_KEY") });

async function sha256(s: string) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
const normalize = (s: string) => s.replace(/\s+/g, " ").trim();
const normPayee = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

async function loadContext() {
  const [accounts, budgets, commitments, memory, settings] = await Promise.all([
    sb.from("accounts").select("*").order("sort"),
    sb.from("budgets").select("*").order("sort"),
    sb.from("commitments").select("*").order("sort"),
    sb.from("payee_memory").select("*").order("taught_at", { ascending: false }).limit(200),
    sb.from("settings").select("*"),
  ]);
  const st: Record<string, unknown> = {};
  for (const r of settings.data ?? []) st[r.key] = r.value;
  return { accounts: accounts.data ?? [], budgets: budgets.data ?? [], commitments: commitments.data ?? [], memory: memory.data ?? [], settings: st };
}

function systemPrompt(ctx: Awaited<ReturnType<typeof loadContext>>, receivedAt: string, sender: string | null) {
  const owner = (ctx.settings.owner_names as string[] | undefined)?.join(", ") ?? "";
  const accounts = ctx.accounts.map((a) => `- ${a.id}: ${a.name} (${a.bank}); identified by any of: ${a.markers.join(", ")}${a.tracked === false ? " — NOT TRACKED (its SMS never reach us)" : ""}`).join("\n");
  const untracked = ctx.accounts.filter((a) => a.tracked === false).map((a) => a.name);
  const budgets = ctx.budgets.map((b) => `- ${b.id}: ${b.name}`).join("\n");
  const commitments = ctx.commitments.map((c) => `- ${c.id}: ${c.name}, ~${c.amount} SAR/month${c.payee ? `, usually paid to "${c.payee}"` : ""}`).join("\n");
  const memory = ctx.memory.length
    ? ctx.memory.map((m) => `- "${m.payee}" → ${m.kind}${m.budget_id ? ` / budget ${m.budget_id}` : ""}${m.commitment_id ? ` / commitment ${m.commitment_id}` : ""}`).join("\n")
    : "(none yet)";

  return `You classify one Saudi bank SMS alert for a personal budgeting app. The owner of every account is one person; their name appears in transfers as: ${owner}.
${sender ? `This SMS was sent by "${sender}" — that is the bank whose account it concerns, even if the text carries no account marker.` : ""}

ACCOUNTS
${accounts}

BUDGETS (for kind=spend)
${budgets}

MONTHLY COMMITMENTS (for kind=commitment — a transfer or payment that pays one of these bills)
${commitments}
Monthly salary is about ${ctx.settings.salary} SAR.

WHAT THE OWNER HAS TAUGHT YOU (a payee seen before, and where it belongs — always follow these)
${memory}

KINDS
- salary: incoming salary (راتب) or an incoming credit within 5% of the salary amount.
- income: any other money coming in from someone who is not the owner (refund, family, friend).
- transfer_out: money leaving one of the owner's accounts to another of the owner's own accounts (beneficiary is the owner's own name, or the other listed bank).
- transfer_in: money arriving from the owner's own other account.
- spend: a purchase or payment to a merchant or person that is not a commitment.
- commitment: a payment that clearly pays one of the listed monthly commitments (by payee name or an amount matching the commitment).
- ignore: OTP codes, promotions, balance inquiries, declined transactions, card-activation notices, anything that is not money actually moving.

RULES
- amount is the principal only; put any fee (رسوم) in fee. Amounts may be written "SAR 89.00", "SR250", "500 رس", "27932.0 SAR" — read them all.
${untracked.length ? `- Money sent from a tracked account to the owner's own ${untracked.join(" / ")} account is NOT a transfer: whatever it buys will never be seen, so record it as kind=spend at the moment it leaves, with account = the sending account, counterparty = "Top-up ${untracked.join(" / ")}", budget_id = null and confidence 0.5 so the owner is asked what it was for.\n` : ""}- Card purchases (شراء, مدى, Apple Pay) are spend unless the merchant is a listed commitment payee.
- Pick the budget from the merchant: restaurants/coffee/delivery (Jahez, HungerStation, Starbucks) → food; airlines, hotels, Careem/Uber → travel; games, PlayStation, app stores → fun; Amazon/Noon/Jarir/clothes → shopping; unknown → other with lower confidence.
- Dates: this SMS was received at ${receivedAt} (${TZ}). Bank date formats are inconsistent (DD-MM-YY and YY-MM-DD both occur). Choose the reading that lands within 3 days of the received time; if none does, return null. Output occurred_at as ISO-8601 with the +03:00 offset.
- confidence: 0.95+ when the kind, account and destination are all unambiguous; 0.6–0.8 when you had to guess the budget or the payee is truncated/unknown; below 0.5 if the message is unclear.
- Return only the JSON object.`;
}

// The Shortcut tells us which bank sent the SMS; that decides the account when the text itself doesn't.
function accountFromSender(sender: string | null, ctx: Awaited<ReturnType<typeof loadContext>>) {
  if (!sender) return null;
  const s = sender.toLowerCase().replace(/\s+/g, "");
  return ctx.accounts.find((a) => s.includes(a.name.toLowerCase().replace(/\s+/g, "")) || s.includes(a.id))?.id ?? null;
}

async function classify(text: string, receivedAt: string, sender: string | null, ctx: Awaited<ReturnType<typeof loadContext>>): Promise<Classified> {
  const res = await anthropic.messages.create({
    model: MODEL,
    max_tokens: 1024,
    system: systemPrompt(ctx, receivedAt, sender),
    messages: [{ role: "user", content: text }],
    output_config: { effort: "medium", format: { type: "json_schema", schema: SCHEMA } },
  });
  if (res.stop_reason === "refusal") throw new Error("classifier refused: " + (res.stop_details?.explanation ?? ""));
  const block = res.content.find((b) => b.type === "text");
  if (!block || block.type !== "text") throw new Error("classifier returned no text (stop_reason=" + res.stop_reason + ")");
  const c = JSON.parse(block.text) as Classified;
  if (!ctx.accounts.some((a) => a.id === c.account)) c.account = accountFromSender(sender, ctx);
  return c;
}

// A payee the owner has already labelled always wins over the model's guess.
function applyMemory(c: Classified, ctx: Awaited<ReturnType<typeof loadContext>>) {
  if (!c.counterparty) return c;
  const key = normPayee(c.counterparty);
  const hit = ctx.memory.find((m) => key === m.payee || key.includes(m.payee) || m.payee.includes(key));
  if (!hit) return c;
  return { ...c, kind: hit.kind as Kind, budget_id: hit.budget_id, commitment_id: hit.commitment_id, confidence: 1, reason: c.reason + " (payee memory)" };
}

function needsTap(c: Classified) {
  if (c.kind === "ignore" || c.kind === "salary") return false;
  if (c.confidence < CONFIRM_AT) return true;
  if (c.kind === "spend" && !c.budget_id) return true;
  if (c.kind === "commitment" && !c.commitment_id) return true;
  return false;
}

// Pair this leg with the opposite leg of the same transfer (same amount, other account, within the window).
// Also rescues the common misread: the STC side arrives as "income from <owner>" — once the BSF side
// says transfer_out to the owner's own name, that income row is really transfer_in.
async function matchTransfer(row: { id: number; kind: Kind; amount: number; account_id: string | null; occurred_at: string }) {
  if (!["transfer_out", "transfer_in", "income"].includes(row.kind)) return null;
  const want: Kind[] = row.kind === "transfer_out" ? ["transfer_in", "income"] : ["transfer_out"];
  const t = new Date(row.occurred_at).getTime();
  const lo = new Date(t - TRANSFER_WINDOW_MIN * 60_000).toISOString();
  const hi = new Date(t + TRANSFER_WINDOW_MIN * 60_000).toISOString();
  const { data } = await sb.from("transactions").select("id,kind,account_id")
    .in("kind", want).eq("amount", row.amount).is("pair_id", null).neq("id", row.id)
    .gte("occurred_at", lo).lte("occurred_at", hi).limit(1);
  const other = data?.[0];
  if (!other || other.account_id === row.account_id) return null;
  const outId = row.kind === "transfer_out" ? row.id : other.id;
  const inId = row.kind === "transfer_out" ? other.id : row.id;
  await sb.from("transactions").update({ kind: "transfer_out", pair_id: inId, confirmed: true, confidence: 1 }).eq("id", outId);
  await sb.from("transactions").update({ kind: "transfer_in", pair_id: outId, confirmed: true, confidence: 1, budget_id: null, commitment_id: null }).eq("id", inId);
  return other.id;
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("POST only", { status: 405 });
  if (req.headers.get("x-ingest-key") !== Deno.env.get("INGEST_KEY")) return new Response("unauthorized", { status: 401 });

  const dry = new URL(req.url).searchParams.get("dry") === "1";
  let text = "", sender: string | null = null, receivedAt = new Date().toISOString();
  const ct = req.headers.get("content-type") ?? "";
  if (ct.includes("json")) {
    const body = await req.json();
    text = String(body.text ?? ""); sender = body.sender ?? null;
    if (body.received_at) receivedAt = new Date(body.received_at).toISOString();
  } else {
    text = await req.text();
  }
  text = text.replace(/\r\n?/g, "\n").trim();   // keep line breaks for display; hash the normalized form
  if (!text) return Response.json({ error: "empty text" }, { status: 400 });

  const ctx = await loadContext();

  if (dry) {
    const c = applyMemory(await classify(text, receivedAt, sender, ctx), ctx);
    return Response.json({ dry: true, ...c, needs_tap: needsTap(c) });
  }

  const hash = await sha256(normalize(text));
  const ins = await sb.from("raw_alerts").insert({ hash, text, sender, received_at: receivedAt }).select("id").single();
  if (ins.error) {
    if (ins.error.code === "23505") return Response.json({ duplicate: true });
    return Response.json({ error: ins.error.message }, { status: 500 });
  }
  const rawId = ins.data.id;

  try {
    const c = applyMemory(await classify(text, receivedAt, sender, ctx), ctx);
    const row = {
      raw_id: rawId,
      kind: c.kind,
      account_id: ctx.accounts.some((a) => a.id === c.account) ? c.account : null,
      amount: c.amount, fee: c.fee,
      counterparty: c.counterparty,
      budget_id: ctx.budgets.some((b) => b.id === c.budget_id) ? c.budget_id : null,
      commitment_id: ctx.commitments.some((k) => k.id === c.commitment_id) ? c.commitment_id : null,
      confidence: c.confidence,
      confirmed: !needsTap(c),
      occurred_at: c.occurred_at ?? receivedAt,
      note: c.reason,
    };
    const tx = await sb.from("transactions").insert(row).select("id,kind,amount,account_id,occurred_at").single();
    if (tx.error) throw new Error(tx.error.message);
    const paired = await matchTransfer(tx.data);
    await sb.from("raw_alerts").update({ processed_at: new Date().toISOString() }).eq("id", rawId);
    return Response.json({ id: tx.data.id, ...row, paired_with: paired });
  } catch (e) {
    await sb.from("raw_alerts").update({ error: String(e) }).eq("id", rawId);
    return Response.json({ error: String(e), raw_id: rawId }, { status: 500 });
  }
});
