import imaplib
import email
import re
import requests
import time
import json
import os
from bs4 import BeautifulSoup

# Assuming these imports exist in your project structure
from firebase_config import PaymentDB, PayoutDB, ConfigDB
from config import CHECK_INTERVAL, TELEGRAM_BOT_TOKEN

processed_emails = {}

def extract_famid(text, utr):
    """Extract FamID from URL or UTR with better regex"""
    # URL pattern
    url_match = re.search(r'fam[app]*\..*?[?&]id=([A-Za-z0-9]+)', text, re.IGNORECASE)
    if url_match:
        return url_match.group(1).strip(), "🔗 URL"
    
    # UTR pattern
    if utr and utr != "N/A":
        utr_match = re.search(r'([A-Z0-9]{6,15})', utr)
        if utr_match:
            return utr_match.group(1).strip(), "🔐 UTR"
    
    return "NOT_FOUND", "❌ None"

def send_to_telegram(message, target_chats):
    """Send formatted message to Telegram channels"""
    for chat_id in target_chats:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id, 
            "text": message, 
            "parse_mode": "Markdown"
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"❌ Telegram Error [{chat_id}]: {e}")

def parse_famapp_email(html_body, account_email):
    """Parse FamApp deposit email using BeautifulSoup"""
    soup = BeautifulSoup(html_body, "html.parser")
    text = soup.get_text(separator="\n")

    # Extracting details using cleaner regex
    received_match = re.search(r"received\s+(₹?\d+(?:\.\d+)?)\s+from\s+([^\n]+)", text, re.IGNORECASE)
    txn_match = re.search(r"Transaction ID\s*:\s*([A-Za-z0-9]+)", text, re.IGNORECASE)
    utr_match = re.search(r"UTR\s*:\s*(\d+)", text, re.IGNORECASE)
    balance_match = re.search(r"Updated Balance\s*:\s*(₹?\d+(?:\.\d+)?)", text, re.IGNORECASE)

    if received_match and txn_match:
        famid, source = extract_famid(text, utr_match.group(1) if utr_match else None)
        return {
            "sender": received_match.group(2).strip(),
            "amount": received_match.group(1).strip(),
            "txn_id": txn_match.group(1).strip(),
            "utr": utr_match.group(1).strip() if utr_match else "N/A",
            "balance": balance_match.group(1).strip() if balance_match else "N/A",
            "famid": famid,
            "famid_source": source
        }
    return None

def create_alert_message(payment_data, email_id):
    """Generates a clean, professional-looking Telegram alert"""
    return (
        f"🔔 *NEW FAMAPP DEPOSIT*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Sender:* {payment_data['sender']}\n"
        f"💵 *Amount:* `{payment_data['amount']}`\n"
        f"🆔 *TXN ID:* `{payment_data['txn_id']}`\n"
        f"🏦 *UTR:* `{payment_data['utr']}`\n"
        f"📊 *Balance:* `{payment_data['balance']}`\n"
        f"{payment_data['famid_source']} *FamID:* `{payment_data['famid']}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📧 Account: {email_id}\n"
        f"✅ Status: Verified & Saved"
    )

def check_emails():
    channels = ConfigDB.get_telegram_channels().get("payment_alerts", [])
    email_accounts = json.loads(os.getenv("EMAIL_ACCOUNTS", "[]"))
    
    for acc in email_accounts:
        email_id = acc.get("email")
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(email_id, acc.get("password"))
            mail.select("inbox")
            
            _, data = mail.search(None, '(FROM "no-reply@famapp.in" SUBJECT "You received")')
            
            for e_id in data[0].split()[-5:]:
                unique_key = f"{email_id}_{e_id.decode()}"
                if unique_key in processed_emails.get(email_id, set()): continue
                
                _, msg_data = mail.fetch(e_id, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/html":
                            body = part.get_payload(decode=True).decode()
                else:
                    body = msg.get_payload(decode=True).decode()
                
                payment_data = parse_famapp_email(body, email_id)
                
                if payment_data and not PaymentDB.check_duplicate(payment_data["txn_id"], payment_data["utr"], email_id):
                    if PaymentDB.save_payment(email_id, **payment_data):
                        PaymentDB.increment_payment_count(email_id)
                        send_to_telegram(create_alert_message(payment_data, email_id), channels)
                        print(f"✅ Processed: {payment_data['txn_id']}")
                
                processed_emails.setdefault(email_id, set()).add(unique_key)
            mail.logout()
        except Exception as e:
            print(f"❌ Error with {email_id}: {e}")

if __name__ == "__main__":
    print("🚀 Bot Started Successfully...")
    while True:
        check_emails()
        time.sleep(CHECK_INTERVAL)
