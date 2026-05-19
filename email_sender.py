import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_gmail(gmail_address, app_password, to_email, subject, body, sender_name=""):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{sender_name} <{gmail_address}>" if sender_name else gmail_address
    msg["To"]      = to_email
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, app_password)
        server.sendmail(gmail_address, to_email, msg.as_string())
