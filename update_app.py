#!/usr/bin/env python3
"""
Saad Money - daily autopilot (v2, deduction model).
BSF transaction SMS emails do NOT include a running balance, so the robot
tracks it by deduction: start from the known balance in data/state.json and
apply each new transaction email from saaddata96@gmail.com.
  - amount = first number before SAR / riyal (Arabic digits ok)
  - credit if text has incoming/salary/deposit/refund keywords, else debit
Also: full DATA override via self-email with subject SAADMONEY-DATA whose
body contains JSON -> replaces the /*DATA-START*/.../*DATA-END*/ block.
Privacy: repo stores only balance, cursor, message-ids. No merchant text.
"""
import imaplib, email, re, json, os, sys, subprocess
from datetime import datetime, timezone, timedelta

USER = os.environ["GMAIL_USER"]
PW = os.environ["GMAIL_APP_PASSWORD"]
INDEX = "index.html"
STATE = "data/state.json"

AR = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
AMOUNT_RX = re.compile(r"([\d٠-٩][\d٠-٩,]*(?:\.[\d٠-٩]{1,2})?)\s*(?:SAR|ريال|ر\.س)", re.I)
CREDIT_RX = re.compile(r"وارد|راتب|إيداع|ايداع|مسترد|استرداد|refund|salary|deposit|credited|incoming", re.I)
OTP_RX = re.compile(r"رمز التحقق|لا تشارك|one.?time|OTP|verification", re.I)
AMOUNT_AFTER_RX = re.compile(r"(?:SAR|ريال)\s*([\d٠-٩][\d٠-٩,]*(?:\.[\d٠-٩]{1,2})?)")

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
    return re.sub(r"<[^>]+>", " ", t)

def load_state():
    return json.load(open(STATE))

def save_state(st):
    os.makedirs("data", exist_ok=True)
    st["processed"] = st["processed"][-500:]
    json.dump(st, open(STATE, "w"), indent=1)

def msg_date(msg):
    try:
        d = email.utils.parsedate_to_datetime(msg.get("Date"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return datetime.now(timezone.utc)

def main():
    st = load_state()
    balance = float(st["balance"])
    cursor = datetime.fromisoformat(st["cursor"].replace("Z", "+00:00"))
    changed = False

    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(USER, PW)
    M.select('"[Gmail]/All Mail"', readonly=True)
    since = (cursor - timedelta(days=1)).strftime("%d-%b-%Y")

    html = open(INDEX, encoding="utf-8").read()
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")

    # ---- 1) deduction sync from saaddata96 transaction emails ----
    txns = []
    typ, ids = M.search(None, f'(FROM "saaddata96@gmail.com" SINCE {since})')
    for i in (ids[0].split() if typ == "OK" else []):
        typ2, data = M.fetch(i, "(RFC822)")
        if typ2 != "OK":
            continue
        msg = email.message_from_bytes(data[0][1])
        mid = msg.get("Message-ID", i.decode())
        if mid in st["processed"]:
            continue
        d = msg_date(msg)
        if d <= cursor:
            continue
        text = body_text(msg)
        if OTP_RX.search(text):
            st["processed"].append(mid)
            changed = True
            log(f"skipped OTP email dated {d:%Y-%m-%d %H:%M}")
            continue
        m = AMOUNT_RX.search(text)
        if m:
            amt = float(m.group(1).translate(AR).replace(",", ""))
        else:
            parts = AMOUNT_AFTER_RX.findall(text)
            if not parts:
                st["processed"].append(mid)
                changed = True
                log(f"skipped (no amount) email dated {d:%Y-%m-%d %H:%M}")
                continue
            amt = sum(float(p.translate(AR).replace(",", "")) for p in parts)
        sign = 1 if CREDIT_RX.search(text) else -1
        txns.append((d, sign * amt, mid))

    txns.sort(key=lambda t: t[0])
    for d, delta, mid in txns:
        balance += delta
        st["processed"].append(mid)
        if d > cursor:
            cursor = d
        changed = True
        log(f"{delta:+.2f} on {d:%Y-%m-%d %H:%M} -> balance {balance:.2f}")

    if txns:
        html2 = re.sub(r"(balance\s*:\s*)[\d_.,]+", rf"\g<1>{balance:.2f}", html, count=1)
        html2 = re.sub(r'(asOf\s*:\s*")[^"]*(")', rf"\g<1>{today}\g<2>", html2, count=1)
        html = html2
    st["balance"] = round(balance, 2)
    st["cursor"] = cursor.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    # ---- 2) full DATA override from Saad (subject SAADMONEY-DATA) ----
    typ, ids = M.search(None, f'(FROM "{USER}" SUBJECT "SAADMONEY-DATA" SINCE {since})')
    latest = None
    for i in (ids[0].split() if typ == "OK" else []):
        typ2, data = M.fetch(i, "(RFC822)")
        if typ2 != "OK":
            continue
        msg = email.message_from_bytes(data[0][1])
        mid = msg.get("Message-ID", i.decode())
        d = msg_date(msg)
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
                    html = html2
                    changed = True
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
        log(f"pushed. balance {balance:.2f}")
    else:
        log("nothing to update.")

if __name__ == "__main__":
    sys.exit(main())
