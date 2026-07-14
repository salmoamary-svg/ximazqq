#!/usr/bin/env python3
"""
Saad Money — Robot v2.3 (with all queued fixes)
- Skip STC card (4220) transactions (fix #1)
- DATA-override balance sync + quoted regex (fix #2)
- Self-transfer model already chosen in v2.2
"""
 
import os
import re
import json
import base64
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google.auth.oauthlib.flow import InstalledAppFlow
from google.cloud import secretmanager
import imaplib
import email
from email.header import decode_header
 
# Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
 
# Regex patterns
AMOUNT_RX = r'([0-9]+(?:\.[0-9]+)?)\s*(?:SAR|ريال|SR)'
AMOUNT_RX_ALT = r'(?:SAR|ريال|SR)\s*([0-9]+(?:\.[0-9]+)?)'
CREDIT_RX = r'\b(?:وارد|راتب|إيداع|مسترد|refund|salary|deposit|credit)\b'
OTP_RX = r'(?:رمز التحقق|OTP|verification code|لا تشارك|do not share)'
BALANCE_RX = r'("?balance"?\s*:\s*)([0-9]+(?:\.[0-9]+)?)'  # FIX #2: accept optional quotes
 
def get_gmail_credentials():
    """Get Gmail API credentials from environment secrets."""
    gmail_user = os.getenv('GMAIL_USER')
    gmail_app_password = os.getenv('GMAIL_APP_PASSWORD')
    
    if not gmail_user or not gmail_app_password:
        raise ValueError("GMAIL_USER or GMAIL_APP_PASSWORD not set")
    
    return gmail_user, gmail_app_password
 
def connect_imap():
    """Connect to Gmail IMAP."""
    gmail_user, gmail_app_password = get_gmail_credentials()
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(gmail_user, gmail_app_password)
    return imap
 
def body_text(msg):
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                return payload.decode('utf-8', errors='ignore')
    else:
        payload = msg.get_payload(decode=True)
        return payload.decode('utf-8', errors='ignore')
    return ""
 
def extract_amount(text):
    """Extract first amount in SAR from text."""
    match = re.search(AMOUNT_RX, text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    
    match = re.search(AMOUNT_RX_ALT, text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    
    return None
 
def is_credit(text):
    """Check if transaction is credit."""
    return bool(re.search(CREDIT_RX, text, re.IGNORECASE))
 
def is_otp(text):
    """Check if email is OTP/verification (skip)."""
    return bool(re.search(OTP_RX, text, re.IGNORECASE))
 
def load_state(repo_path):
    """Load state from data/state.json."""
    state_file = os.path.join(repo_path, 'data', 'state.json')
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            return json.load(f)
    return {
        'balance': 0,
        'cursor': (datetime.utcnow() - timedelta(days=1)).isoformat() + 'Z',
        'processed': [],
        'last_data_msgid': None
    }
 
def save_state(repo_path, state):
    """Save state to data/state.json."""
    state_file = os.path.join(repo_path, 'data', 'state.json')
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)
 
def process_emails(repo_path):
    """Main robot: fetch, process, update state."""
    st = load_state(repo_path)
    imap = connect_imap()
    imap.select("All Mail")
    
    # Search: from saaddata96@gmail.com, since cursor-1d
    cursor_date = datetime.fromisoformat(st['cursor'].replace('Z', '+00:00'))
    search_since = (cursor_date - timedelta(days=1)).strftime('%d-%b-%Y')
    
    status, messages = imap.search(None, f'FROM saaddata96@gmail.com SINCE {search_since}')
    msg_ids = messages[0].split()
    
    processed_count = 0
    for msg_id in msg_ids:
        # Skip if already processed
        if msg_id.decode() in st['processed']:
            continue
        
        status, msg_data = imap.fetch(msg_id, '(RFC822)')
        msg = email.message_from_bytes(msg_data[0][1])
        msg_id_str = msg_id.decode()
        
        # Get message date
        msg_date_str = msg.get('Date')
        msg_date = email.utils.parsedate_to_datetime(msg_date_str) if msg_date_str else datetime.utcnow()
        msg_date_iso = msg_date.isoformat()
        
        # Skip if before cursor
        if msg_date_iso <= st['cursor']:
            continue
        
        text = body_text(msg)
        
        # Skip OTP emails
        if is_otp(text):
            st['processed'].append(msg_id_str)
            print(f"[SKIP] OTP email {msg_id_str}")
            continue
        
        # FIX #1: Skip STC card (4220) transactions
        if "4220" in text:
            st['processed'].append(msg_id_str)
            print(f"[SKIP] STC card (*4220) {msg_id_str}: {text[:60]}")
            continue
        
        amount = extract_amount(text)
        if amount is None:
            st['processed'].append(msg_id_str)
            print(f"[SKIP] No amount found in {msg_id_str}")
            continue
        
        # Determine credit or debit
        if is_credit(text):
            st['balance'] += amount
            print(f"[CREDIT] +{amount} SAR → {st['balance']}")
        else:
            st['balance'] -= amount
            print(f"[DEBIT] -{amount} SAR → {st['balance']}")
        
        st['processed'].append(msg_id_str)
        st['cursor'] = msg_date_iso
        st['last_data_msgid'] = msg_id_str
        processed_count += 1
    
    # Cap processed list to 500
    if len(st['processed']) > 500:
        st['processed'] = st['processed'][-500:]
    
    imap.close()
    imap.logout()
    
    # FIX #2: Check for SAADMONEY-DATA override
    status, override_msgs = imap.search(None, 'SUBJECT "SAADMONEY-DATA"')
    if override_msgs[0]:
        imap = connect_imap()
        imap.select("All Mail")
        status, msg_data = imap.fetch(override_msgs[0].split()[-1], '(RFC822)')
        msg = email.message_from_bytes(msg_data[0][1])
        text = body_text(msg)
        
        # Parse JSON override
        try:
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                obj = json.loads(json_match.group())
                if 'balance' in obj:
                    st['balance'] = obj['balance']
                    print(f"[OVERRIDE] Balance set to {st['balance']} from SAADMONEY-DATA")
        except Exception as e:
            print(f"[OVERRIDE ERROR] {e}")
        
        imap.close()
        imap.logout()
    
    return st, processed_count
 
def update_html(repo_path, state):
    """Update index.html with new balance and timestamp."""
    index_file = os.path.join(repo_path, 'index.html')
    
    with open(index_file, 'r') as f:
        content = f.read()
    
    # Update balance (safe regex with optional quotes - FIX #2)
    content = re.sub(
        r'("?balance"?\s*:\s*)([0-9]+(?:\.[0-9]+)?)',
        rf'\g<1>{state["balance"]}',
        content
    )
    
    # Update asOf timestamp
    content = re.sub(
        r'("?asOf"?\s*:\s*)"[^"]*"',
        rf'\g<1>"{datetime.utcnow().isoformat()}Z"',
        content
    )
    
    with open(index_file, 'w') as f:
        f.write(content)
    
    print(f"✓ Updated index.html: balance={state['balance']}, asOf={datetime.utcnow().isoformat()}Z")
 
def commit_and_push(repo_path):
    """Commit and push to GitHub."""
    os.chdir(repo_path)
    os.system('git config user.email "saad-money-updater@bot"')
    os.system('git config user.name "Saad Money Robot"')
    os.system('git add data/state.json index.html')
    os.system(f'git commit -m "Robot v2.3: skip 4220, fix override sync {datetime.utcnow().isoformat()}"')
    os.system('git push origin main')
    print("✓ Committed and pushed to GitHub")
 
def main():
    repo_path = os.getenv('GITHUB_WORKSPACE', '.')
    
    print("🤖 Saad Money Robot v2.3 starting...")
    print(f"   Repo: {repo_path}")
    
    try:
        state, processed_count = process_emails(repo_path)
        print(f"✓ Processed {processed_count} new emails")
        
        save_state(repo_path, state)
        print(f"✓ State saved: balance={state['balance']}")
        
        update_html(repo_path, state)
        commit_and_push(repo_path)
        
        print("✅ Robot run complete!")
    except Exception as e:
        print(f"❌ Error: {e}")
        raise
 
if __name__ == "__main__":
    main()
