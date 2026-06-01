# -*- coding: utf-8 -*-
"""
PHANTRON messaging - send WhatsApp / Telegram messages and read/send email.

Each channel is independent and best-effort. Nothing here is imported at
package load time, and every method degrades with a clear message when a
channel isn't configured or a library is missing - PHANTRON never crashes.

Channels:
  * WhatsApp  - opens WhatsApp Web with the message pre-filled and sends it.
                Uses pywhatkit if installed (precise), else a browser URL.
  * Telegram  - via a Telegram Bot. Put bot_token + chat_id in config; uses
                only urllib (no extra package).
  * Email     - SMTP send + IMAP read using Python's stdlib (smtplib/imaplib).
                For Gmail use an App Password (not your normal password).

Security note: credentials live in config.json (or env vars). Treat that file
like a password file. PHANTRON only ever uses them to act on P7's behalf.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
import webbrowser
from typing import Callable, Optional


class Messaging:
    def __init__(self, cfg, on_event: Optional[Callable[[str, str, str], None]] = None):
        self.cfg = cfg
        self.on_event = on_event
        self.name = cfg.get("user_name", "P7")

    def _log(self, action: str, detail: str = "", kind: str = "message"):
        if self.on_event:
            try:
                self.on_event(action, detail, kind)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    #  WhatsApp
    # ─────────────────────────────────────────────────────────────────────
    def whatsapp(self, to: str = "", message: str = "") -> str:
        """
        Send a WhatsApp message. `to` may be a phone number with country code
        (e.g. +9198...). If pywhatkit is installed it sends instantly; else we
        open WhatsApp Web / the wa.me link with the text pre-filled.
        """
        num = re.sub(r"[^\d+]", "", to or "")
        msg = (message or "").strip()
        if not num:
            return "Kis number pe bhejun? (country code ke saath, jaise +9198...)"
        if not msg:
            return "Kya message bhejun?"
        self._log("WhatsApp", num)

        # Precise instant send via pywhatkit (if present)
        try:
            import pywhatkit  # type: ignore
            pywhatkit.sendwhatmsg_instantly(num, msg, wait_time=12, tab_close=True)
            return "WhatsApp message bhej diya %s ko." % num
        except Exception:
            pass

        # Fallback: open wa.me with the message pre-filled (user taps send)
        try:
            url = "https://wa.me/%s?text=%s" % (num.lstrip("+"), urllib.parse.quote(msg))
            webbrowser.open(url)
            return ("WhatsApp khol diya message ke saath, %s - bas Send daba do. "
                    "(Instant send chahiye to: pip install pywhatkit)" % self.name)
        except Exception as exc:
            return "WhatsApp error: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Telegram (bot)
    # ─────────────────────────────────────────────────────────────────────
    def telegram(self, message: str = "", chat_id: str = "") -> str:
        token = self.cfg.get("messaging.telegram.bot_token", "")
        cid = chat_id or self.cfg.get("messaging.telegram.chat_id", "")
        if not token:
            return ("Telegram bot setup nahi hai. @BotFather se token le ke "
                    "config messaging.telegram.bot_token + chat_id me daal do.")
        if not cid:
            return "Telegram chat_id missing hai config me."
        if not message:
            return "Kya message bhejun?"
        self._log("Telegram", cid)
        try:
            url = "https://api.telegram.org/bot%s/sendMessage" % token
            data = urllib.parse.urlencode({"chat_id": cid, "text": message}).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=12) as resp:
                out = json.loads(resp.read().decode("utf-8", "replace"))
            if out.get("ok"):
                return "Telegram message bhej diya, %s." % self.name
            return "Telegram ne reject kiya: " + str(out.get("description", ""))
        except Exception as exc:
            return "Telegram error: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Email - send (SMTP)
    # ─────────────────────────────────────────────────────────────────────
    def send_email(self, to: str = "", subject: str = "", body: str = "") -> str:
        addr = (self.cfg.get("messaging.email.address", "") or "").strip()
        pwd = self.cfg.get("messaging.email.app_password", "")
        host = self.cfg.get("messaging.email.smtp_host", "smtp.gmail.com")
        port = int(self.cfg.get("messaging.email.smtp_port", 587))
        if not addr or not pwd:
            return ("Email setup nahi hai. config messaging.email me address + "
                    "app_password daalo (Gmail ke liye App Password use karo).")
        if not to:
            return "Kisko email bhejun?"
        self._log("Email send", to)
        try:
            import smtplib
            from email.mime.text import MIMEText

            mime = MIMEText(body or "", "plain", "utf-8")
            mime["Subject"] = subject or "(no subject)"
            mime["From"] = addr
            mime["To"] = to

            with smtplib.SMTP(host, port, timeout=20) as server:
                server.starttls()
                server.login(addr, pwd)
                server.sendmail(addr, [a.strip() for a in to.split(",")], mime.as_string())
            return "Email bhej diya %s ko." % to
        except Exception as exc:
            return "Email send fail: " + str(exc)

    # ─────────────────────────────────────────────────────────────────────
    #  Email - read latest (IMAP)
    # ─────────────────────────────────────────────────────────────────────
    def read_email(self, count: int = 5) -> str:
        addr = (self.cfg.get("messaging.email.address", "") or "").strip()
        pwd = self.cfg.get("messaging.email.app_password", "")
        host = self.cfg.get("messaging.email.imap_host", "imap.gmail.com")
        if not addr or not pwd:
            return ("Email setup nahi hai. config messaging.email me address + "
                    "app_password daalo.")
        self._log("Email read", "%d latest" % count)
        try:
            import imaplib
            import email
            from email.header import decode_header

            def _decode(val):
                if not val:
                    return ""
                parts = decode_header(val)
                out = ""
                for txt, enc in parts:
                    if isinstance(txt, bytes):
                        out += txt.decode(enc or "utf-8", "replace")
                    else:
                        out += txt
                return out

            box = imaplib.IMAP4_SSL(host)
            box.login(addr, pwd)
            box.select("INBOX")
            _, data = box.search(None, "ALL")
            ids = data[0].split()
            latest = ids[-int(count):][::-1] if ids else []
            lines = []
            for i in latest:
                _, msg_data = box.fetch(i, "(RFC822.HEADER)")
                msg = email.message_from_bytes(msg_data[0][1])
                frm = _decode(msg.get("From"))
                subj = _decode(msg.get("Subject"))
                lines.append("- %s | %s" % (frm[:40], subj[:60]))
            box.logout()
            if not lines:
                return "Inbox khaali lag raha hai."
            return "Latest %d emails:\n" % len(lines) + "\n".join(lines)
        except Exception as exc:
            return "Email read fail: " + str(exc)

    def status(self) -> dict:
        return {
            "telegram": bool(self.cfg.get("messaging.telegram.bot_token")),
            "email": bool(self.cfg.get("messaging.email.address")
                          and self.cfg.get("messaging.email.app_password")),
        }
