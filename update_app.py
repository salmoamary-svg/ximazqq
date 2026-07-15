
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Saad Money - autopilot v3.0 (dynamic, single source of truth = data.json).
 
What the robot does each run:
  * reads data.json (balance, cursor, commitments, debts, goals, spend cats).
  * rolls the cycle over on payday (27th; Fri->26th, Sat->28th): clears every
    commitment's paid flag, zeroes this-cycle spending, resets the "under
    salary" meter.
  * reads new transaction emails from saaddata96@gmail.com and:
      - skips OTP, STC card *4220, and credit-card *3368 PURCHASES,
      - credits salary / incoming transfers, debits everything else,
      - buckets each debit into a spending category (for "biggest leak"),
      - auto-marks the credit-card commitment + reduces the credit-card debt
        when it sees a card payment ("تسديد بطاقة ائتمانية"),
      - auto-marks the phone commitment when it sees an STC postpaid payment.
  * SAADMONEY-DATA self-email still overrides the balance (JSON body).
Everything else (ticking commitments, editing debts/goals, setting the true
balance) is done from the app and written straight back to data.json.
Privacy: category is a single word bucket; no merchant text is stored.
"""
import imaplib, email, re, json, os, sys, subprocess
from datetime import datetime, timezone, timedelta, date
 
USER = os.environ["GMAIL_USER"]
PW   = os.environ["GMAIL_APP_PASSWORD"]
DATA = "data.json"
 
AR = str.maketrans("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669","0123456789")
CURR = r"(?:SAR|SR|\u0631\u064a\u0627\u0644|\u0631\u0633|\u0631\.?\s?\u0633)"
NUM  = r"[\d\u0660-\u0669][\d\u0660-\u0669,]*(?:\.[\d\u0660-\u0669]{1,2})?"
AMT_RX = re.compile(r"(?:"+CURR+r"\s*("+NUM+r")|("+NUM+r")\s*"+CURR+r")", re.I)
BAL_RX = re.compile(r"\u0627\u0644\u0631\u0635\u064a\u062f|\u0631\u0635\u064a\u062f|\u0627\u0644\u0645\u062a\u0628\u0642\u064a|\u0627\u0644\u0645\u062a\u0628\u0642\u0649|\u0627\u0644\u0645\u062a\u0648\u0641\u0631|\u0625\u062c\u0645\u0627\u0644\u064a|\u0627\u0644\u0645\u0628\u0644\u063a \u0627\u0644\u0645\u0633\u062a\u062d\u0642")
FEE_RX = re.compile(r"\u0627\u0644\u0631\u0633\u0648\u0645|\u0631\u0633\u0648\u0645")
CREDIT_RX = re.compile(r"\u0648\u0627\u0631\u062f\u0629|\u0648\u0627\u0631\u062f|\u0631\u0627\u062a\u0628|\u0625\u064a\u062f\u0627\u0639|\u0627\u064a\u062f\u0627\u0639|\u0645\u0633\u062a\u0631\u062f|\u0627\u0633\u062a\u0631\u062f\u0627\u062f|refund|salary|deposit|credited|incoming", re.I)
OTP_RX = re.compile(r"\u0631\u0645\u0632 \u0627\u0644\u062a\u062d\u0642\u0642|\u0644\u0627 \u062a\u0634\u0627\u0631\u0643|one.?time|OTP|verification", re.I)
SKIP_CARD_RX = re.compile(r"4220")
CC_RX = re.compile(r"\u0628\u0637\u0627\u0642\u0629 \u0627\u0626\u062a\u0645\u0627\u0646\u064a\u0629")
SETTLE_RX = re.compile(r"\u062a\u0633\u062f\u064a\u062f")
 
def log(*a): print("[saadmoney]", *a, flush=True)
 
def body_text(msg):
    parts=[]
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() in ("text/plain","text/html") and not p.get("Content-Disposition"):
                try: parts.append(p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8","ignore"))
                except Exception: pass
    else:
        try: parts.append(msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8","ignore"))
        except Exception: pass
    return re.sub(r"<[^>]+>"," ","\n".join(parts))
 
def parse_amount(text):
    fees=0.0; primary=None
    for line in text.splitlines():
        if not line.strip(): continue
        if BAL_RX.search(line): continue
        m=AMT_RX.search(line)
        if not m: continue
        val=float((m.group(1) or m.group(2)).translate(AR).replace(",",""))
        if FEE_RX.search(line): fees+=val; continue
        if primary is None: primary=val
    if primary is None: return None
    return round(primary+fees,2)
 
def categorize(text):
    t=text.lower()
    if SETTLE_RX.search(text) and CC_RX.search(text): return "cc"
    if "\u0642\u0633\u0637" in text: return "loan"
    if "stc" in t and "postpaid" in t: return "phone"
    if "stc" in t: return "stc"
    if "tabby" in t or "tamara" in t: return "bnpl"
    if "\u062a\u0623\u0645\u064a\u0646" in text or "insurance" in t: return "carins"
    if "labayh" in t or "medical" in t or "\u0635\u064a\u062f\u0644" in text or "pharmac" in t: return "bills"
    if "\u0635\u0627\u062f\u0631\u0629" in text or ("\u062d\u0648\u0627\u0644\u0629" in text and "\u0648\u0627\u0631\u062f" not in text): return "family"
    if "airlines" in t or "flight" in t or "\u0637\u064a\u0631\u0627\u0646" in text or "booking" in t: return "travel"
    if "amazon" in t or "noon" in t or "jarir" in t or "apple" in t: return "shopping"
    return "everyday"
 
def latest_payday(d):
    def adj(y,m):
        p=date(y,m,27); dow=p.weekday()
        if dow==4: p=date(y,m,26)
        if dow==5: p=date(y,m,28)
        return p
    y,m=d.year,d.month; p=adj(y,m)
    if p>d:
        m-=1
        if m<1: m=12; y-=1
        p=adj(y,m)
    return p
 
def msg_date(msg):
    try:
        d=email.utils.parsedate_to_datetime(msg.get("Date"))
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return datetime.now(timezone.utc)
 
def load():
    return json.load(open(DATA, encoding="utf-8"))
 
def save(d):
    d["processed"]=d.get("processed",[])[-500:]
    json.dump(d, open(DATA,"w",encoding="utf-8"), ensure_ascii=False, indent=1)
 
def find(lst, key, val):
    for x in lst:
        if x.get(key)==val: return x
    return None
 
def main():
    d=load()
    d.setdefault("spendCats",{})
    d.setdefault("commitments",[]); d.setdefault("debts",[]); d.setdefault("goals",[])
    d.setdefault("processed",[]); d.setdefault("lastCycleCats",{})
    balance=float(d["balance"])
    cursor=datetime.fromisoformat(d["cursor"].replace("Z","+00:00"))
    changed=False
 
    riyadh=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))
    stamp=riyadh.strftime("%Y-%m-%d %I:%M %p")
    today=riyadh.date()
 
    # ---- payday rollover ----
    pay=latest_payday(today).isoformat()
    if d.get("cycleStart") != pay:
        d["lastCycleCats"]=dict(d.get("spendCats",{}))
        for c in d["commitments"]: c["paid"]=False
        d["spendCats"]={}
        g=find(d["goals"],"id","under")
        if g: g["cur"]=0
        d["cycleStart"]=pay
        changed=True
        log("cycle rollover -> new payday cycle", pay)
 
    # ---- read transaction emails ----
    M=imaplib.IMAP4_SSL("imap.gmail.com"); M.login(USER,PW)
    M.select('"[Gmail]/All Mail"', readonly=True)
    since=(cursor-timedelta(days=1)).strftime("%d-%b-%Y")
 
    typ,ids=M.search(None,'(FROM "saaddata96@gmail.com" SINCE '+since+')')
    rows=[]
    for i in (ids[0].split() if typ=="OK" else []):
        t2,data=M.fetch(i,"(RFC822)")
        if t2!="OK": continue
        msg=email.message_from_bytes(data[0][1])
        mid=msg.get("Message-ID", i.decode())
        if mid in d["processed"]: continue
        dt=msg_date(msg)
        if dt<=cursor: continue
        text=body_text(msg)
        rows.append((dt,mid,text))
    rows.sort(key=lambda r:r[0])
 
    for dt,mid,text in rows:
        if OTP_RX.search(text):
            d["processed"].append(mid); changed=True; continue
        if SKIP_CARD_RX.search(text):
            d["processed"].append(mid); changed=True
            log("skip STC *4220", dt.strftime("%m-%d %H:%M")); continue
        if CC_RX.search(text) and not SETTLE_RX.search(text):
            d["processed"].append(mid); changed=True
            log("skip credit-card purchase", dt.strftime("%m-%d %H:%M")); continue
        amt=parse_amount(text)
        if amt is None:
            d["processed"].append(mid); changed=True
            log("skip (no amount)", dt.strftime("%m-%d %H:%M")); continue
 
        if CREDIT_RX.search(text):
            balance+=amt
            log("+%.2f -> %.2f" % (amt,balance))
        else:
            balance-=amt
            cat=categorize(text)
            d["spendCats"][cat]=round(d["spendCats"].get(cat,0)+amt,2)
            # auto-attribution (conservative)
            if cat=="cc":
                cm=find(d["commitments"],"id","cc")
                if cm: cm["paid"]=True
                db=find(d["debts"],"id","cc")
                if db: db["remaining"]=max(0,round(db["remaining"]-amt,2))
            if cat=="phone":
                cm=find(d["commitments"],"id","phone")
                if cm: cm["paid"]=True
            log("-%.2f [%s] -> %.2f" % (amt,cat,balance))
 
        d["processed"].append(mid)
        if dt>cursor: cursor=dt
        changed=True
 
    # ---- live "spent this cycle" meter ----
    spent=round(sum(d["spendCats"].values()),2)
    g=find(d["goals"],"id","under")
    if g: g["cur"]=spent
 
    # ---- SAADMONEY-DATA balance override ----
    typ,ids=M.search(None,'(FROM "'+USER+'" SUBJECT "SAADMONEY-DATA" SINCE '+since+')')
    latest=None
    for i in (ids[0].split() if typ=="OK" else []):
        t2,data=M.fetch(i,"(RFC822)")
        if t2!="OK": continue
        msg=email.message_from_bytes(data[0][1])
        dt=msg_date(msg); mid=msg.get("Message-ID", i.decode())
        if latest is None or dt>latest[0]: latest=(dt,body_text(msg),mid)
    if latest and latest[2]!=d.get("last_data_msgid"):
        m=re.search(r"\{.*\}", latest[1], re.S)
        if m:
            try:
                obj=json.loads(m.group(0))
                if "balance" in obj:
                    balance=float(obj["balance"]); changed=True
                    d["last_data_msgid"]=latest[2]
                    log("balance override ->", balance)
            except json.JSONDecodeError as e:
                log("override JSON invalid:", e)
    M.logout()
 
    d["balance"]=round(balance,2)
    d["cursor"]=cursor.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
    d["asOf"]=stamp
    save(d)
 
    subprocess.run(["git","config","user.name","saad-money-bot"],check=True)
    subprocess.run(["git","config","user.email","bot@saadmoney.local"],check=True)
    subprocess.run(["git","add",DATA],check=True)
    c=subprocess.run(["git","commit","-m","auto-update "+stamp])
    if c.returncode==0:
        subprocess.run(["git","push"],check=True)
        log("pushed. balance %.2f at %s" % (balance,stamp))
    else:
        log("no change to commit (balance %.2f)" % balance)
 
if __name__=="__main__":
    sys.exit(main())
