import frappe
import frappe.defaults
from frappe.model.document import Document
from frappe.utils import flt
from erpnext.selling.doctype.customer.customer import (
    get_credit_limit,
    get_customer_outstanding,
)


def validate(doc: Document, method: str | None = None) -> None:
    if doc.custom_sales_type == "Credit":
        # Only perform credit check for credit customers
        company = frappe.defaults.get_user_default("Company")

        doc.custom_credit_check = (
            "PASS" if check_credit_limit(doc.customer, company) else "FAIL"
        )

        # Update Outstanding Limit and Credit Limit custom fields of sales order
        doc.custom_outstanding_balance = get_customer_outstanding(customer, company)
        doc.custom_credit_limit = get_credit_limit(customer, company)


def check_credit_limit(
    customer, company, ignore_outstanding_sales_order=False, extra_amount=0
):
    credit_limit = get_credit_limit(customer, company)
    if not credit_limit:
        return True

    customer_outstanding = get_customer_outstanding(
        customer, company, ignore_outstanding_sales_order
    )
    if extra_amount > 0:
        customer_outstanding += flt(extra_amount)

    if credit_limit > 0 and flt(customer_outstanding) > credit_limit:
        return False

    return True
