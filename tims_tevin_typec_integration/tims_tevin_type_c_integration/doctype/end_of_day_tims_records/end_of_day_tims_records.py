# Copyright (c) 2024, Navari Ltd and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class EndOfDayTIMSRecords(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		company: DF.Link | None
		date_of_summary: DF.Date | None
		end_of_day_id: DF.Data | None
		first_invoice_number: DF.Int
		last_invoice_number: DF.Int
		number_of_invoices_sent: DF.Int
		total_invoice_amount: DF.Float
		total_tax_amount: DF.Float
		total_taxable_amount: DF.Float
		transmission_timestamp: DF.Data | None
	# end: auto-generated types

	pass
