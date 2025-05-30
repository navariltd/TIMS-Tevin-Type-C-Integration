
import frappe
def calculate_tax(doc):
    tax_rate = items_tax_fields(doc)
    for item in doc.items:
        tax = 0
        if tax_rate:
            tax = item.net_amount * tax_rate / 100
        item.custom_tax_amount = tax
        item.custom_tax_rate = tax_rate
    
    else:
        return 0
    
def items_tax_fields(doc):
    taxes_template = doc.taxes_and_charges
    tax_template = frappe.get_doc("Sales Taxes and Charges Template", taxes_template)
    if tax_template.taxes:
        return tax_template.taxes[0].rate
    else:
        return None
    
def before_save(doc, method=None):
    calculate_tax(doc)
    
def before_save_sales_invoice(doc, method=None):
    if doc.is_return==1:
        calculate_tax(doc)
    

def get_hs_code_before_save(doc):
    for item in doc.items:
        hs_code = get_hs_code_item_tax(item.item_code, item.item_tax_template)
        item.custom_hs_code = hs_code or ""
        

def get_hs_code_item_tax(item_code, item_tax_template_name=None):
    """
    Retrieve HS Code from Item Tax child table by checking:
    1. If item has Item Tax row matching the provided item_tax_template.
    2. If not, fallback to any Item Tax for the item.
    3. If not found, fallback to Item Group's Item Tax row.
    """
    hs_code = ""

    if item_tax_template_name:
        tax_row = frappe.db.get_value(
            "Item Tax",
            {"item_tax_template": item_tax_template_name},
            ["tims_hscode"]
        )
        if tax_row:
            return tax_row

    # Fallback: Get first Item Tax record for item
    item_tax = frappe.get_all("Item Tax", filters={"parent": item_code}, fields=["tims_hscode"], limit=1)
    if item_tax and item_tax[0].tims_hscode:
        return item_tax[0].tims_hscode

    # Fallback: Get item group tax template
    item_doc = frappe.get_doc("Item", item_code)
    item_group = item_doc.item_group
    group_tax = frappe.get_all("Item Tax", filters={"parent": item_group}, fields=["tims_hscode"], limit=1)
    if group_tax and group_tax[0].tims_hscode:
        return group_tax[0].tims_hscode

    return hs_code
