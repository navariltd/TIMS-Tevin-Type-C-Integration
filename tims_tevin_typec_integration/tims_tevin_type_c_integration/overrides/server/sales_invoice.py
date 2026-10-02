"""Sales Invoice hooks. The work itself lives in the service layer."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit

from ...services.hs_code_service import set_item_hs_codes
from ...services.invoice_service import (
	get_settings,
	submit_invoice,
	validate_cancellation,
)


def before_save(doc: Document, method: str | None = None) -> None:
	set_item_hs_codes(doc)


def on_submit(doc: Document, method: str | None = None) -> None:
	submit_invoice(doc)


def before_cancel(doc: Document, method: str | None = None) -> None:
	validate_cancellation(doc)


@frappe.whitelist()
@rate_limit(limit=10, seconds=60)
def resubmit_to_tims(invoice: str) -> str | None:
	"""Retry a submitted invoice the device has not stamped yet."""
	doc = frappe.get_doc("Sales Invoice", invoice)
	doc.check_permission("submit")

	if doc.docstatus != 1:
		frappe.throw(_("Only a submitted invoice can be sent to TIMS"))

	if doc.custom_cu_invoice_number:
		frappe.throw(_("{0} has already been filed with TIMS").format(frappe.bold(invoice)))

	if not get_settings(doc.company):
		frappe.throw(_("{0} has no active TIMS Settings").format(frappe.bold(doc.company)))

	if not submit_invoice(doc):
		return None

	return _("{0} has been queued for submission to TIMS").format(invoice)
