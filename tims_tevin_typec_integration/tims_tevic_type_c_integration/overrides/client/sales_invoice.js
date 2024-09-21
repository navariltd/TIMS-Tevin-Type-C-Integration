frappe.ui.form.on("Sales Invoice", {
  validate: function (frm) {
    const taxCategory = frm.doc.tax_category;
    const customer = frm.doc.customer;

    if (!!!taxCategory) {
      frappe.throw(`Please select the Customer ${customer}'s Tax Category`);
    }
  },
});
