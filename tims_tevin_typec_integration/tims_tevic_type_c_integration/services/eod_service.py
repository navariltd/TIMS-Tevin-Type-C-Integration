"""Pull the device's end-of-day summary and file it as a record."""

from __future__ import annotations

import frappe

from ..api.api_connector import RemoteRequestError
from ..api.tims_api import fetch_eod_summary
from ..utils import log_error

SYNC_EOD_METHOD = (
	"tims_tevin_typec_integration.tims_tevic_type_c_integration.services.eod_service.sync_eod_summary"
)

EOD_DOCTYPE = "End Of Day TIMS Records"

JOB_TIMEOUT_SECONDS = 300

#: TIMS response key -> End Of Day TIMS Records fieldname.
EOD_FIELD_MAP = {
	"EODId": "end_of_day_id",
	"DateOfEODSummary": "date_of_summary",
	"EODTransmissionTimestamp": "transmission_timestamp",
	"NumberOfFirstInvoice": "first_invoice_number",
	"NumberOfLastInvoice": "last_invoice_number",
	"TotalInvoiceAmountOfTheDay": "total_invoice_amount",
	"TotalTaxableAmountOfTheDay": "total_taxable_amount",
	"TotalTaxAmountOfTheDay": "total_tax_amount",
	"NumberOfInvoicesSentOfTheDay": "number_of_invoices_sent",
}


def queue_eod_sync() -> None:
	"""Scheduled entry point: fetch a summary from every configured device."""
	for settings_name in frappe.get_all("TIMS Settings", filters={"is_active": 1}, pluck="name"):
		frappe.enqueue(
			SYNC_EOD_METHOD,
			queue="long",
			timeout=JOB_TIMEOUT_SECONDS,
			settings_name=settings_name,
		)


def sync_eod_summary(settings_name: str) -> None:
	"""Background job: fetch one device's summary and record it."""
	settings = frappe.get_cached_doc("TIMS Settings", settings_name)

	try:
		summary = fetch_eod_summary(settings)
	except RemoteRequestError as error:
		log_error(f"End of day fetch for {settings_name} failed", str(error))
		return

	create_eod_record(summary, settings.company)


def create_eod_record(summary: dict, company: str) -> None:
	"""File the summary, ignoring one the device has already reported."""
	end_of_day_id = summary.get("EODId")

	if not end_of_day_id:
		log_error("TIMS end of day response has no EODId", frappe.as_json(summary))
		return

	if frappe.db.exists(EOD_DOCTYPE, end_of_day_id):
		return

	record = frappe.new_doc(EOD_DOCTYPE)
	record.company = company
	record.update({field: summary.get(key) for key, field in EOD_FIELD_MAP.items()})
	record.insert(ignore_permissions=True)
