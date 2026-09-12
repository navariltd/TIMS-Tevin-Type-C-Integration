"""Scheduled maintenance for the TIMS integration."""

from __future__ import annotations

import frappe
from frappe.utils import add_days, add_to_date, now_datetime, today

from ..services.eod_service import queue_eod_sync
from ..services.invoice_service import submit_invoice
from ..utils import log_error

SETTINGS_FIELDS = ["name", "company", "resend_batch_size", "resend_lookback_days", "resend_cooldown_minutes"]


def resend_invoices() -> None:
	"""Retry submitted invoices that the device never stamped."""
	for settings in frappe.get_all("TIMS Settings", filters={"is_active": 1}, fields=SETTINGS_FIELDS):
		for invoice in get_invoices_to_resend(settings):
			try:
				submit_invoice(frappe.get_doc("Sales Invoice", invoice))
			except Exception:
				# One unfilable invoice must not stop the rest of the batch.
				log_error(f"Could not queue {invoice} for resubmission to TIMS")


def get_invoices_to_resend(settings: frappe._dict) -> list[str]:
	"""Find recent submitted invoices that still have no CU number."""
	candidates = frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"company": settings.company,
			"is_opening": ("!=", "Yes"),
			"custom_cu_invoice_number": ("in", (None, "")),
			"posting_date": (">=", add_days(today(), -(settings.resend_lookback_days or 7))),
		},
		pluck="name",
		order_by="posting_date asc, creation asc",
		limit=settings.resend_batch_size or 20,
	)

	if not candidates:
		return []

	attempted = get_recently_attempted(candidates, settings.resend_cooldown_minutes or 60)

	return [invoice for invoice in candidates if invoice not in attempted]


def get_recently_attempted(invoices: list[str], cooldown_minutes: int) -> set[str]:
	"""Invoices already tried within the cooldown, so a dead device is not hammered."""
	return set(
		frappe.get_all(
			"Integration Request",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_docname": ("in", invoices),
				"creation": (">", add_to_date(now_datetime(), minutes=-cooldown_minutes)),
			},
			pluck="reference_docname",
		)
	)


def get_eod_records() -> None:
	"""Scheduled entry point for the end-of-day summary fetch."""
	queue_eod_sync()
