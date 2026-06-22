import imaplib
import email
import re
import requests
import time
from bs4 import BeautifulSoup
import os

from firebase_config import PaymentDB, PayoutDB, ConfigDB
from config import CHECK_INTERVAL, TELEGRAM_BOT_TOKEN

processed_emails = {}


def extract_famid(text, utr):
    """Extract FamID from URL or UTR"""
    famid = None
    
    # Try to extract from URL pattern: fam.com?id=XXXXX or similar
    url_match = re.search(r'fam[app]*\..*?[?&]id=([A-Za-z0-9]+)', text, re.IGNORECASE)
    if url_match:
        famid = url_match.group(1).strip()
        return famid, "url"
    
    # Try to extract from UTR (sometimes FamID is embedded)
    if utr and utr != "N/A":
        # Check if UTR contains FamID pattern
        utr_match = re.search(r'([A-Z0-9]{6,15})', utr)
        if utr_match:
            potential_famid = utr_match.group(1)
            # Verify it looks like FamID
            if len(potential_famid) >= 6:
                return potential_famid, "utr"
    
    return "NONE", "none"


def send_to_telegram(message, target_chats):
    """Send message to Telegram channels"""
    for chat_id in target_chats:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                print(f"Telegram API Error for ID {chat_id}: {response.text}")
        except Exception as e:
            print(f"Error sending to Telegram ID {chat_id}: {e}")


def parse_famapp_email(html_body, account_email):
    """Parse FamApp deposit email and extract details"""
    soup = BeautifulSoup(html_body, "html.parser")
    text = soup.get_text(separator="\n")

    # Extract amount and sender
    received_match = re.search(
        r"You have successfully received\s+(₹?\d+(?:\.\d+)?)\s+from\s+([^\n]+)",
        text,
        re.IGNORECASE,
    )
    
    # Extract Transaction ID
    txn_match = re.search(r"Transaction ID\s*:\s*([A-Za-z0-9]+)", text, re.IGNORECASE)
    
    # Extract UTR
    utr_match = re.search(r"UTR\s*:\s*(\d+)", text, re.IGNORECASE)
    
    # Extract Balance
    balance_match = re.search(
        r"Updated Balance\s*:\s*(₹?\d+(?:\.\d+)?)", text, re.IGNORECASE
    )

    if received_match and txn_match:
        amount = received_match.group(1).strip()
        sender = received_match.group(2).strip()
        txn_id = txn_match.group(1).strip()
        utr = utr_match.group(1).strip() if utr_match else "N/A"
        balance = balance_match.group(1).strip() if balance_match else "N/A"

        # Extract FamID from URL or UTR
        famid, source = extract_famid(text, utr)

        return {
            "sender": sender,
            "amount": amount,
            "txn_id": txn_id,
            "utr": utr,
            "balance": balance,
            "famid": famid,
            "famid_source": source
        }

    return None


def verify_and_save_payment(email_id, payment_data):
    """Verify payment and save to Firebase, preventing duplicates"""
    
    txn_id = payment_data["txn_id"]
    utr = payment_data["utr"]
    famid = payment_data["famid"]
    
    # Check for duplicates
    is_duplicate = PaymentDB.check_duplicate(txn_id, utr, email_id)
    
    if is_duplicate:
        print(f"⚠️ Duplicate payment detected: {txn_id}")
        return False, "duplicate"
    
    # Save to Firebase
    payment_key = PaymentDB.save_payment(
        email=email_id,
        sender=payment_data["sender"],
        amount=payment_data["amount"],
        txn_id=txn_id,
        utr=utr,
        balance=payment_data["balance"],
        famid=famid
    )
    
    if payment_key:
        # Increment stats
        PaymentDB.increment_payment_count(email_id)
        return True, payment_key
    else:
        return False, "database_error"


def create_alert_message(payment_data, email_id, famid_source):
    """Create formatted Telegram alert message"""
    
    famid_info = payment_data["famid"]
    source_emoji = "🔗" if famid_source == "url" else "🔐" if famid_source == "utr" else "❌"
    
    msg = (
        f"💰 *NEW FAMAPP DEPOSIT - VERIFIED* 💰\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📩 *Email:* `{email_id}`\n"
        f"👤 *Sender:* {payment_data['sender']}\n"
        f"💵 *Amount:* {payment_data['amount']}\n"
        f"🆔 *TXN ID:* `{payment_data['txn_id']}`\n"
        f"🏦 *UTR:* `{payment_data['utr']}`\n"
        f"📊 *Balance:* {payment_data['balance']}\n\n"
        f"{source_emoji} *FamID:* `{famid_info}`\n"
        f"(Source: {famid_source})\n\n"
        f"✅ *Status:* VERIFIED & SAVED\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    return msg


def check_emails():
    """Check Gmail inbox for FamApp deposit emails"""
    
    # Get channels from Firebase config
    channels = ConfigDB.get_telegram_channels()
    payment_channels = channels.get("payment_alerts", [])
    
    if not payment_channels:
        print("⚠️ No payment alert channels configured in Firebase")
        return
    
    # Get email accounts from environment
    email_accounts = os.getenv("EMAIL_ACCOUNTS")
    
    if not email_accounts:
        print("❌ EMAIL_ACCOUNTS not configured")
        return
    
    import json
    try:
        accounts = json.loads(email_accounts)
    except:
        print("❌ Invalid EMAIL_ACCOUNTS format")
        return
    
    for acc in accounts:
        email_id = acc.get("email")
        password = acc.get("password")
        
        if not email_id or not password:
            continue
        
        if email_id not in processed_emails:
            processed_emails[email_id] = set()
        
        try:
            # Connect to Gmail IMAP
            mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
            mail.login(email_id, password)
            mail.select("inbox")
            
            # Search for FamApp emails
            status, data = mail.search(
                None, '(FROM "no-reply@famapp.in" SUBJECT "You received")'
            )
            
            if status == "OK":
                email_ids = data[0].split()
                
                # Process last 5 emails
                for e_id in email_ids[-5:]:
                    unique_key = f"{email_id}_{e_id.decode()}"
                    
                    # Skip if already processed
                    if unique_key in processed_emails[email_id]:
                        continue
                    
                    try:
                        status, msg_data = mail.fetch(e_id, "(RFC822)")
                        
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                
                                # Extract HTML body
                                html_body = ""
                                if msg.is_multipart():
                                    for part in msg.walk():
                                        if part.get_content_type() == "text/html":
                                            html_body = part.get_payload(decode=True).decode()
                                            break
                                else:
                                    html_body = msg.get_payload(decode=True).decode()
                                
                                # Parse payment details
                                payment_data = parse_famapp_email(html_body, email_id)
                                
                                if payment_data:
                                    # Verify and save
                                    is_saved, result = verify_and_save_payment(email_id, payment_data)
                                    
                                    if is_saved:
                                        # Create alert
                                        alert_msg = create_alert_message(
                                            payment_data, 
                                            email_id, 
                                            payment_data["famid_source"]
                                        )
                                        
                                        # Send to all channels
                                        send_to_telegram(alert_msg, payment_channels)
                                        
                                        print(f"✅ Payment verified and saved: {payment_data['txn_id']}")
                                    else:
                                        if result == "duplicate":
                                            print(f"⏭️ Skipped duplicate: {payment_data['txn_id']}")
                                    
                                    # Mark as processed
                                    processed_emails[email_id].add(unique_key)
                    
                    except Exception as e:
                        print(f"Error processing email {e_id}: {e}")
            
            mail.logout()
            
        except Exception as e:
            print(f"❌ Error checking email {email_id}: {e}")


def run_loop():
    """Main background loop"""
    print("🤖 FamApp Payment Verifier Bot Started")
    print("📊 Using Firebase Database")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    while True:
        try:
            check_emails()
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_loop()
        
