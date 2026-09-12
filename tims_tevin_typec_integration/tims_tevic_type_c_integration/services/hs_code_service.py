"""Resolve the KRA HS Code reported for each invoice line."""

from __future__ import annotations

import frappe
from frappe.model.document import Document

#: Custom field contributed by the csf_ke app; absent on sites without it.
HS_CODE_FIELD = "tims_hscode"


def has_hs_code_field() -> bool:
	return frappe.get_meta("Item Tax").has_field(HS_CODE_FIELD)


def set_item_hs_codes(doc: Document) -> None:
	"""Stamp each item row with the HS Code held on its tax masters."""
	if not has_hs_code_field():
		return

	fallback = get_tax_category_hs_code(doc.tax_category)

	for item in doc.items:
		# A code entered by hand is only ever replaced by one from the masters,
		# never cleared.
		item.custom_hs_code = resolve_hs_code(item) or fallback or item.custom_hs_code or ""


def resolve_hs_code(item: Document) -> str | None:
	"""Look for the item's HS Code, widening from the item to its group."""
	if item.item_tax_template:
		hs_code = get_item_tax_hs_code(
			parent=item.item_code, parenttype="Item", item_tax_template=item.item_tax_template
		)
		if hs_code:
			return hs_code

	if hs_code := get_item_tax_hs_code(parent=item.item_code, parenttype="Item"):
		return hs_code

	item_group = frappe.db.get_value("Item", item.item_code, "item_group")

	return get_item_tax_hs_code(parent=item_group, parenttype="Item Group") if item_group else None


def get_item_tax_hs_code(parent: str, parenttype: str, item_tax_template: str | None = None) -> str | None:
	"""Read the HS Code off an Item Tax row belonging to ``parent``."""
	filters = {"parent": parent, "parenttype": parenttype}
	if item_tax_template:
		filters["item_tax_template"] = item_tax_template

	return frappe.db.get_value("Item Tax", filters, HS_CODE_FIELD)


def get_tax_category_hs_code(tax_category: str | None) -> str | None:
	"""Fall back to the HS Code configured against the customer's Tax Category."""
	if not tax_category:
		return None

	return frappe.db.get_value("Tax Category", tax_category, "custom_hs_code")
