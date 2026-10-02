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
	get_hs_description,
	get_qr_code_data_uri,
	get_trader_invoice_number,
	is_valid_kra_pin,
	kra_round,
	log_error,
)
from .tax_service import get_item_tax, get_itemised_tax_details, validate_itemised_tax

if TYPE_CHECKING:
	from ..doctype.tims_settings.tims_settings import TIMSSettings

SEND_PAYLOAD_METHOD = (
	"tims_tevin_typec_integration.tims_tevin_type_c_integration.services.invoice_service.send_payload"
)

CREDIT_NOTE = "Credit Note"
DEBIT_NOTE = "Debit Note"
TAX_INVOICE = "Tax Invoice"

STANDARD_VAT_RATE = 16.0

JOB_TIMEOUT_SECONDS = 300


def get_settings(company: str | None) -> TIMSSettings | None:
	"""Return the active TIMS Settings for a company, if it has been onboarded."""
	if not company:
		return None

	name = frappe.db.get_value("TIMS Settings", {"company": company, "is_active": 1}, "name")

	return frappe.get_cached_doc("TIMS Settings", name) if name else None


def submit_invoice(doc: Document) -> bool:
	settings = get_settings(doc.company)
	if not settings or not should_submit(doc):
		return False

	payload = build_payload(doc, settings)
	if payload is None:
		return False

	frappe.enqueue(
		SEND_PAYLOAD_METHOD,
		queue="default",
		timeout=JOB_TIMEOUT_SECONDS,
		enqueue_after_commit=True,
		job_id=f"tims-submit-{doc.name}",
		deduplicate=True,
		invoice=doc.name,
		payload=payload,
		settings_name=settings.name,
	)

	return True


def validate_cancellation(doc: Document) -> None:
	"""An invoice filed with KRA is final: only a Credit Note can reverse it."""
	if not doc.get("custom_cu_invoice_number"):
		return

	frappe.throw(
		_(
			"{0} has already been submitted to TIMS (CU Invoice Number {1}) and cannot be cancelled. "
			"Raise a Credit Note against it instead."
		).format(frappe.bold(doc.name), frappe.bold(doc.custom_cu_invoice_number)),
		title=_("Already Submitted to TIMS"),
	)


def should_submit(doc: Document) -> bool:
	"""Opening entries are historical, and a stamped invoice is already filed."""
	return doc.is_opening != "Yes" and not doc.custom_cu_invoice_number


def send_payload(invoice: str, payload: dict, settings_name: str) -> None:
	if frappe.db.get_value("Sales Invoice", invoice, "docstatus") != 1:
		return

	settings = frappe.get_cached_doc("TIMS Settings", settings_name)

	try:
		response = post_invoice(settings, payload, reference_docname=invoice)
	except RemoteRequestError as error:
		log_error(f"Submission of {invoice} to TIMS failed", str(error))
		return

	apply_response(invoice, response)


def apply_response(invoice: str, response: dict) -> None:
	"""Stamp the CU invoice number and QR code returned by the device."""
	invoice_info = response.get("Invoice") or response.get("Existing") or {}
	control_code = invoice_info.get("ControlCode") or response.get("ControlCode")
	qr_code = invoice_info.get("QRCode") or response.get("QRCode")

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
			"custom_cu_invoice_number": str(control_code),
			"custom_qr_code": get_qr_code_data_uri(qr_code),
		},
		update_modified=True,
	)


def build_payload(doc: Document, settings: TIMSSettings) -> dict | None:
	validate_buyer_pin(doc)

	tax_details = get_itemised_tax_details(doc)
	validate_itemised_tax(doc, tax_details)
	validate_hs_codes(doc, tax_details)

	relevant_invoice_number = get_relevant_invoice_number(doc)
	if relevant_invoice_number is None:
		return None

	item_details = build_item_details(doc, tax_details)
	total_taxable = kra_round(sum(item["ItemAmount"] for item in item_details))
	total_tax = kra_round(sum(item["TaxAmount"] for item in item_details))

	return {
		"Invoice": {
			"SenderId": settings.sender_id,
			"TraderSystemInvoiceNumber": get_trader_invoice_number(doc.name),
			"InvoiceCategory": get_invoice_category(doc),
			"InvoiceTimestamp": f"{doc.posting_date}T{format_posting_time(doc.posting_time)}",
			"RelevantInvoiceNumber": relevant_invoice_number,
			"PINOfBuyer": get_buyer_pin(doc, settings),
			"Discount": 0,
			"InvoiceType": "Original",
			"TotalInvoiceAmount": kra_round(total_taxable + total_tax),
			"TotalTaxableAmount": total_taxable,
			"TotalTaxAmount": total_tax,
			"ExemptionNumber": "",
			"ItemDetails": item_details,
		}
	}


def get_invoice_category(doc: Document) -> str:
	if doc.is_return:
		return CREDIT_NOTE

	if doc.get("is_debit_note"):
		return DEBIT_NOTE

	return TAX_INVOICE


def is_standard_rated(tax_rate: float) -> bool:
	return flt(tax_rate) == STANDARD_VAT_RATE


def build_item_details(doc: Document, tax_details: dict[str, dict]) -> list[dict]:
	items = []

	for item in doc.items:
		tax_rate = flt(get_item_tax(tax_details, item)["tax_rate"])
		item_amount = kra_round(abs(flt(item.base_net_amount)))

		items.append(
			{
				"HSDesc": get_hs_description(item.item_name, item.description, item.item_code),
				"ItemAmount": item_amount,
				"TransactionType": "1",
				"UnitPrice": kra_round(abs(flt(item.base_net_rate))),
				"Quantity": kra_round(abs(flt(item.qty))),
				"HSCode": "" if is_standard_rated(tax_rate) else (item.custom_hs_code or ""),
				"TaxRate": tax_rate,
				"TaxAmount": kra_round(item_amount * tax_rate / 100),
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


def validate_buyer_pin(doc: Document) -> None:
	if doc.tax_id and not is_valid_kra_pin(doc.tax_id):
		frappe.throw(
			_("The PIN {0} is not a valid KRA PIN. Please review it before submitting to TIMS.").format(
				frappe.bold(doc.tax_id)
			)
		)


def validate_hs_codes(doc: Document, tax_details: dict[str, dict]) -> None:
	"""KRA requires an HS Code on every line not charged the standard VAT rate."""
	missing = [
		item.idx
		for item in doc.items
		if not is_standard_rated(get_item_tax(tax_details, item)["tax_rate"]) and not item.custom_hs_code
	]

	if missing:
		frappe.throw(
			_(
				"Rows {0} are not charged the standard VAT rate but have no HS Code. "
				"Ask the Account Controller to set one."
			).format(frappe.bold(", ".join(str(idx) for idx in missing)))
		)


def get_relevant_invoice_number(doc: Document) -> str | None:
	category = get_invoice_category(doc)
	if category == TAX_INVOICE:
		return ""

	relevant_invoice_number = doc.custom_relevant_invoice_number
	if doc.return_against:
		relevant_invoice_number = (
			frappe.db.get_value("Sales Invoice", doc.return_against, "custom_cu_invoice_number")
			or relevant_invoice_number
		)

	relevant_invoice_number = (relevant_invoice_number or "").strip()
	submit_action = frappe.bold(_("TIMS Actions > Submit to TIMS"))

	if not relevant_invoice_number and doc.return_against:
		reason = _("the invoice it is raised against ({0}) has no CU Invoice Number yet").format(
			frappe.bold(doc.return_against)
		)
		remedy = _("Send {0} to TIMS first, then use {1} on this document.").format(
			frappe.bold(doc.return_against), submit_action
		)
	elif not relevant_invoice_number:
		reason = _("the {0} field is empty").format(frappe.bold(_("Relevant Invoice Number")))
		remedy = _("Enter the CU Number of the original invoice in that field, then use {0}.").format(
			submit_action
		)
	elif len(relevant_invoice_number) != CU_INVOICE_NUMBER_LENGTH:
		reason = _("{0} is not a valid CU Number: it must be exactly {1} characters, but is {2}").format(
			frappe.bold(relevant_invoice_number),
			CU_INVOICE_NUMBER_LENGTH,
			len(relevant_invoice_number),
		)
		remedy = _("Correct the {0}, then use {1}.").format(
			frappe.bold(_("Relevant Invoice Number")), submit_action
		)
	else:
		return relevant_invoice_number

	frappe.msgprint(
		_("This {0} has been submitted, but was {1} because {2}.").format(
			_(category), frappe.bold(_("not sent to TIMS")), reason
		)
		+ "<br><br>"
		+ remedy,
		title=_("Not Sent to TIMS"),
		indicator="orange",
	)

	return None
