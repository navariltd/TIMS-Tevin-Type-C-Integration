"""Turn a Sales Invoice into a TIMS submission and apply the device's reply."""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from ..api.api_connector import RemoteRequestError
from ..api.tims_api import post_invoice
from ..utils import (
	CU_INVOICE_NUMBER_LENGTH,
	format_posting_time,
	get_qr_code_data_uri,
	is_valid_kra_pin,
	log_error,
	strip_html_tags,
)
from .tax_service import get_item_tax, get_itemised_tax_details, get_total_tax_amount, validate_itemised_tax

if TYPE_CHECKING:
	from ..doctype.tims_settings.tims_settings import TIMSSettings

SEND_PAYLOAD_METHOD = (
	"tims_tevin_typec_integration.tims_tevic_type_c_integration.services.invoice_service.send_payload"
)

CREDIT_NOTE = "Credit Note"
TAX_INVOICE = "Tax Invoice"

#: Seconds allowed for one queued submission, a little above the request timeout.
JOB_TIMEOUT_SECONDS = 300


# ---------------------------------------------------------------- settings


def get_settings(company: str | None) -> TIMSSettings | None:
	"""Return the active TIMS Settings for a company, if it has been onboarded."""
	if not company:
		return None

	name = frappe.db.get_value("TIMS Settings", {"company": company, "is_active": 1}, "name")

	return frappe.get_cached_doc("TIMS Settings", name) if name else None


# ---------------------------------------------------------------- submission


def submit_invoice(doc: Document) -> None:
	"""Validate an invoice and queue it for transmission to the TIMS device.

	Companies without active TIMS Settings are not onboarded onto TIMS, so their
	invoices pass through untouched.
	"""
	settings = get_settings(doc.company)
	if not settings or not should_submit(doc):
		return

	payload = build_payload(doc, settings)

	frappe.enqueue(
		SEND_PAYLOAD_METHOD,
		queue="default",
		timeout=JOB_TIMEOUT_SECONDS,
		# The worker must not read the invoice before this transaction lands.
		enqueue_after_commit=True,
		invoice=doc.name,
		payload=payload,
		settings_name=settings.name,
	)


def should_submit(doc: Document) -> bool:
	"""Opening entries are historical, and a stamped invoice is already filed."""
	return doc.is_opening != "Yes" and not doc.custom_cu_invoice_number


def send_payload(invoice: str, payload: dict, settings_name: str) -> None:
	"""Background job: post the payload and record the device's response.

	Failures are already logged against the Integration Request, which drives
	the failure Notification, so the job swallows them instead of crashing the
	worker.
	"""
	settings = frappe.get_cached_doc("TIMS Settings", settings_name)

	try:
		response = post_invoice(settings, payload, reference_docname=invoice)
	except RemoteRequestError as error:
		log_error(f"Submission of {invoice} to TIMS failed", str(error))
		return

	apply_response(invoice, response)


def apply_response(invoice: str, response: dict) -> None:
	"""Stamp the CU invoice number and QR code returned by the device."""
	# "Existing" is returned when the device has already filed this invoice.
	invoice_info = response.get("Invoice") or response.get("Existing") or {}
	control_code = invoice_info.get("ControlCode")
	qr_code = invoice_info.get("QRCode")

	if not control_code or not qr_code:
		log_error(
			f"TIMS response for {invoice} is missing the control code or QR code",
			frappe.as_json(response),
		)
		return

	frappe.db.set_value(
		"Sales Invoice",
		invoice,
		{
			"custom_cu_invoice_number": control_code,
			"custom_qr_code": get_qr_code_data_uri(qr_code),
		},
		update_modified=True,
	)


# ---------------------------------------------------------------- payload


def build_payload(doc: Document, settings: TIMSSettings) -> dict:
	"""Assemble the TIMS invoice payload, validating the invoice as it goes."""
	validate_buyer_pin(doc)

	tax_details = get_itemised_tax_details(doc)
	validate_itemised_tax(doc, tax_details)
	validate_hs_codes(doc, tax_details)

	return {
		"Invoice": {
			"SenderId": settings.sender_id,
			"TraderSystemInvoiceNumber": doc.name,
			"InvoiceCategory": CREDIT_NOTE if doc.is_return else TAX_INVOICE,
			"InvoiceTimestamp": f"{doc.posting_date}T{format_posting_time(doc.posting_time)}",
			"RelevantInvoiceNumber": get_relevant_invoice_number(doc),
			"PINOfBuyer": get_buyer_pin(doc, settings),
			"Discount": 0,
			"InvoiceType": "Original",
			"TotalInvoiceAmount": abs(flt(doc.base_grand_total)),
			"TotalTaxableAmount": abs(flt(doc.base_net_total)),
			"TotalTaxAmount": abs(get_total_tax_amount(tax_details)),
			"ExemptionNumber": "",
			"ItemDetails": build_item_details(doc, tax_details),
		}
	}


def build_item_details(doc: Document, tax_details: dict[str, dict]) -> list[dict]:
	"""Describe each invoice line, reporting ERPNext's own tax figures."""
	items = []

	for item in doc.items:
		tax = get_item_tax(tax_details, item)
		items.append(
			{
				"HSDesc": strip_html_tags(item.description) or item.item_name,
				"ItemAmount": abs(flt(item.base_net_amount)),
				"TransactionType": "1",
				"UnitPrice": abs(flt(item.base_net_rate)),
				"Quantity": abs(flt(item.qty)),
				"HSCode": item.custom_hs_code or "",
				"TaxRate": flt(tax["tax_rate"]),
				"TaxAmount": abs(flt(tax["tax_amount"])),
			}
		)

	return items


def get_buyer_pin(doc: Document, settings: TIMSSettings) -> str:
	"""Read the buyer's KRA PIN, allowing walk-in customers to supply their own."""
	if settings.cash_customer and doc.customer == settings.cash_customer:
		pin = doc.get("custom_cash_customer_kra_pin")
	else:
		pin = doc.tax_id

	return (pin or "").strip()


# ---------------------------------------------------------------- validation


def validate_buyer_pin(doc: Document) -> None:
	if doc.tax_id and not is_valid_kra_pin(doc.tax_id):
		frappe.throw(
			_("The PIN {0} is not a valid KRA PIN. Please review it before submitting to TIMS.").format(
				frappe.bold(doc.tax_id)
			)
		)


def validate_hs_codes(doc: Document, tax_details: dict[str, dict]) -> None:
	"""KRA requires an HS Code on every line that is not charged tax."""
	missing = [
		item.idx
		for item in doc.items
		if not flt(get_item_tax(tax_details, item)["tax_rate"]) and not item.custom_hs_code
	]

	if missing:
		frappe.throw(
			_("Rows {0} are zero rated but have no HS Code. Ask the Account Controller to set one.").format(
				frappe.bold(", ".join(str(idx) for idx in missing))
			)
		)


def get_relevant_invoice_number(doc: Document) -> str:
	"""Return the CU number of the invoice a credit note reverses."""
	if not doc.is_return:
		return ""

	if doc.return_against:
		relevant_invoice_number = frappe.db.get_value(
			"Sales Invoice", doc.return_against, "custom_cu_invoice_number"
		)
	else:
		relevant_invoice_number = doc.custom_relevant_invoice_number

	if not relevant_invoice_number:
		frappe.throw(
			_("Enter the CU number of the original invoice in the {0} field.").format(
				frappe.bold(_("Relevant Invoice Number"))
			)
		)

	if len(relevant_invoice_number) != CU_INVOICE_NUMBER_LENGTH:
		frappe.throw(
			_("The Relevant Invoice Number must be the {0} character CU number. It is {1} characters.").format(
				CU_INVOICE_NUMBER_LENGTH, len(relevant_invoice_number)
			)
		)

	return relevant_invoice_number
