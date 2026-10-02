"""Per-item tax figures, read from ERPNext's itemised tax breakup."""

from __future__ import annotations

from frappe import _, throw
from frappe.model.document import Document
from frappe.utils import flt

VALUATION_CATEGORY = "Valuation"

ZERO_TAX = {"tax_rate": 0.0, "tax_amount": 0.0, "taxable_amount": 0.0}


def get_itemised_tax_details(doc: Document) -> dict[str, dict]:
	tax_rows = {tax.name: tax for tax in doc.get("taxes") or []}
	details: dict[str, dict] = {}

	for row in doc.get("item_wise_tax_details") or []:
		tax_row = tax_rows.get(row.tax_row)
		if tax_row and tax_row.get("category") == VALUATION_CATEGORY:
			continue

		entry = details.setdefault(row.item_row, dict(ZERO_TAX))
		entry["tax_rate"] += flt(row.rate)
		entry["tax_amount"] += flt(row.amount)
		entry["taxable_amount"] = entry["taxable_amount"] or flt(row.taxable_amount)

	return details


def get_item_tax(details: dict[str, dict], item: Document) -> dict:
	"""Return the tax figures for one item row, defaulting to zero-rated."""
	return details.get(item.name) or dict(ZERO_TAX)


def validate_itemised_tax(doc: Document, details: dict[str, dict]) -> None:
	if details or not flt(doc.base_total_taxes_and_charges):
		return

	throw(
		_(
			"The itemised tax breakup for {0} is missing, so its tax cannot be reported to TIMS. "
			"Amend and resubmit the invoice to have ERPNext rebuild the breakup."
		).format(doc.name)
	)
