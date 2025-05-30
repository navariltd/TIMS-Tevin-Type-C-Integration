import re
from base64 import b64encode
from datetime import timedelta
from io import BytesIO
from typing import Literal

import qrcode
import requests
import json
import frappe
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import get_formatted_email
from frappe.utils.user import get_users_with_role
from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data

CASH_CUSTOMER_CONTROL = "CASH CUSTOMER CONTROL"

def on_submit(doc: Document, method: str | None = None) -> None:
    """Submit hook for Sales Invoice that submits tax information to TIMS device"""
    if should_skip_submission(doc):
        return
    
    setting = get_tims_settings(doc)
    if not setting:
        return
        
    validate_tax_id(doc)
    
    invoice_category = get_invoice_category(doc)
    tax_rate = get_tax_details(doc)
    validate_tax_exemption(doc, tax_rate)
    
    relevant_invoice_number = get_relevant_invoice_number(doc)
    item_details = build_item_details(doc, tax_rate)
    
    payload = build_payload(doc, setting, invoice_category, relevant_invoice_number, item_details)
    submit_to_tims(doc, setting, payload)


def should_skip_submission(doc) -> bool:
    """Check if submission should be skipped"""
    company = frappe.defaults.get_user_default("Company")
    if not company or doc.is_opening == "Yes":
        return True
    return False


def get_tims_settings(doc) -> dict | None:
    """Get TIMS settings for the company"""
    company = frappe.defaults.get_user_default("Company")
    return frappe.db.get_value(
        "TIMS Settings",
        {"company": company, "is_active": 1},
        ["server_address", "sender_id"],
        as_dict=True,
    )


def validate_tax_id(doc) -> None:
    """Validate the tax ID/KRA PIN if present"""
    if doc.tax_id and not is_valid_kra_pin(doc.tax_id):
        frappe.throw(
            f"The entered PIN: <b>{doc.tax_id}</b>, is not valid. Please review this."
        )


def get_invoice_category(doc) -> str:
    """Determine the invoice category"""
    return "Credit Note" if doc.is_return else "Tax Invoice"


def get_tax_details(doc) -> tuple:
    """Get tax details (HS Code and tax rate) for the document"""
    
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
    
    return tax_rate


def validate_tax_exemption(doc, tax_rate) -> None:
    """Validate tax exemption requirements"""
    for item in doc.items:
        if tax_rate == 0 and not item.custom_hs_code:
            frappe.throw(
                "Please contact the <b>Account Controller</b> to ensure the HSCode for this customer's Tax Category is set"
            )


def get_relevant_invoice_number(doc) -> str:
    """Get and validate the relevant invoice number for returns"""
    relevant_invoice_number = ""
    
    if doc.is_return:
        if not doc.return_against:
            if not doc.custom_relevant_invoice_number:
                frappe.throw(
                    "Please enter the CU Number in the <b>Relevant Invoice Number</b> field"
                )
            relevant_invoice_number = doc.custom_relevant_invoice_number
        else:
            relevant_invoice_number = frappe.db.get_value(
                "Sales Invoice",
                {"name": doc.return_against},
                ["custom_cu_invoice_number"],
            )
        validate_relevant_invoice_number(relevant_invoice_number)
    
    return relevant_invoice_number


def build_item_details(doc, tax_rate) -> list[dict]:
    """Build item details for the payload"""
    item_details = []
    
    for item in doc.items:
        item_data = {
            "HSDesc": strip_html_tags(item.description),
            "ItemAmount": abs(item.base_net_amount),
            "TransactionType": "1",
            "UnitPrice": item.base_net_rate,
            "Quantity": abs(item.qty),
            "HSCode":item.custom_hs_code
        }
        
        if tax_rate == 0:
            # Exempt customers
            item_data.update({
                "TaxRate": 0,
                "TaxAmount": 0,
            })
        else:
            item_data.update({
                "TaxRate": item.custom_tax_rate,
                "TaxAmount": abs(item.custom_tax_amount),
                "HSCode": "",
            })
        
        item_details.append(item_data)
    
    return item_details


def get_trader_invoice_number(doc) -> str:
    """Get the trader invoice number from document"""
    return doc.name.split("-", 1)[-1]
    # Alternative implementation if using custom_delivery_note_no:
    # return doc.custom_delivery_note_no if doc.custom_delivery_note_no else doc.name.split("-", 1)[-1]


def format_posting_time(posting_time) -> str:
    """Format the posting time for the invoice"""
    if isinstance(posting_time, str):
        posting_time = posting_time.split(".", 1)[0]
    elif isinstance(posting_time, timedelta):
        posting_time = str(posting_time).split(".", 1)[0]
    return format_time_for_invoice(posting_time)


def get_buyer_pin(doc) -> str:
    """Get the buyer's PIN/KRA tax ID"""
    CASH_CUSTOMER_CONTROL = "Cash Customer"  # This should probably be a constant defined elsewhere
    if doc.customer == CASH_CUSTOMER_CONTROL:
        return doc.custom_cash_customer_kra_pin or ""
    return doc.tax_id or ""


def build_payload(doc, setting, invoice_category, relevant_invoice_number, item_details) -> dict:
    """Build the payload for TIMS submission"""
    trader_invoice_no = get_trader_invoice_number(doc)
    posting_time = format_posting_time(doc.posting_time)
    pin = get_buyer_pin(doc)
    
    return {
        "Invoice": {
            "SenderId": setting.sender_id,
            "TraderSystemInvoiceNumber": trader_invoice_no,
            "InvoiceCategory": invoice_category,
            "InvoiceTimestamp": f"{doc.posting_date}T{posting_time}",
            "RelevantInvoiceNumber": relevant_invoice_number,
            "PINOfBuyer": pin.strip(),
            "Discount": 0,
            "InvoiceType": "Original",
            "TotalInvoiceAmount": abs(doc.base_grand_total),
            "TotalTaxableAmount": abs(doc.base_net_total),
            "TotalTaxAmount": (
                abs(doc.base_total_taxes_and_charges)
                if doc.tax_category != "Exempt"
                else 0
            ),
            "ExemptionNumber": "",
            "ItemDetails": item_details,
        }
    }


def submit_to_tims(doc, setting, payload) -> None:
    """Submit the payload to TIMS"""
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


def strip_html_tags(text):
    clean_text = re.sub(r'<[^>]*>', '', text)
    return clean_text


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

        update_integration_request(integration_request, "Completed", response.json())

        qr_code = get_qr_code(invoice_info["QRCode"])
        
        '''Change the prefix to CN- if the invoice is a credit note'''
        invoice_prefix = "CN-" if invoice_info["InvoiceCategory"] == "Credit Note" else "INV-"
        invoice_number = f"{invoice_prefix}{invoice}"

        frappe.db.set_value(
            "Sales Invoice",
            invoice_number,
            {
                "custom_cu_invoice_number": invoice_info["ControlCode"],
                "custom_qr_code": qr_code,
            },
            update_modified=True,
        )
        '''If you decide to go with the custom_delivery_note_no field, uncomment the code below'''
        # invoice_name=frappe.db.get_value("Sales Invoice",{"custom_delivery_note_no":invoice},"name")
        # frappe.db.set_value(
        #     "Sales Invoice",
        #     invoice_name,
        #     {
        #         "custom_cu_invoice_number": invoice_info["ControlCode"],
        #         "custom_qr_code": qr_code,
        #     },
        #     update_modified=True,
        # )

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

def validate_relevant_invoice_number(relevant_invoice_number):
    if len(relevant_invoice_number) != 19:
        frappe.throw(
            "The <b>Relevant Invoice Number</b> must be exactly 19 characters long and should be the CU number. Current length: {}.".format(len(relevant_invoice_number))
        )

@frappe.whitelist()
def single_invoice_submission(doc):
    doc_name = json.loads(doc).get("name")
    doc = frappe.get_doc("Sales Invoice", doc_name)
    on_submit(doc)
    frappe.msgprint("TIMS submission successful")
    
