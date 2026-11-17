"""
IMAP Gmail reader to fetch OTPs.

Confirmed real email format (20-10-26):
    <strong>4011716</strong> is your one time password (OTP).
    For account safety, do not share your OTP with others.

Extraction strategy (in order):
  1. <strong>/<b>/<em> tag containing a 4-8 digit number  (highest confidence)
  2. Digits followed by "is your one time password / OTP / code"
  3. Digits immediately followed within 40 chars by OTP keyword
  4. Older keyword-first patterns (kept for backward-compat with other emails)
  5. Bidirectional proximity heuristic

Other fixes:
  * Uses BODY.PEEK[] so fetching does NOT auto-mark messages Seen
    (only the successfully-extracted OTP email is explicitly marked Seen)
  * SINCE filter limits search to last 2 days (avoids parsing 400+ history)
  * HTML entities are unescaped before matching
  * Optional OTP_INCLUDE_SEEN=1 to allow Seen emails (for testing)
  * Optional DUMP_OTP_EMAIL=1 to dump the full email body on success
"""
import os
import imaplib
import email
import re
import html as html_mod
import asyncio
from datetime import datetime, timedelta, timezone, date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional, Tuple


def _strip_html(s: str) -> str:
    return re.sub(r'<[^>]+>', ' ', s)


def _extract_otp_from_text(text: str) -> Tuple[Optional[str], str]:
    if not text:
        return None, "empty-body"

    # 1. Highest-confidence: strong/b/em tag wrapping digits
    m = re.search(r'<(?:strong|b|em)[^>]*>\s*(\d{4,8})\s*</(?:strong|b|em)>',
                  text, re.IGNORECASE)
    if m:
        return m.group(1), "strong-tag"

    # 2. Clean body: strip tags, unescape entities
    plain = html_mod.unescape(_strip_html(text))

    patterns = [
        # Digits FIRST, then "is your one time password / OTP"
        (r'(\d{4,8})\s+(?:is|was)\s+(?:your|the)\s+'
         r'(?:one[\s-]*time\s+)?(?:password|pin|code|otp)', "digits-then-password"),
        # Digits followed within 40 non-digit chars by OTP keyword
        (r'\b(\d{4,8})\b[^0-9]{0,40}'
         r'(?:OTP|one[\s-]*time[\s-]*password|verification[\s-]*code)', "digits-then-otp"),
        # Keyword FIRST then digits (classic format)
        (r'(?:OTP|one[\s-]*time[\s-]*password)\s*(?:is|:)?\s*[:\-]?\s*(\d{4,8})',
         "explicit-otp"),
        (r'(?:verification|security)\s*(?:code|pin)\s*(?:is|:)?\s*[:\-]?\s*(\d{4,8})',
         "explicit-verification"),
        (r'(?:your|the)\s+(?:one[\s-]*time\s+)?(?:password|pin|code)\s+(?:is|:)?\s*[:\-]?\s*(\d{4,8})',
         "explicit-password"),
        (r'(?:code|pin|password)\s*[:\-]\s*(\d{4,8})', "generic-keyword-colon"),
    ]
    for pat, label in patterns:
        m = re.search(pat, plain, re.IGNORECASE)
        if m:
            return m.group(1), label

    # 5. Bidirectional proximity heuristic
    kw_re = re.compile(r'(?:OTP|password|pin|code)', re.IGNORECASE)
    for kw in kw_re.finditer(plain):
        forward = plain[kw.end():kw.end() + 200]
        m = re.search(r'\b(\d{4,8})\b', forward)
        if m:
            return m.group(1), "proximity-after-" + kw.group(0).lower()
        backward = plain[max(0, kw.start() - 200):kw.start()]
        ms = list(re.finditer(r'\b(\d{4,8})\b', backward))
        if ms:
            return ms[-1].group(1), "proximity-before-" + kw.group(0).lower()

    return None, "no-match"


def _fetch_latest_otp_email(user: str, password: str,
                            max_age_minutes: int = 5,
                            include_seen: bool = False,
                            dump_path: Optional[Path] = None) -> Optional[str]:
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        mail.login(user, password)
        mail.select("inbox")

        since = (date.today() - timedelta(days=2)).strftime("%d-%b-%Y")
        if include_seen:
            criterion = f'(FROM "no-reply@pumis.in" SINCE {since})'
        else:
            criterion = f'(UNSEEN FROM "no-reply@pumis.in" SINCE {since})'

        status, messages = mail.search(None, criterion)
        if status != "OK" or not messages[0]:
            return None

        msg_nums = messages[0].split()
        mode = "recent (incl. seen)" if include_seen else "unread"
        print(f"  Found {len(msg_nums)} {mode} email(s) from no-reply@pumis.in (since {since})")

        for num in reversed(msg_nums):
            # BODY.PEEK[] does NOT auto-mark as \Seen
            res, msg_data = mail.fetch(num, "(BODY.PEEK[])")
            body = None
            internal_date = None
            subject = ""

            for part in msg_data:
                if isinstance(part, tuple):
                    msg = email.message_from_bytes(part[1])
                    subject = msg.get("Subject", "") or ""
                    d = msg.get("Date")
                    if d:
                        try:
                            internal_date = parsedate_to_datetime(d)
                        except Exception:
                            pass
                    if msg.is_multipart():
                        for sub in msg.walk():
                            if sub.get_content_type() in ("text/plain", "text/html"):
                                payload = sub.get_payload(decode=True)
                                if payload:
                                    body = (body or "") + "\n" + payload.decode(errors='ignore')
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body = payload.decode(errors='ignore')

            if not body:
                continue

            if internal_date is not None:
                if internal_date.tzinfo is None:
                    internal_date = internal_date.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - internal_date
                if age > timedelta(minutes=max_age_minutes):
                    print(f"  Skip {num.decode()!r} ({subject!r}): too old ({age})")
                    continue

            otp, how = _extract_otp_from_text(body)
            print(f"  Email {num.decode()!r} ({subject!r}): otp={otp!r} via={how}")

            if otp:
                if dump_path is not None:
                    try:
                        dump_path.write_text(
                            f"Subject: {subject}\nDate: {internal_date}\n"
                            f"Extracted OTP: {otp} (via {how})\n\n{body}",
                            encoding="utf-8",
                        )
                        print(f"  Dumped email to {dump_path}")
                    except Exception as e:
                        print(f"  Warning: dump failed: {e}")
                # Explicitly mark ONLY the matched OTP email as seen
                try:
                    mail.store(num, '+FLAGS', '\\Seen')
                except Exception as e:
                    print(f"  Warning: mark-as-seen failed: {e}")
                return otp

        return None
    finally:
        try: mail.close()
        except Exception: pass
        try: mail.logout()
        except Exception: pass


async def get_otp() -> str:
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")

    if not user or not password:
        raise ValueError("EMAIL_USER or EMAIL_PASS not set in .env file.")

    print('Connecting to Gmail to fetch OTP...')

    dump_path = Path("otp_email_dump.txt") if os.getenv("DUMP_OTP_EMAIL") == "1" else None
    include_seen = os.getenv("OTP_INCLUDE_SEEN") == "1"

    for attempt in range(15):
        try:
            otp = _fetch_latest_otp_email(
                user, password,
                include_seen=include_seen,
                dump_path=dump_path,
            )
            if otp:
                return otp
        except Exception as e:
            print(f"IMAP Error on attempt {attempt+1}: {e}")
        await asyncio.sleep(4)

    raise TimeoutError("Failed to retrieve OTP via IMAP within the timeout.")
