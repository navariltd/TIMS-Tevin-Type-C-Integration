frappe.ui.form.on("Sales Invoice", {
	validate: function (frm) {
		if (!frm.doc.tax_category) {
			frappe.throw(__("Please select the Customer {0}'s Tax Category", [frm.doc.customer]));
		}
	},
	refresh: function (frm) {
		if (frm.doc.docstatus === 1 && !frm.doc.custom_cu_invoice_number) {
			frm.add_custom_button(
				__("Submit to TIMS"),
				function () {
					frappe.call({
						method: "tims_tevin_typec_integration.tims_tevin_type_c_integration.overrides.server.sales_invoice.resubmit_to_tims",
						args: {
							invoice: frm.doc.name,
						},
						freeze: true,
						callback: function (response) {
							if (response.message) {
								frappe.show_alert({
									message: response.message,
									indicator: "green",
								});
							}
						},
					});
				},
				__("TIMS Actions")
			);
		}
	},
});
