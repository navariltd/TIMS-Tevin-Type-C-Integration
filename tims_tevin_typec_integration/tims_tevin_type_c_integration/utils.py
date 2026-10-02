from __future__ import annotations

import re
from base64 import b64encode
from datetime import time, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from io import BytesIO

import frappe

ERROR_LOG_TITLE = "TIMS Tevin Type-C Integration"

KRA_PIN_PATTERN = re.compile(r"^[A-Za-z]\d{9}[A-Za-z]$")
HTML_TAG_PATTERN = re.compile(r"<[^>]*>")
WHITESPACE_PATTERN = re.compile(r"\s+")
NON_ALPHANUMERIC_PATTERN = re.compile(r"[^A-Za-z0-9]+")
NON_DESCRIPTION_PATTERN = re.compile(r"[^A-Za-z0-9 ]")

CU_INVOICE_NUMBER_LENGTH = 19

TRADER_INVOICE_NUMBER_MAX_LENGTH = 15

HS_DESCRIPTION_MAX_LENGTH = 20


def kra_round(value: float | str | None) -> float:
	truncated = Decimal(str(value or 0)).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
	return float(truncated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_trader_invoice_number(name: str) -> str:
	segments = [segment for segment in NON_ALPHANUMERIC_PATTERN.split(name) if segment]

	while len(segments) > 1 and len("".join(segments)) > TRADER_INVOICE_NUMBER_MAX_LENGTH:
		segments.pop(0)

	return "".join(segments)[-TRADER_INVOICE_NUMBER_MAX_LENGTH:]


def get_hs_description(*candidates: str | None) -> str:
	"""Return the first usable description, sanitised the way the middleware does."""
	for candidate in candidates:
		description = WHITESPACE_PATTERN.sub(
			" ", NON_DESCRIPTION_PATTERN.sub(" ", strip_html_tags(candidate))
		).strip()
		if description:
			return description[:HS_DESCRIPTION_MAX_LENGTH].strip()

	return ""


def is_valid_kra_pin(pin: str | None) -> bool:
	if not pin:
		return False

	return bool(KRA_PIN_PATTERN.match(pin.strip()))


def strip_html_tags(text: str | None) -> str:
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
	parts += ["0"] * (3 - len(parts))
	hour, minute, second = (int(part or 0) for part in parts[:3])

	return f"{hour:02d}:{minute:02d}:{second:02d}"


def get_qr_code_data_uri(data: str) -> str:
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
