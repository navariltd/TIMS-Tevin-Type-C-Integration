import frappe
from frappe.model.rename_doc import get_link_fields

OLD_MODULE = "TIMS Tevic Type-C Integration"
NEW_MODULE = "TIMS Tevin Type-C Integration"
APP_NAME = "tims_tevin_typec_integration"


def execute():
	if not frappe.db.exists("Module Def", OLD_MODULE):
		return

	if not frappe.db.exists("Module Def", NEW_MODULE):
		module = frappe.new_doc("Module Def")
		module.module_name = NEW_MODULE
		module.app_name = APP_NAME
		module.db_insert()

	for link in get_link_fields("Module Def"):
		if link.issingle:
			if frappe.db.get_single_value(link.parent, link.fieldname) == OLD_MODULE:
				frappe.db.set_single_value(link.parent, link.fieldname, NEW_MODULE)
			continue

		frappe.db.set_value(
			link.parent, {link.fieldname: OLD_MODULE}, link.fieldname, NEW_MODULE, update_modified=False
		)

	frappe.db.delete("Module Def", OLD_MODULE)
	frappe.clear_cache()
