
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
    # Fetch the Sales Taxes and Charges Template
    tax_template = frappe.get_doc("Sales Taxes and Charges Template", taxes_template)
    if tax_template.taxes:
        return tax_template.taxes[0].rate
    else:
        return None
    
def before_save(doc, method=None):
    calculate_tax(doc)
    
