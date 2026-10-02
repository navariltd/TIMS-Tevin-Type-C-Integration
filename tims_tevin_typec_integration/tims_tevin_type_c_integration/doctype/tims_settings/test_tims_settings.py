# Copyright (c) 2024, Navari Ltd and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ...api.api_connector import RemoteRequestError
from .tims_settings import reapply_job_frequencies

COMPANY = "_Test Company"
RESEND_METHOD = "tims_tevin_typec_integration.tims_tevin_type_c_integration.tasks.tasks.resend_invoices"
DEVICE_STATUS = "tims_tevin_typec_integration.tims_tevin_type_c_integration.api.tims_api.fetch_device_status"


def make_settings(**overrides):
	values = {
		"doctype": "TIMS Settings",
		"company": COMPANY,
		"sender_id": frappe.generate_hash(length=20),
		"server_address": f"tims-{frappe.generate_hash(length=8)}.local:3000",
		"is_active": 0,
	}
	values.update(overrides)
	return frappe.get_doc(values).insert()


class TestTIMSSettings(IntegrationTestCase):
	def setUp(self):
		frappe.db.set_value("TIMS Settings", {"company": COMPANY}, "is_active", 0)

	def test_server_address_is_normalised(self):
		settings = make_settings(server_address="192.168.1.101:3000/")
		self.assertEqual(settings.server_address, "http://192.168.1.101:3000/api")

	def test_credentials_in_server_address_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			make_settings(server_address="http://user:secret@192.168.1.101:3000")

	def test_one_active_setting_per_company(self):
		make_settings(is_active=1)

		with self.assertRaises(frappe.ValidationError):
			make_settings(is_active=1)

		make_settings(is_active=0)

	def test_job_frequency_follows_settings(self):
		if not frappe.db.exists("Scheduled Job Type", {"method": RESEND_METHOD}):
			self.skipTest("Scheduled jobs have not been synced on this site")

		make_settings(is_active=1, resend_invoices_frequency="Hourly")
		self.assertEqual(
			frappe.db.get_value("Scheduled Job Type", {"method": RESEND_METHOD}, "frequency"),
			"Hourly",
		)

		frappe.db.set_value("Scheduled Job Type", {"method": RESEND_METHOD}, "frequency", "All")
		reapply_job_frequencies()
		self.assertEqual(
			frappe.db.get_value("Scheduled Job Type", {"method": RESEND_METHOD}, "frequency"),
			"Hourly",
		)

	def test_connection_reports_device_status(self):
		settings = make_settings()
		status = {
			"CurrentState": "paired",
			"DeviceNumber": "KRAMW005201908000003",
			"SetupTimeStamp": "x",
		}

		with patch(DEVICE_STATUS, return_value=status):
			result = settings.test_connection()

		self.assertEqual(result["CurrentState"], "paired")
		self.assertNotIn("SetupTimeStamp", result)

	def test_connection_failure_is_shown_to_the_user(self):
		settings = make_settings()

		with patch(DEVICE_STATUS, side_effect=RemoteRequestError("Could not connect")):
			with self.assertRaises(frappe.ValidationError):
				settings.test_connection()
