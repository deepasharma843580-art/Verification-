import imaplib
import email
import re
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime

from config import TELEGRAM_BOT_TOKEN, EMAIL_ACCOUNTS, CHECK_INTERVAL

processed_emails = {}


def send_to_telegram(message, target_chats):
    for chat_id in target_chats:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }
        try:
            response = requests.post(url, json=payload)
            if response.status_code != 200:
                print(f"[ERROR] Telegram → ID {chat_id}: {response.text}")
        except Exception as e:
            print(f"[ERROR] Telegram send failed for {chat_id}: {e}")


def parse_famapp_email(html_body, account_email):
    soup = BeautifulSoup(html_body, "html.parser")
    text = soup.get_text(separator="\n")

    received_match = re.search(
        r"You have successfully received\s+(₹?\d+(?:\.\d+)?)\s+from\s+([^\n]+)",
        text,
        re.IGNORECASE,
    )
    txn_match = re.search(r"Transaction ID\s*:\s*([A-Za-z0-9]+)", text, re.IGNORECASE)
    utr_match = re.search(r"UTR\s*:\s*(\d+)", text, re.IGNORECASE)
    balance_match = re.search(
        r"Updated Balance\s*:\s*(₹?\d+(?:\.\d+)?)", text, re.IGNORECASE
    )

    if not (received_match and txn_match):
        return None

    amount  = received_match.group(1).strip().lstrip("₹")
    sender  = received_match.group(2).strip()
    txn_id  = txn_match.group(1).strip()
    utr     = utr_match.group(1).strip() if utr_match else "N/A"
    balance = balance_match.group(1).strip() if balance_match else "N/A"
    now     = datetime.now().strftime("%d %b %Y  •  %I:%M %p")

    msg = (
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"       💸  *PAYMENT RECEIVED*\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
        f"\n"
        f"🏦  *Platform*  ›  FamApp\n"
        f"🕐  *Time*       ›  {now}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"         💰  *TRANSACTION*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"👤  *From*      ›  {sender}\n"
        f"💵  *Amount*   ›  `₹{amount}`\n"
        f"📊  *Balance*  ›  `{balance}`\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"         🔐  *REFERENCE IDs*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"🔖  *TXN*  ›  `{txn_id}`\n"
        f"📋  *UTR*   ›  `{utr}`\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"         📬  *CREDITED TO*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📧  `{account_email}`\n"
        f"\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"✅  *Status*  ›  SETTLED & VERIFIED\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
    )
    return msg


def check_emails():
    for acc in EMAIL_ACCOUNTS:
        email_id     = acc["email"]
        password     = acc["password"]
        target_chats = acc["telegram_chats"]

        processed_emails.setdefault(email_id, set())

        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(email_id, password)
            mail.select("inbox")

            status, data = mail.search(
                None, '(FROM "no-reply@famapp.in" SUBJECT "You received")'
            )

            if status == "OK":
                email_ids = data[0].split()

                # Check last 5 emails only
                for e_id in email_ids[-5:]:
                    unique_key = f"{email_id}_{e_id.decode()}"

                    if unique_key in processed_emails[email_id]:
                        continue

                    status, msg_data = mail.fetch(e_id, "(RFC822)")
                    for response_part in msg_data:
                        if not isinstance(response_part, tuple):
                            continue

                        msg = email.message_from_bytes(response_part[1])

                        html_body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/html":
                                    html_body = part.get_payload(decode=True).decode()
                                    break
                        else:
                            html_body = msg.get_payload(decode=True).decode()

                        tg_message = parse_famapp_email(html_body, email_id)
                        if tg_message:
                            send_to_telegram(tg_message, target_chats)
                            processed_emails[email_id].add(unique_key)
                            print(f"[✓] Alert sent → {email_id}")

            mail.logout()

        except Exception as e:
            print(f"[ERROR] Checking {email_id}: {e}")


def run_loop():
    print("━" * 40)
    print("  🤖  FamApp Deposit Verifier — LIVE")
    print(f"  ⏱   Interval: {CHECK_INTERVAL}s")
    print("━" * 40)

    while True:
        check_emails()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_loop()
    
