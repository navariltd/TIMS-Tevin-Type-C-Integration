"""Shared helpers for the TIMS Tevic Type-C integration.
"""

from __future__ import annotations

import re
from base64 import b64encode
from datetime import time, timedelta
from io import BytesIO

import frappe
from frappe import _

ERROR_LOG_TITLE = "TIMS Tevic Type-C Integration"

KRA_PIN_PATTERN = re.compile(r"^[A-Za-z]\d{9}[A-Za-z]$")
HTML_TAG_PATTERN = re.compile(r"<[^>]*>")
WHITESPACE_PATTERN = re.compile(r"\s+")

#: Length of a KRA Control Unit invoice number, used to sanity check credit note references.
CU_INVOICE_NUMBER_LENGTH = 19


def is_valid_kra_pin(pin: str | None) -> bool:
	"""Check that a string looks like a KRA PIN.

	This only validates the shape of the PIN; it does not check that the PIN is
	registered with KRA.
	"""
	if not pin:
		return False

	return bool(KRA_PIN_PATTERN.match(pin.strip()))


def strip_html_tags(text: str | None) -> str:
	"""Flatten an ERPNext rich-text field into a single line of plain text.

	Item descriptions are editor fields, and the TIMS device rejects markup in
	``HSDesc``.
	"""
	if not text:
		return ""

	return WHITESPACE_PATTERN.sub(" ", HTML_TAG_PATTERN.sub(" ", text)).strip()


def format_posting_time(posting_time: str | timedelta | time | None) -> str:
	"""Normalise a posting time to a zero padded ``HH:MM:SS`` string."""
	if posting_time is None:
		return "00:00:00"

	if isinstance(posting_time, timedelta):
		total_seconds = int(posting_time.total_seconds())
		hour, remainder = divmod(total_seconds, 3600)
		minute, second = divmod(remainder, 60)
		return f"{hour:02d}:{minute:02d}:{second:02d}"

	if isinstance(posting_time, time):
		return f"{posting_time.hour:02d}:{posting_time.minute:02d}:{posting_time.second:02d}"

	parts = str(posting_time).split(".", 1)[0].split(":")
	# Pad out partial values such as "9:30" so the device always receives HH:MM:SS.
	parts += ["0"] * (3 - len(parts))
	hour, minute, second = (int(part or 0) for part in parts[:3])

	return f"{hour:02d}:{minute:02d}:{second:02d}"


def get_qr_code_data_uri(data: str) -> str:
	"""Render ``data`` as a PNG QR code embedded in a data URI.

	The URI is stored on the Sales Invoice so print formats can render the code
	without an extra File record.
	"""
	import qrcode

	buffered = BytesIO()
	qrcode.make(data).save(buffered, format="PNG")
	encoded = b64encode(buffered.getvalue()).decode("utf-8")

	return f"data:image/png;base64,{encoded}"


def log_error(title: str, message: str | None = None) -> str:
	"""Write an Error Log entry and return its name."""
	return frappe.log_error(
		title=f"{ERROR_LOG_TITLE}: {title}", message=message or frappe.get_traceback()
	).name


def log_and_throw(title: str, message: str | None = None) -> None:
	"""Record the failure for support, then surface it to the caller."""
	log_error(title, message)
	frappe.throw(_(title))
