#!/usr/bin/env python3
"""
Saad Money — daily autopilot.
Runs inside GitHub Actions. Reads Gmail via IMAP (app password), then:
 1) BALANCE SYNC: newest email from saaddata96@gmail.com that contains a
    BSF balance -> updates DATA.balance + DATA.asOf in index.html.
 2) FULL DATA OVERRIDE: newest email from Saad himself with subject
    'SAADMONEY-DATA' whose body contains a JSON object -> replaces the whole
    DATA block (this is how Claude ships monthly deep updates: Claude creates
    a Gmail draft, Saad taps Send, robot applies it overnight).
Privacy: nothing from email bodies is stored in the repo except the balance
number and timestamps. No merchant text, no names.
"""
import imaplib, email, re, json, os, sys, subprocess
from email.header import decode_header
from datetime import datetime, timezone, timedelta

USER = os.environ["GMAIL_USER"]
PW   = os.environ["GMAIL_APP_PASSWORD"]
INDEX = "index.html"
STATE = "data/state.json"

def log(*a): print("[saadmoney]", *a, flush=True)

def body_text(msg):
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() in ("text/plain", "text/html") and not p.get("Content-Disposition"):
                try:
                    parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "ignore"))
                except Exception:
                    pass
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "ignore"))
        except Exception:
            pass
    t = "\n".join(parts)
    return re.sub(r"<[^>]+>", " ", t)  # crude html strip is fine for SMS bodies

# Balance extraction: BSF SMS usually carries remaining balance.
# Match Arabic & English variants; tolerant of formatting.
BAL_RES = [
    re.compile(r"(?:الرصيد|رصيدك|رصيد)\D{0,12}?([\d١٢٣٤٥٦٧٨٩٠,]+(?:\.\d{1,2})?)"),
    re.compile(r"(?:Balance|Avail(?:able)?\.? ?Bal(?:ance)?)\D{0,12}?([\d,]+(?:\.\d{1,2})?)", re.I),
]
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
BSF_MARK = re.compile(r"(BSF|فرنسي|الفرنسي|FRANSI)", re.I)

def extract_balance(text):
    if not BSF_MARK.search(text):
        return None
    for rx in BAL_RES:
        m = rx.search(text)
        if m:
            raw = m.group(1).translate(AR_DIGITS).replace(",", "")
            try:
                v = float(raw)
                if 0 <= v < 10_000_000:
                    return v
            except ValueError:
                pass
    return None

def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"processed": [], "last_balance": None, "last_data_msgid": None}

def save_state(st):
    os.makedirs("data", exist_ok=True)
    st["processed"] = st["processed"][-500:]
    json.dump(st, open(STATE, "w"), indent=1)

def main():
    st = load_state()
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(USER, PW)
    M.select('"[Gmail]/All Mail"', readonly=True)
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d-%b-%Y")

    changed = False
    html = open(INDEX, encoding="utf-8").read()
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")

    # ---- 1) balance sync from saaddata96 ----
    typ, ids = M.search(None, f'(FROM "saaddata96@gmail.com" SINCE {since})')
    best = None  # (datetime, balance, msgid)
    for i in (ids[0].split() if typ == "OK" else []):
        typ2, data = M.fetch(i, "(RFC822)")
        if typ2 != "OK": continue
        msg = email.message_from_bytes(data[0][1])
        mid = msg.get("Message-ID", i.decode())
        try:
            d = email.utils.parsedate_to_datetime(msg.get("Date"))
        except Exception:
            d = datetime.now(timezone.utc)
        bal = extract_balance(body_text(msg))
        if bal is not None and (best is None or d > best[0]):
            best = (d, bal, mid)
    if best and best[2] not in st["processed"]:
        new_bal = best[1]
        html2 = re.sub(r"(balance\s*:\s*)[\d_.,]+", rf"\g<1>{new_bal:g}", html, count=1)
        html2 = re.sub(r'(asOf\s*:\s*")[^"]*(")', rf"\g<1>{today}\g<2>", html2, count=1)
        if html2 != html:
            html = html2; changed = True
            st["processed"].append(best[2]); st["last_balance"] = new_bal
            log(f"balance -> {new_bal} (from email dated {best[0]:%Y-%m-%d %H:%M})")
    else:
        log("no new BSF balance found in last 7 days")

    # ---- 2) full DATA override from Saad (subject SAADMONEY-DATA) ----
    typ, ids = M.search(None, f'(FROM "{USER}" SUBJECT "SAADMONEY-DATA" SINCE {since})')
    latest = None
    for i in (ids[0].split() if typ == "OK" else []):
        typ2, data = M.fetch(i, "(RFC822)")
        if typ2 != "OK": continue
        msg = email.message_from_bytes(data[0][1])
        mid = msg.get("Message-ID", i.decode())
        try:
            d = email.utils.parsedate_to_datetime(msg.get("Date"))
        except Exception:
            d = datetime.now(timezone.utc)
        if latest is None or d > latest[0]:
            latest = (d, body_text(msg), mid)
    if latest and latest[2] != st.get("last_data_msgid"):
        m = re.search(r"\{.*\}", latest[1], re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                block = "/*DATA-START*/\nconst DATA=" + json.dumps(obj, ensure_ascii=False) + ";\n/*DATA-END*/"
                html2 = re.sub(r"/\*DATA-START\*/.*?/\*DATA-END\*/", block, html, flags=re.S)
                if html2 != html:
                    html = html2; changed = True
                    st["last_data_msgid"] = latest[2]
                    log("full DATA override applied")
            except json.JSONDecodeError as e:
                log("override email found but JSON invalid:", e)
    M.logout()

    if changed:
        open(INDEX, "w", encoding="utf-8").write(html)
        save_state(st)
        subprocess.run(["git", "config", "user.name", "saad-money-bot"], check=True)
        subprocess.run(["git", "config", "user.email", "bot@saadmoney.local"], check=True)
        subprocess.run(["git", "add", INDEX, STATE], check=True)
        subprocess.run(["git", "commit", "-m", f"auto-update {today}"], check=True)
        subprocess.run(["git", "push"], check=True)
        log("pushed.")
    else:
        save_state(st)
        log("nothing to update.")

if __name__ == "__main__":
    sys.exit(main())
