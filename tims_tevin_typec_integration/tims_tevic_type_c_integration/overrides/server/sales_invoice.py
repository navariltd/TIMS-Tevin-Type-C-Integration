import re
from base64 import b64encode
from datetime import timedelta
from io import BytesIO
from typing import Literal

import qrcode
import requests

import frappe
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import get_formatted_email
from frappe.utils.user import get_users_with_role
from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data

CASH_CUSTOMER_CONTROL = "CASH CUSTOMER CONTROL"


def on_submit(doc: Document, method: str | None = None) -> None:
    """Submit hook for Sales Invoice that submits tax information to TIMS device"""
    company = frappe.defaults.get_user_default("Company")

    # Fetch active setting tied to current company
    # TODO: tie in additional filters to allow fine-grained searching of setting[s]
    setting = frappe.db.get_value(
        "TIMS Settings",
        {"company": company, "is_active": 1},
        ["server_address", "sender_id"],
        as_dict=True,
    )

    if setting:
        if doc.tax_id and not is_valid_kra_pin(doc.tax_id):
            # Validate KRA PIN if provided and raise exception if invalid
            frappe.throw(
                f"The entered PIN: <b>{doc.tax_id}</b>, is not valid. Please review this."
            )

        invoice_category = "Credit Note" if doc.is_return else "Tax Invoice"

        # HS Codes are mapped in the Tax Category doctype.
        # NOTE: VATABLE tax category never has an HS Code
        hs_code = frappe.db.get_value(
            "Tax Category", {"name": doc.tax_category}, ["custom_hs_code"]
        )
        # Use the Sales Tax Template to determine the Tax Rate
        tax_rule = frappe.db.get_value(
            "Tax Rule",
            {"tax_category": doc.tax_category, "tax_type": "Sales"},
            ["sales_tax_template"],
            as_dict=True,
        )
        tax_rate = frappe.db.get_value(
            "Sales Taxes and Charges",
            {
                "parent": tax_rule.sales_tax_template,
                "parenttype": "Sales Taxes and Charges Template",
            },
            ["rate"],
        )

        if tax_rate == 0 and not hs_code:
            # Ensure only Tax Rate 16% can have an empty HS Code. Otherwise, if no HS Code, raise error
            frappe.throw(
                "Please contact the <b>Account Controller</b> to ensure the HSCode for this customer's Tax Category is set"
            )

        relevant_invoice_number = ""
        if doc.is_return:
            # If this is a Credit Note
            if not doc.return_against:
                # If it's a standalone Credit Note, prompt user to Enter CU Invoice No.
                if not doc.custom_relevant_invoice_number:
                    frappe.throw(
                        "Please enter the CU Number in the <b>Relevant Invoice Number</b> field"
                    )

                relevant_invoice_number = doc.custom_relevant_invoice_number

            else:
                # If this isn't a standalone Credit Note, fetch CU invoice number
                relevant_invoice_number = frappe.db.get_value(
                    "Sales Invoice",
                    {"name": doc.return_against},
                    ["custom_cu_invoice_number"],
                )

        item_details = []  # ItemDetails list
        if tax_rate == 0:
            # Exempt customers: TaxRate: 0, TaxAmount: 0, and HSCode can't be empty
            for item in doc.items:
                item_details.append(
                    {
                        "HSDesc": item.description,
                        "TaxRate": 0,
                        "ItemAmount": abs(item.net_amount),
                        "TaxAmount": 0,
                        "TransactionType": "1",
                        "UnitPrice": item.base_rate,
                        "HSCode": hs_code,
                        "Quantity": abs(item.qty),
                    }
                )

        else:
            # Vatable customers
            item_taxes = get_itemised_tax_breakup_data(doc)  # Get Taxation breakdown
            tax_head = doc.taxes[0].description

            for item in doc.items:
                tax_details = list(
                    filter(lambda i: i["item"] == item.item_code, item_taxes)
                )[0]

                item_details.append(
                    {
                        "HSDesc": item.description,
                        "TaxRate": tax_details[tax_head]["tax_rate"],
                        "ItemAmount": abs(tax_details["taxable_amount"]),
                        "TaxAmount": abs(tax_details[tax_head]["tax_amount"]),
                        "TransactionType": "1",
                        "UnitPrice": item.base_rate,
                        "HSCode": "",
                        "Quantity": abs(item.qty),
                    }
                )

        trader_invoice_no = doc.name.split("-", 1)[
            -1]
        
        # Get numbers portion of name, i.e. INV-123456 > 123456
        # trader_invoice_no = doc.custom_delivery_note_no if doc.custom_delivery_note_no else doc.name.split("-", 1)[-1]
        if isinstance(doc.posting_time, str):
            # If it's a string
            posting_time = doc.posting_time.split(".", 1)[0]
        elif isinstance(doc.posting_time, timedelta):
            # If it's a timedelta object
            posting_time = str(doc.posting_time).split(".", 1)[0]
        posting_time_=format_time_for_invoice(posting_time)
        if doc.customer == CASH_CUSTOMER_CONTROL:
            pin = doc.custom_cash_customer_kra_pin or ""
        else:
            pin = doc.tax_id or ""

        payload = {
            "Invoice": {
                "SenderId": setting.sender_id,
                "TraderSystemInvoiceNumber": trader_invoice_no,
                "InvoiceCategory": invoice_category,
                "InvoiceTimestamp": f"{doc.posting_date}T{posting_time_}",
                "RelevantInvoiceNumber": relevant_invoice_number,
                "PINOfBuyer": pin,
                "Discount": 0,
                "InvoiceType": "Original",
                "TotalInvoiceAmount": abs(doc.grand_total),
                "TotalTaxableAmount": abs(doc.net_total),
                "TotalTaxAmount": (
                    abs(doc.total_taxes_and_charges)
                    if doc.tax_category != "Exempt"
                    else 0
                ),
                "ExemptionNumber": "",
                "ItemDetails": item_details,
            }
        }

        # Create Integration Request log
        url = f"{setting.server_address}/invoice"
        integration_request = create_request_log(
            data=payload,
            is_remote_request=True,
            service_name="TIMS",
            request_headers=None,
            url=url,
            reference_docname=doc.name,
            reference_doctype="Sales Invoice",
        )

        frappe.enqueue(
            make_tims_request,
            url=url,
            payload=payload,
            integration_request=integration_request.name,
            queue="default",
            is_async=True,
            timeout=65,
        )


def is_valid_kra_pin(pin: str) -> bool:
    """Checks if the string provided conforms to the pattern of a KRA PIN.
    This function does not validate if the PIN actually exists, only that
    it resembles a valid KRA PIN.

    Args:
        pin (str): The KRA PIN to test

    Returns:
        bool: True if input is a valid KRA PIN, False otherwise
    """
    pattern = r"^[a-zA-Z]{1}[0-9]{9}[a-zA-Z]{1}$"
    return bool(re.match(pattern, pin))


def update_integration_request(
    integration_request: str,
    status: Literal["Completed", "Failed"],
    output: str | None = None,
    error: str | None = None,
) -> None:
    """Updates the given integration request record

    Args:
        integration_request (str): The provided integration request
        status (Literal[&quot;Completed&quot;, &quot;Failed&quot;]): The new status of the request
        output (str | None, optional): The response message, if any. Defaults to None.
        error (str | None, optional): The error message, if any. Defaults to None.
    """
    doc = frappe.get_doc("Integration Request", integration_request, for_update=True)
    doc.status = str(status)
    doc.error = str(error)
    doc.output = str(output)

    doc.save(ignore_permissions=True)


def make_tims_request(
    url: str,
    payload: dict | None = None,
    timeout: int | float = 60,
    integration_request: str | None = None,
) -> None:
    try:
        response = requests.post(url=url, json=payload, timeout=timeout)
        response.raise_for_status()  # Raise exception if HTTPError or any other exception is raised

        try:
            invoice_info = response.json()["Invoice"]
        except KeyError as error:
            # If duplicate record was sent
            invoice_info = response.json()["Existing"]
        invoice = invoice_info["TraderSystemInvoiceNumber"]

        # Update Integration Request Log
        update_integration_request(integration_request, "Completed", response.json())

        # Update Sales Invoice record
        qr_code = get_qr_code(invoice_info["QRCode"])
        frappe.db.set_value(
            "Sales Invoice",
            f"INV-{invoice}",
            {
                "custom_cu_invoice_number": invoice_info["ControlCode"],
                "custom_qr_code": qr_code,
            },
            update_modified=True,
        )

    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.ConnectTimeout,
    ) as error:
        notify_users("System Manager", integration_request)
        update_integration_request(integration_request, "Failed", error=error)
        frappe.throw(f"{error}")

    except requests.exceptions.HTTPError as error:
        message = f"{error.response.status_code}\n\n{error.response.text}"
        notify_users("System Manager", integration_request)
        update_integration_request(integration_request, "Failed", error=message)


def get_qr_code(data: str) -> str:
    """Generate QR Code data

    Args:
        data (str): The information used to generate the QR Code

    Returns:
        str: The QR Code.
    """
    qr_code_bytes = get_qr_code_bytes(data, format="PNG")
    base_64_string = bytes_to_base64_string(qr_code_bytes)

    return add_file_info(base_64_string)


def add_file_info(data: str) -> str:
    """Add info about the file type and encoding.

    This is required so the browser can make sense of the data."""
    return f"data:image/png;base64, {data}"


def get_qr_code_bytes(data: bytes | str, format: str = "PNG") -> bytes:
    """Create a QR code and return the bytes."""
    img = qrcode.make(data)

    buffered = BytesIO()
    img.save(buffered, format=format)

    return buffered.getvalue()


def bytes_to_base64_string(data: bytes) -> str:
    """Convert bytes to a base64 encoded string."""
    return b64encode(data).decode("utf-8")


def notify_users(role: str, integration_request: str) -> None:
    """Notify users with provided role of the failed integration request

    Args:
        role (str): The role to alert users on
        integration_request (str): The integration request to alert users of

    Returns:
        None
    """
    users = get_users_with_role(role)
    recipients = [
        get_formatted_email(user).replace("<", "(").replace(">", ")") for user in users
    ]

    frappe.sendmail(
        recipients,
        subject="TIMS Error",
        message=f"An Error has been logged for TIMS Integration under the integration Request: {integration_request}",
        reference_doctype="Integration Request",
        reference_name=integration_request,
        delayed=False,
    )

def format_time_for_invoice(time: str) -> str:
    """Format time to ensure leading zero for single-digit hours."""
    hour, minute, second = time.split(":")
    return f"{int(hour):02d}:{minute}:{second}"