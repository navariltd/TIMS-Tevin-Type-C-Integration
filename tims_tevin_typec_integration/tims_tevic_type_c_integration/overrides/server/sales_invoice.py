import re
from base64 import b64encode
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


def on_submit(doc: Document, method: str | None = None) -> None:
    # TODO: Correctly pick Tax Head from Sales Taxes and Charges Template. Ensure HSCode in vata
    # Create the payload generation functionality here
    company = frappe.defaults.get_user_default("Company")

    setting = frappe.db.get_value(
        "TIMS Settings",
        {"company": company},
        ["server_address", "sender_id"],
        as_dict=True,
    )

    if setting:
        if doc.tax_id and not is_valid_kra_pin(doc.tax_id):
            # Validate KRA PIN if provided
            frappe.throw(
                f"The entered PIN: <b>{doc.tax_id}</b>, is not valid. Please review this."
            )

        invoice_category = "Credit Note" if doc.is_return else "Tax Invoice"
        hs_code = frappe.db.get_value(
            "Tax Category", {"name": doc.tax_category}, ["custom_hs_code"]
        )
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
            frappe.throw(
                "Please contact the Account Controller to ensure the HSCode for this customer's Tax Category is set"
            )

        relevant_invoice_number = ""
        if doc.is_return:
            # If this is a Credit Note
            if not doc.return_against:
                # If it's a standalone Credit Note
                if not doc.custom_relevant_invoice_number:
                    frappe.throw(
                        "Please enter the CU Number in the <b>Relevant Invoice Number</b> field"
                    )

                relevant_invoice_number = doc.custom_relevant_invoice_number

            else:
                # If this isn't a standalone Credit Note
                relevant_invoice_number = frappe.db.get_value(
                    "Sales Invoice",
                    {"name": doc.return_against},
                    ["custom_cu_invoice_number"],
                )

        item_details = []
        if tax_rate == 0:
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
            item_taxes = get_itemised_tax_breakup_data(doc)
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

        trader_invoice_no = doc.name.split("-", 2)[-1]
        payload = {
            "Invoice": {
                "SenderId": setting.sender_id,
                "TraderSystemInvoiceNumber": trader_invoice_no,
                "InvoiceCategory": invoice_category,
                "InvoiceTimestamp": f"{doc.posting_date}T{doc.posting_time.split('.', 1)[0]}",
                "RelevantInvoiceNumber": relevant_invoice_number,
                "PINOfBuyer": doc.tax_id or "",
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

        invoice_info = response.json()["Invoice"]
        invoice = invoice_info["TraderSystemInvoiceNumber"]

        # Update Integration Request Log
        update_integration_request(integration_request, "Completed", invoice_info)

        # Update Sales Invoice record
        qr_code = get_qr_code(invoice_info["QRCode"])
        # TODO: Figure out an elegant strategy to handle the Invoice number
        frappe.db.set_value(
            "Sales Invoice",
            f"ACC-SINV-{invoice[:4]}-{invoice[4:9]}",
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
        # TODO: Create notifications if any exception/error
        notify_users("System Manager", integration_request)
        frappe.throw(f"{error}")

    except requests.exceptions.HTTPError as error:
        # TODO: Create notifications if any exception/error
        message = f"{error.response.status_code}\n\n{error.response.text}"
        update_integration_request(integration_request, "Failed", error=message)
        notify_users("System Manager", integration_request)


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
    users = get_users_with_role(role)
    recipients = [
        get_formatted_email(user).replace("<", "(").replace(">", ")") for user in users
    ]

    frappe.sendmail(
        recipients,
        subject="TIMS Error",
        message=f"An Error has been logged for TIMS Integration under integration Request: {integration_request}",
        reference_doctype="Integration Request",
        reference_name=integration_request,
        delayed=False,
    )
