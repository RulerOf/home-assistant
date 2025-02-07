"""Mail (SMTP) notification service."""
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from imghdr import tests
import email.utils
import logging
import os
import smtplib

import voluptuous as vol

from homeassistant.const import (
    CONF_PASSWORD,
    CONF_PORT,
    CONF_RECIPIENT,
    CONF_SENDER,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util

from homeassistant.components.notify import (
    ATTR_DATA,
    ATTR_TITLE,
    ATTR_TITLE_DEFAULT,
    PLATFORM_SCHEMA,
    BaseNotificationService,
)

_LOGGER = logging.getLogger(__name__)

ATTR_IMAGES = "images"  # optional embedded image file attachments
ATTR_HTML = "html"

CONF_ENCRYPTION = "encryption"
CONF_DEBUG = "debug"
CONF_SERVER = "server"
CONF_SENDER_NAME = "sender_name"

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 587
DEFAULT_TIMEOUT = 5
DEFAULT_DEBUG = False
DEFAULT_ENCRYPTION = "starttls"

ENCRYPTION_OPTIONS = ["tls", "starttls", "none"]

# pylint: disable=no-value-for-parameter
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_RECIPIENT): vol.All(cv.ensure_list, [vol.Email()]),
        vol.Required(CONF_SENDER): vol.Email(),
        vol.Optional(CONF_SERVER, default=DEFAULT_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
        vol.Optional(CONF_ENCRYPTION, default=DEFAULT_ENCRYPTION): vol.In(
            ENCRYPTION_OPTIONS
        ),
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SENDER_NAME): cv.string,
        vol.Optional(CONF_DEBUG, default=DEFAULT_DEBUG): cv.boolean,
    }
)

# Monkeypatch bug in imagehdr, https://stackoverflow.com/a/57693121/7969681 for details


def test_jpeg1(h, f):
    """JPEG data in JFIF format."""
    if b"JFIF" in h[:23]:
        return "jpeg"


JPEG_MARK = (
    b"\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06"
    b"\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f"
)


def test_jpeg2(h, f):
    """JPEG with small header."""
    if len(h) >= 32 and 67 == h[5] and h[:32] == JPEG_MARK:
        return "jpeg"


def test_jpeg3(h, f):
    """JPEG data in JFIF or Exif format."""
    if h[6:10] in (b"JFIF", b"Exif") or h[:2] == b"\xff\xd8":
        return "jpeg"


tests.append(test_jpeg1)
tests.append(test_jpeg2)
tests.append(test_jpeg3)


def get_service(hass, config, discovery_info=None):
    """Get the mail notification service."""
    mail_service = MailNotificationService(
        config.get(CONF_SERVER),
        config.get(CONF_PORT),
        config.get(CONF_TIMEOUT),
        config.get(CONF_SENDER),
        config.get(CONF_ENCRYPTION),
        config.get(CONF_USERNAME),
        config.get(CONF_PASSWORD),
        config.get(CONF_RECIPIENT),
        config.get(CONF_SENDER_NAME),
        config.get(CONF_DEBUG),
    )

    if mail_service.connection_is_valid():
        return mail_service

    return None


class MailNotificationService(BaseNotificationService):
    """Implement the notification service for E-mail messages."""

    def __init__(
        self,
        server,
        port,
        timeout,
        sender,
        encryption,
        username,
        password,
        recipients,
        sender_name,
        debug,
    ):
        """Initialize the SMTP service."""
        self._server = server
        self._port = port
        self._timeout = timeout
        self._sender = sender
        self.encryption = encryption
        self.username = username
        self.password = password
        self.recipients = recipients
        self._sender_name = sender_name
        self.debug = debug
        self.tries = 2

    def connect(self):
        """Connect/authenticate to SMTP Server."""
        if self.encryption == "tls":
            mail = smtplib.SMTP_SSL(self._server, self._port, timeout=self._timeout)
        else:
            mail = smtplib.SMTP(self._server, self._port, timeout=self._timeout)
        mail.set_debuglevel(self.debug)
        mail.ehlo_or_helo_if_needed()
        if self.encryption == "starttls":
            mail.starttls()
            mail.ehlo()
        if self.username and self.password:
            mail.login(self.username, self.password)
        return mail

    def connection_is_valid(self):
        """Check for valid config, verify connectivity."""
        server = None
        try:
            server = self.connect()
        except (smtplib.socket.gaierror, ConnectionRefusedError):
            _LOGGER.exception(
                "SMTP server not found or refused connection (%s:%s). "
                "Please check the IP address, hostname, and availability of your SMTP server.",
                self._server,
                self._port,
            )

        except smtplib.SMTPAuthenticationError:
            _LOGGER.exception(
                "Login not possible. "
                "Please check your setting and/or your credentials"
            )
            return False

        finally:
            if server:
                server.quit()

        return True

    def send_message(self, message="", **kwargs):
        """
        Build and send a message to a user.

        Will send plain text normally, or will build a multipart HTML message
        with inline image attachments if images config is defined, or will
        build a multipart HTML if html config is defined.
        """
        subject = kwargs.get(ATTR_TITLE, ATTR_TITLE_DEFAULT)
        data = kwargs.get(ATTR_DATA)

        if data:
            if ATTR_HTML in data:
                msg = _build_html_msg(
                    message, data[ATTR_HTML], images=data.get(ATTR_IMAGES, [])
                )
            else:
                msg = _build_multipart_msg(message, images=data.get(ATTR_IMAGES, []))
        else:
            msg = _build_text_msg(message)

        msg["Subject"] = subject
        msg["To"] = ",".join(self.recipients)
        if self._sender_name:
            msg["From"] = f"{self._sender_name} <{self._sender}>"
        else:
            msg["From"] = self._sender
        msg["X-Mailer"] = "HomeAssistant"
        msg["Date"] = email.utils.format_datetime(dt_util.now())
        msg["Message-Id"] = email.utils.make_msgid()

        return self._send_email(msg)

    def _send_email(self, msg):
        """Send the message."""
        mail = self.connect()
        for _ in range(self.tries):
            try:
                mail.sendmail(self._sender, self.recipients, msg.as_string())
                break
            except smtplib.SMTPServerDisconnected:
                _LOGGER.warning(
                    "SMTPServerDisconnected sending mail: retrying connection"
                )
                mail.quit()
                mail = self.connect()
            except smtplib.SMTPException:
                _LOGGER.warning("SMTPException sending mail: retrying connection")
                mail.quit()
                mail = self.connect()
        mail.quit()


def _build_text_msg(message):
    """Build plaintext email."""
    _LOGGER.debug("Building plain text email")
    return MIMEText(message)


def _build_multipart_msg(message, images):
    """Build Multipart message with in-line images."""
    _LOGGER.debug("Building multipart email with embedded attachment(s)")
    msg = MIMEMultipart("related")
    msg_alt = MIMEMultipart("alternative")
    msg.attach(msg_alt)
    body_txt = MIMEText(message)
    msg_alt.attach(body_txt)
    body_text = [f"<p>{message}</p><br>"]

    for atch_num, atch_name in enumerate(images):
        cid = f"image{atch_num}"
        body_text.append(f'<img src="cid:{cid}"><br>')
        try:
            with open(atch_name, "rb") as attachment_file:
                file_bytes = attachment_file.read()
                try:
                    attachment = MIMEImage(file_bytes)
                    msg.attach(attachment)
                    attachment.add_header("Content-ID", f"<{cid}>")
                except TypeError:
                    _LOGGER.warning(
                        "Attachment %s has an unknown MIME type. "
                        "Falling back to file",
                        atch_name,
                    )
                    attachment = MIMEApplication(file_bytes, Name=atch_name)
                    attachment["Content-Disposition"] = (
                        "attachment; " 'filename="%s"' % atch_name
                    )
                    msg.attach(attachment)
        except FileNotFoundError:
            _LOGGER.warning("Attachment %s not found. Skipping", atch_name)

    body_html = MIMEText("".join(body_text), "html")
    msg_alt.attach(body_html)
    return msg


def _build_html_msg(text, html, images):
    """Build Multipart message with in-line images and rich HTML (UTF-8)."""
    _LOGGER.debug("Building HTML rich email")
    msg = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text, _charset="utf-8"))
    alternative.attach(MIMEText(html, ATTR_HTML, _charset="utf-8"))
    msg.attach(alternative)

    for atch_num, atch_name in enumerate(images):
        name = os.path.basename(atch_name)
        try:
            with open(atch_name, "rb") as attachment_file:
                attachment = MIMEImage(attachment_file.read(), filename=name)
            msg.attach(attachment)
            attachment.add_header("Content-ID", f"<{name}>")
        except FileNotFoundError:
            _LOGGER.warning(
                "Attachment %s [#%s] not found. Skipping", atch_name, atch_num
            )
    return msg
