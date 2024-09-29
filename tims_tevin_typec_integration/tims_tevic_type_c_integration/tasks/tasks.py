import requests

import frappe
from frappe.integrations.utils import create_request_log

from ..overrides.server.sales_invoice import on_submit, update_integration_request


def resend_invoices() -> None:
    # Fetch all invoices with no CU Invoice number and QR code value, that are submitted
    query = """
    SELECT name
    FROM `tabSales Invoice`
    WHERE custom_cu_invoice_number IS NULL
        AND custom_qr_code IS NULL
        AND docstatus = 1;
    """
    invoices = frappe.db.sql(query, as_dict=True)

    for invoice in invoices:
        doc = frappe.get_doc("Sales Invoice", invoice.name)

        on_submit(doc)


def get_eod_records() -> None:
    company = frappe.defaults.get_user_default("Company")

    setting = frappe.db.get_value(
        "TIMS Settings",
        {"company": company},
        ["server_address", "sender_id"],
        as_dict=True,
    )

    if setting:
        url = f"{setting.server_address}/eod/{setting.sender_id}"
        integration_request = create_request_log(
            url=url,
            is_remote_request=True,
            service_name="TIMS",
            request_headers=None,
            data=dict(),
        )

        frappe.enqueue(
            make_tims_get_request,
            url=url,
            integration_request=integration_request.name,
            queue="default",
            is_async=True,
            timeout=65,
        )


def make_tims_get_request(url: str, integration_request: str) -> None:
    try:
        response = requests.get(url)
        response.raise_for_status()

        eod_info = response.json()
        update_integration_request(integration_request, "Completed", eod_info)

        eod_doc = frappe.new_doc("End Of Day TIMS Records")

        eod_doc.end_of_day_id = eod_info["EODId"]
        eod_doc.date_of_summary = eod_info["DateOfEODSummary"]
        eod_doc.transmission_timestamp = eod_info["EODTransmissionTimestamp"]
        eod_doc.first_invoice_number = eod_info["NumberOfFirstInvoice"]
        eod_doc.last_invoice_number = eod_info["NumberOfLastInvoice"]
        eod_doc.total_invoice_amount = eod_info["TotalInvoiceAmountOfTheDay"]
        eod_doc.total_taxable_amount = eod_info["TotalTaxableAmountOfTheDay"]
        eod_doc.total_tax_amount = eod_info["TotalTaxAmountOfTheDay"]
        eod_doc.number_of_invoices_sent = eod_info["NumberOfInvoicesSentOfTheDay"]

        eod_doc.save()
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.ConnectTimeout,
    ) as error:
        # TODO: Create notifications if any exception/error
        frappe.throw(f"{error}")
    except requests.exceptions.HTTPError as error:
        # TODO: Create notifications if any exception/error
        message = f"{error.response.status_code}\n\n{error.response.text}"
        update_integration_request(integration_request, "Failed", error=message)
