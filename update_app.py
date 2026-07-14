#!/usr/bin/env python3
"""
Saad Money - daily autopilot (v2.4, deduction model).
BSF transaction SMS emails do NOT include a running balance, so the robot
tracks it by deduction: start from the known balance in data/state.json and
apply each new transaction email from saaddata96@gmail.com.

Rules:
  - amount = the transaction amount (labelled line), plus explicit fees.
    Balance / "amount due" / loan-remaining lines are ignored so they can
    never be mistaken for the transaction amount.
  - credit if the text has incoming/salary/deposit/refund keywords, else debit.
  - OTP emails skipped.
  - STC card *4220 skipped: it is topped up from BSF and the top-up transfer
    is already deducted, so counting *4220 purchases would double-count.
  - Credit-card *3368 PURCHASES skipped (they hit the bank only when the card
    bill is paid). The card PAYMENT ("تسديد بطاقة ائتمانية") IS counted.

Also: full DATA override via self-email with subject SAADMONEY-DATA whose
body contains JSON -> replaces the /*DATA-START*/.../*DATA-END*/ block and
re-seeds the state balance if the JSON includes "balance".
Privacy: repo stores only balance, cursor, message-ids. No merchant text.
"""
import imaplib, email, re, json, os, sys, subprocess
from datetime import datetime, timezone, timedelta

USER = os.environ["GMAIL_USER"]
PW = os.environ["GMAIL_APP_PASSWORD"]
INDEX = "index.html"
STATE = "data/state.json"

AR = str.maketrans("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "0123456789")

# currency tokens seen in BSF / STC SMS: SAR, SR, ريال, رس, ر.س
CURR = r"(?:SAR|SR|\u0631\u064a\u0627\u0644|\u0631\u0633|\u0631\.?\s?\u0633)"
NUM = r"[\d\u0660-\u0669][\d\u0660-\u0669,]*(?:\.[\d\u0660-\u0669]{1,2})?"
AMT_RX = re.compile(r"(?:" + CURR + r"\s*(" + NUM + r")|(" + NUM + r")\s*" + CURR + r")", re.I)

# lines that carry a BALANCE / total, not the transaction delta -> ignored
BAL_RX = re.compile(r"\u0627\u0644\u0631\u0635\u064a\u062f|\u0631\u0635\u064a\u062f|\u0627\u0644\u0645\u062a\u0628\u0642\u064a|\u0627\u0644\u0645\u062a\u0628\u0642\u0649|\u0627\u0644\u0645\u062a\u0648\u0641\u0631|\u0625\u062c\u0645\u0627\u0644\u064a|\u0627\u0644\u0645\u0628\u0644\u063a \u0627\u0644\u0645\u0633\u062a\u062d\u0642")
# fee lines -> added to the transaction amount
FEE_RX = re.compile(r"\u0627\u0644\u0631\u0633\u0648\u0645|\u0631\u0633\u0648\u0645")

CREDIT_RX = re.compile(r"\u0648\u0627\u0631\u062f\u0629|\u0648\u0627\u0631\u062f|\u0631\u0627\u062a\u0628|\u0625\u064a\u062f\u0627\u0639|\u0627\u064a\u062f\u0627\u0639|\u0645\u0633\u062a\u0631\u062f|\u0627\u0633\u062a\u0631\u062f\u0627\u062f|refund|salary|deposit|credited|incoming", re.I)
OTP_RX = re.compile(r"\u0631\u0645\u0632 \u0627\u0644\u062a\u062d\u0642\u0642|\u0644\u0627 \u062a\u0634\u0627\u0631\u0643|one.?time|OTP|verification", re.I)
SKIP_CARD_RX = re.compile(r"4220")                                   # STC card
CC_RX = re.compile(r"\u0628\u0637\u0627\u0642\u0629 \u0627\u0626\u062a\u0645\u0627\u0646\u064a\u0629")  # credit card
SETTLE_RX = re.compile(r"\u062a\u0633\u062f\u064a\u062f")             # card bill payment

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

def parse_amount(text):
    """Transaction amount = first labelled amount line (not a balance line),
    plus any explicit fee lines. Returns float or None."""
    fees = 0.0
    primary = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if BAL_RX.search(line):        # skip balance / total lines entirely
            continue
        m = AMT_RX.search(line)
        if not m:
            continue
        val = float((m.group(1) or m.group(2)).translate(AR).replace(",", ""))
        if FEE_RX.search(line):
            fees += val
            continue
        if primary is None:
            primary = val
    if primary is None:
        return None
    return round(primary + fees, 2)

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
    now_riyadh = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=3)))
    # AM/PM timestamp, e.g. "2026-07-15 01:30 AM"
    stamp = now_riyadh.strftime("%Y-%m-%d %I:%M %p")

    # ---- 1) deduction sync from saaddata96 transaction emails ----
    txns = []
    typ, ids = M.search(None, '(FROM "saaddata96@gmail.com" SINCE ' + since + ')')
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
            st["processed"].append(mid); changed = True
            log("skipped OTP email dated %s" % d.strftime("%Y-%m-%d %H:%M"))
            continue
        if SKIP_CARD_RX.search(text):
            st["processed"].append(mid); changed = True
            log("skipped STC-card (*4220) txn dated %s" % d.strftime("%Y-%m-%d %H:%M"))
            continue
        if CC_RX.search(text) and not SETTLE_RX.search(text):
            st["processed"].append(mid); changed = True
            log("skipped credit-card purchase (*3368) dated %s" % d.strftime("%Y-%m-%d %H:%M"))
            continue

        amt = parse_amount(text)
        if amt is None:
            st["processed"].append(mid); changed = True
            log("skipped (no amount) email dated %s" % d.strftime("%Y-%m-%d %H:%M"))
            continue

        sign = 1 if CREDIT_RX.search(text) else -1
        txns.append((d, sign * amt, mid))

    txns.sort(key=lambda t: t[0])
    for d, delta, mid in txns:
        balance += delta
        st["processed"].append(mid)
        if d > cursor:
            cursor = d
        changed = True
        log("%+.2f on %s -> balance %.2f" % (delta, d.strftime("%Y-%m-%d %H:%M"), balance))

    if txns:
        html = re.sub(r'("?balance"?\s*:\s*)[0-9]+(?:\.[0-9]+)?', r"\g<1>%.2f" % balance, html, count=1)
    st["balance"] = round(balance, 2)
    st["cursor"] = cursor.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    # ---- 2) full DATA override from Saad (subject SAADMONEY-DATA) ----
    typ, ids = M.search(None, '(FROM "' + USER + '" SUBJECT "SAADMONEY-DATA" SINCE ' + since + ')')
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
                    if "balance" in obj:
                        st["balance"] = float(obj["balance"])
                    log("full DATA override applied")
            except json.JSONDecodeError as e:
                log("override email found but JSON invalid:", e)
    M.logout()

    if changed:
        # refresh the "last updated" stamp (AM/PM, Riyadh) on every change
        html = re.sub(r'("?asOf"?\s*:\s*")[^"]*(")', r"\g<1>" + stamp + r"\g<2>", html, count=1)
        open(INDEX, "w", encoding="utf-8").write(html)
        save_state(st)
        subprocess.run(["git", "config", "user.name", "saad-money-bot"], check=True)
        subprocess.run(["git", "config", "user.email", "bot@saadmoney.local"], check=True)
        subprocess.run(["git", "add", INDEX, STATE], check=True)
        subprocess.run(["git", "commit", "-m", "auto-update " + stamp], check=True)
        subprocess.run(["git", "push"], check=True)
        log("pushed. balance %.2f at %s" % (balance, stamp))
    else:
        save_state(st)
        log("nothing to update.")

if __name__ == "__main__":
    sys.exit(main())
    <script>
(function(){
var API='https://api.github.com/repos/salmoamary-svg/ximazqq';
function fmt(n){return n.toLocaleString('en-US')}
function patch(bal){
 try{
  var el=document.getElementById('bal');
  if(el)el.innerHTML=fmt(bal)+' <span class="sar">SAR</span>';
  var m=document.getElementById('m-bal');if(m)m.textContent=fmt(bal);
  if(typeof DATA!=='undefined')DATA.balance=bal;
 }catch(e){}
}
async function freshBalance(t){
 var h={'Accept':'application/vnd.github.raw'};
 if(t)h['Authorization']='Bearer '+t;
 try{
  var r=await fetch(API+'/contents/data/state.json?ref=main&_='+Date.now(),{headers:h});
  if(!r.ok)return null;
  var s=await r.json();
  return (typeof s.balance==='number')?s.balance:null;
 }catch(e){return null}
}
freshBalance(null).then(function(b){if(b!=null)patch(b)});
var old=document.getElementById('robotBtn');if(old)old.remove();
var b=document.createElement('button');
b.id='robotBtn';b.textContent='⟳ Update';
b.style.cssText='position:fixed;right:14px;bottom:96px;z-index:9999;background:linear-gradient(135deg,#2dd4bf,#1f9e8f);color:#062a26;border:none;border-radius:22px;padding:10px 16px;font-size:14px;font-weight:700;box-shadow:0 4px 14px rgba(0,0,0,.4)';
b.onclick=async function(){
 var t=localStorage.getItem('saad_money_gh_pat');
 if(!t){t=prompt('Paste your GitHub token');if(!t)return;t=t.trim();localStorage.setItem('saad_money_gh_pat',t);}
 b.disabled=true;b.textContent='⏳ starting…';
 var H={'Authorization':'Bearer '+t,'Accept':'application/vnd.github+json'};
 var WF=API+'/actions/workflows/update.yml';
 try{
  var prev=null;
  try{var p=await(await fetch(WF+'/runs?per_page=1',{headers:H})).json();prev=(p.workflow_runs&&p.workflow_runs[0])?p.workflow_runs[0].id:null;}catch(e){}
  var d=await fetch(WF+'/dispatches',{method:'POST',headers:H,body:JSON.stringify({ref:'main'})});
  if(d.status===401||d.status===403){localStorage.removeItem('saad_money_gh_pat');b.textContent='🔑 token problem — tap again';b.disabled=false;return;}
  if(d.status!==204){b.textContent='✗ error '+d.status;b.disabled=false;return;}
  var t0=Date.now();
  var timer=setInterval(async function(){
   var secs=Math.round((Date.now()-t0)/1000);
   b.textContent='🤖 robot working… '+secs+'s';
   if(secs>180){clearInterval(timer);b.textContent='⌛ slow — check Actions';b.disabled=false;return;}
   try{
    var j=await(await fetch(WF+'/runs?per_page=1&_='+Date.now(),{headers:H})).json();
    var run=j.workflow_runs&&j.workflow_runs[0];
    if(run&&run.id!==prev&&run.status==='completed'){
     clearInterval(timer);
     if(run.conclusion==='success'){
      var nb=await freshBalance(t);
      if(nb!=null)patch(nb);
      b.textContent='✅ up to date';
      b.disabled=false;
      setTimeout(function(){location.reload()},60000);
     }else{b.textContent='✗ robot failed — check Actions';b.disabled=false;}
    }
   }catch(e){}
  },5000);
 }catch(e){b.textContent='✗ no internet?';b.disabled=false;}
};
document.body.appendChild(b);
})();
</script>