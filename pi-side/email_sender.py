import os
import smtplib
import ssl
import re
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)

def _html_to_plaintext(html: str) -> str:
    """
    Converts HTML to plain text using standard library tools.
    1. Replaces common block tags with newlines for readability.
    2. Strips all remaining HTML tags using regex.
    3. Unescapes basic HTML entities (optional improvement).
    """
    # 1. Normalize line breaks
    # Replace breaks and paragraph ends with newlines
    text = html.replace("<br>", "\n").replace("<br/>", "\n").replace("</p>", "\n\n").replace("</div>", "\n")
    
    # 2. Strip all remaining tags (e.g. <b>, <ul>, <html>)
    # This regex matches any character between < and >
    text = re.sub(r'<[^>]+>', '', text)
    
    # 3. Collapse multiple empty lines into max two
    text = re.sub(r'\n\s*\n', '\n\n', text).strip()
    
    return text

def send_email_smtp(to: str, subject: str, body_html: str) -> None:
    """
    Sends an email using SMTP configuration from environment variables.
    
    Args:
        to: Recipient email address.
        subject: Email subject.
        body_html: HTML content of the email.
    
    Raises:
        ValueError: If required environment variables are missing.
        Exception: If SMTP connection or sending fails.
    """
    # --- 1. Load Configuration ---
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_from = os.getenv("SMTP_FROM")

    # Defaults
    smtp_starttls = os.getenv("SMTP_STARTTLS", "1") == "1"
    smtp_ssl = os.getenv("SMTP_SSL", "0") == "1"

    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")

    # Check if we are running with default placeholders (security check)
    if smtp_from == "your.senderadress@gmail.com":
        logger.error("SMTP configuration contains default placeholders. Please update env-exports.sh.")
        raise ValueError("SMTP configuration contains default placeholders.")

    if not all([smtp_host, smtp_port, smtp_from]):
        missing = []
        if not smtp_host: missing.append("SMTP_HOST")
        if not smtp_port: missing.append("SMTP_PORT")
        if not smtp_from: missing.append("SMTP_FROM")
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    try:
        port = int(smtp_port)
    except ValueError:
        raise ValueError(f"Invalid SMTP_PORT: {smtp_port}")

    # --- 2. Create Message (MIME) ---
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to

    # Generate cleaner fallback text
    text_content = _html_to_plaintext(body_html)
    text_fallback = f"This email requires an HTML-compatible client.\n\n---\n\n{text_content}"

    part1 = MIMEText(text_fallback, "plain", "utf-8")
    part2 = MIMEText(body_html, "html", "utf-8")

    msg.attach(part1)
    msg.attach(part2)

    # --- 3. Connect and Send (Resource Safe) ---
    server = None
    try:
        # Create SSL Context
        context = ssl.create_default_context()
        
        # Connect
        if smtp_ssl:
            server = smtplib.SMTP_SSL(smtp_host, port, context=context)
        else:
            server = smtplib.SMTP(smtp_host, port)
            # Upgrade to TLS if requested
            if smtp_starttls:
                server.starttls(context=context)

        # Login
        if smtp_username and smtp_password:
            server.login(smtp_username, smtp_password)

        # Send
        server.send_message(msg)
        logger.info(f"Email sent successfully to {to}")

    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        raise e
        
    finally:
        # Guarantee cleanup even on exception
        if server:
            try:
                server.quit()
            except Exception:
                # If the connection is already broken (e.g. timeout), quit() might fail too.
                # We ignore this secondary error to raise the primary one.
                pass

