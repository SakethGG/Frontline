"""Out-of-browser notifications: native Windows toasts (desktop app), ntfy.sh push and email digests."""

import asyncio
import html
import logging
import os
import smtplib
import subprocess
from email.message import EmailMessage
from xml.sax.saxutils import escape

import httpx

from . import config

log = logging.getLogger("frontline.notify")

# Shows a toast through the WinRT notification API under Frontline's app identity
# (registered in HKCU by frontline.desktop). Payload comes in via env vars to avoid quoting issues.
_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:FRONTLINE_TOAST_XML)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:FRONTLINE_APP_ID).Show($toast)
"""


def desktop_toast(title: str, lines: list[str]) -> None:
    texts = "".join(f"<text>{escape(t)}</text>" for t in [title, *lines][:3])
    xml = f'<toast><visual><binding template="ToastGeneric">{texts}</binding></visual></toast>'
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _TOAST_PS],
        env={**os.environ, "FRONTLINE_TOAST_XML": xml, "FRONTLINE_APP_ID": config.APP_ID},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        capture_output=True, timeout=30, check=True,
    )


def channels() -> list[str]:
    out = ["desktop"] if config.DESKTOP else []
    if config.NTFY_TOPIC:
        out.append("ntfy")
    if config.SMTP_HOST and config.DIGEST_TO:
        out.append("email")
    return out


async def send_ntfy(title: str, message: str, click: str) -> None:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{config.NTFY_SERVER}/{config.NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers={
                # HTTP headers must be latin-1, so strip anything exotic from the title
                "Title": title.encode("ascii", "ignore").decode(),
                "Click": click,
                "Tags": "page_facing_up",
            },
        )
        r.raise_for_status()


def _send_email(subject: str, text: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER or config.DIGEST_TO
    msg["To"] = config.DIGEST_TO
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as s:
        s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)


def _email_card(p: dict) -> str:
    s = p["summary"]
    points = "".join(f"<li>{html.escape(k)}</li>" for k in s.get("key_points", []))
    return (
        "<div style='margin:0 0 16px;padding:12px 14px;border:1px solid #ddd;border-radius:10px'>"
        f"<a href='{html.escape(p['url'])}' style='font-weight:600;font-size:15px'>"
        f"{html.escape(p['title'])}</a>"
        f"<div style='color:#666;font-size:12px;margin:4px 0'>Relevance {p['relevance']}/10 · "
        f"{html.escape(', '.join(s.get('subtopics', [])))}</div>"
        f"<div>{html.escape(s.get('tldr', ''))}</div><ul>{points}</ul></div>"
    )


async def notify_sweep(sweep_id: int, new_papers: list[dict], digest: str) -> list[str]:
    """Send the sweep result through every configured channel; returns channels that succeeded."""
    if not new_papers:
        return []
    ranked = sorted(new_papers, key=lambda p: p["relevance"], reverse=True)
    link = f"{config.PUBLIC_URL}/?sweep={sweep_id}"
    title = f"Frontline: {len(new_papers)} new paper{'s' if len(new_papers) != 1 else ''}"
    bullets = "\n".join(
        f"- ({p['relevance']}/10) {p['title']}: {p['summary'].get('tldr', '')}" for p in ranked[:5]
    )
    sent = []
    if config.DESKTOP:
        try:
            top = ranked[0]["title"]
            await asyncio.to_thread(desktop_toast, title, [f"Top pick: {top}"])
            sent.append("desktop")
        except Exception as e:
            log.warning("desktop toast failed: %s", e)
    if config.NTFY_TOPIC:
        try:
            await send_ntfy(title, f"Top picks:\n{bullets}"[:3500], link)
            sent.append("ntfy")
        except Exception as e:
            log.warning("ntfy failed: %s", e)
    if config.SMTP_HOST and config.DIGEST_TO:
        body = (
            "<div style='font-family:system-ui,sans-serif;max-width:680px'>"
            f"<h2>{html.escape(title)}</h2>"
            f"<pre style='white-space:pre-wrap;font-family:inherit'>{html.escape(digest)}</pre>"
            + "".join(_email_card(p) for p in ranked[:20])
            + f"<p><a href='{link}'>Open in Frontline</a></p></div>"
        )
        try:
            await asyncio.to_thread(_send_email, title, f"{digest}\n\n{bullets}\n\n{link}", body)
            sent.append("email")
        except Exception as e:
            log.warning("email failed: %s", e)
    return sent
