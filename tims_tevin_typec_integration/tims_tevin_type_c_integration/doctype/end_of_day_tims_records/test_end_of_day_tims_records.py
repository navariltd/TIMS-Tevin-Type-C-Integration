# Copyright (c) 2024, Navari Ltd and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from ...services.eod_service import EOD_DOCTYPE, create_eod_record

COMPANY = "_Test Company"


def make_summary(**overrides):
	summary = {
		"AnswerTo": "ReadEod",
		"EODId": 900119,
		"DateOfEODSummary": "2026-10-01",
		"EODTransmissionTimestamp": None,
		"NumberOfFirstInvoice": 527,
		"NumberOfLastInvoice": 530,
		"TotalInvoiceAmountOfTheDay": "97200.73",
		"TotalTaxableAmountOfTheDay": "83793.75",
		"TotalTaxAmountOfTheDay": "13406.98",
		"NumberOfInvoicesSentOfTheDay": "4",
	}
	summary.update(overrides)
	return summary


class TestEndOfDayTIMSRecords(IntegrationTestCase):
	def test_summary_is_recorded(self):
		create_eod_record(make_summary(), COMPANY)

		record = frappe.get_doc(EOD_DOCTYPE, "900119")
		self.assertEqual(record.company, COMPANY)
		self.assertEqual(str(record.date_of_summary), "2026-10-01")
		self.assertEqual(record.first_invoice_number, 527)
		self.assertEqual(record.number_of_invoices_sent, 4)
		self.assertAlmostEqual(record.total_invoice_amount, 97200.73)
		self.assertFalse(record.transmission_timestamp)

	def test_refetched_summary_updates_the_record(self):
		create_eod_record(make_summary(), COMPANY)
		create_eod_record(make_summary(EODTransmissionTimestamp="2026-10-02T00:05:00Z"), COMPANY)

		self.assertEqual(frappe.db.count(EOD_DOCTYPE, {"name": "900119"}), 1)
		self.assertEqual(
			frappe.db.get_value(EOD_DOCTYPE, "900119", "transmission_timestamp"),
			"2026-10-02T00:05:00Z",
		)
