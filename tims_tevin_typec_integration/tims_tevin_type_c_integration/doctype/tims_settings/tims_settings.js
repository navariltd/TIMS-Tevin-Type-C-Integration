// Copyright (c) 2024, Navari Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("TIMS Settings", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Test Connection"), () => {
			frm.call({ method: "test_connection", doc: frm.doc, freeze: true }).then(
				({ message }) => {
					if (!message) return;

					frappe.msgprint({
						title: __("TIMS device reachable"),
						indicator: message.CurrentState === "paired" ? "green" : "orange",
						message: Object.entries(message)
							.map(
								([key, value]) =>
									`<b>${frappe.utils.escape_html(
										key
									)}</b>: ${frappe.utils.escape_html(String(value ?? ""))}`
							)
							.join("<br>"),
					});
				}
			);
		});
	},
});
