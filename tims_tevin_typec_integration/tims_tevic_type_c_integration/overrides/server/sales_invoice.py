import re

import requests

import frappe
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from erpnext.controllers.taxes_and_totals import get_itemised_tax_breakup_data


def on_submit(doc: Document, method: str | None = None) -> None:
    # TODO: Handle exemptions
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

        item_details = []
        if doc.tax_category == "Exempt":
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

            for item in doc.items:
                tax_details = list(
                    filter(lambda i: i["item"] == item.item_code, item_taxes)
                )[0]

                item_details.append(
                    {
                        "HSDesc": item.description,
                        "TaxRate": tax_details["VAT"]["tax_rate"],
                        "ItemAmount": abs(tax_details["taxable_amount"]),
                        "TaxAmount": abs(tax_details["VAT"]["tax_amount"]),
                        "TransactionType": "1",
                        "UnitPrice": item.base_rate,
                        "HSCode": hs_code,
                        "Quantity": abs(item.qty),
                    }
                )

        payload = {
            "Invoice": {
                "SenderId": setting.sender_id,
                "TraderSystemInvoiceNumber": doc.name,
                "InvoiceCategory": invoice_category,
                "InvoiceTimestamp": f"{doc.posting_date}T{doc.posting_time.split('.', 1)[0]}",
                "RelevantInvoiceNumber": (
                    frappe.db.get_value(
                        "Sales Invoice",
                        {"name": doc.return_against},
                        ["custom_cu_invoice_number"],
                    )
                    if doc.is_return
                    else ""
                ),
                "PINOfBuyer": doc.tax_id,
                "Discount": 0,
                "InvoiceType": "Original",
                "TotalInvoiceAmount": abs(doc.grand_total),
                "TotalTaxableAmount": abs(doc.total),
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
        integration_request = create_request_log(
            data=payload,
            is_remote_request=True,
            service_name="TIMS",
            request_headers=None,
            url=setting.server_address,
            reference_docname=doc.name,
            reference_doctype="Sales Invoice",
        )

        # ! Confirm address before making request to not post to live
        response = requests.post(url=setting.server_address, json=payload, timeout=300)

        if response:
            print(response.status_code, response)

        frappe.throw(f"Testing Exception: {payload}")


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
