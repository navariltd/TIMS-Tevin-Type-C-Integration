frappe.ui.form.on("Sales Invoice", {
  validate: function (frm) {
    const taxCategory = frm.doc.tax_category;
    const customer = frm.doc.customer;

    if (!!!taxCategory) {
      frappe.throw(`Please select the Customer ${customer}'s Tax Category`);
    }
  },
  refresh: function (frm) {
    if(frm.doc.docstatus === 1 && !frm.doc.custom_cu_invoice_number){
      frm.add_custom_button(
      __("Submit"),
      function () {
        

        frappe.call({
          method: "tims_tevin_typec_integration.tims_tevic_type_c_integration.overrides.server.sales_invoice.single_invoice_submission",
          args: {
            doc: frm.doc,
          },
          callback: function (response) {
            if (response.message) {
             console.log()
            }
          },
        });
      },
      __("eTims Actions")
    );
  }
  },
});

