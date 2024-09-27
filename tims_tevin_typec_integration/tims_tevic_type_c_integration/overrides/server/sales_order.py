import frappe
import frappe.defaults
from frappe.model.document import Document
from frappe.model.workflow import apply_workflow
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


def on_submit(doc: Document, method: str | None = None) -> None:
    if doc.custom_sales_type == "Cash":
        if doc.custom_cost_validation_status == "FAIL":
            cost_approval = apply_workflow(doc, "Send for Cost Approval")

            if cost_approval:
                apply_workflow(doc, "Approve")

        else:
            apply_workflow(doc, "Approve")

    else:
        if doc.custom_cost_validation_status == "FAIL":
            cost_approval = apply_workflow(doc, "Send for Cost Approval")

            if cost_approval:
                if doc.custom_credit_check == "FAIL":
                    apply_workflow(doc, "Request Credit Limit Approval")

                else:
                    apply_workflow(doc, "Approve")

        else:
            if doc.custom_credit_check == "FAIL":
                credit_approval = apply_workflow(doc, "Request Credit Limit Approval")

                if credit_approval:
                    apply_workflow(doc, "Approve")

            else:
                apply_workflow(doc, "Approve")


def check_credit_limit(
    customer, company, ignore_outstanding_sales_order=False, extra_amount=0
):
    credit_limit = get_credit_limit(customer, company)
    if not credit_limit:
        return

    customer_outstanding = get_customer_outstanding(
        customer, company, ignore_outstanding_sales_order
    )
    if extra_amount > 0:
        customer_outstanding += flt(extra_amount)

    if credit_limit > 0 and flt(customer_outstanding) > credit_limit:
        return False

    return True
