# Copyright (c) 2024, Navari Ltd and contributors
# For license information, please see license.txt

from __future__ import annotations

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.email.queue import flush
from frappe.model.document import Document

from ...tasks.tasks import get_eod_records, resend_invoices

ALLOWED_URL_SCHEMES = ("http", "https")
API_PATH_SUFFIX = "/api"

CONFIGURABLE_JOBS = (
	("eod_fetch_frequency", "eod_cron", f"%{get_eod_records.__name__}%"),
	(
		"resend_invoices_frequency",
		"resend_invoices_cron",
		f"%{resend_invoices.__name__}%",
	),
	("flush_email_frequency", "flush_email_cron", f"%email%{flush.__name__}%"),
)


class TIMSSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_key: DF.Password | None
		branch_id: DF.Data | None
		cash_customer: DF.Link | None
		company: DF.Link
		cusn: DF.Data | None
		eod_cron: DF.Data | None
		eod_fetch_frequency: DF.Literal["", "Daily", "Cron"]
		flush_email_cron: DF.Data | None
		flush_email_frequency: DF.Literal["", "All", "Hourly", "Cron"]
		is_active: DF.Check
		request_timeout: DF.Int
		resend_batch_size: DF.Int
		resend_cooldown_minutes: DF.Int
		resend_invoices_cron: DF.Data | None
		resend_invoices_frequency: DF.Literal["", "All", "Hourly", "Daily", "Cron"]
		resend_lookback_days: DF.Int
		sender_id: DF.Data
		server_address: DF.Data
		verify_tls: DF.Check
	# end: auto-generated types

	def validate(self) -> None:
		self.validate_server_address()
		self.validate_one_active_setting_per_company()

	def validate_server_address(self) -> None:
		"""Normalise the device address and reject anything unsafe to call."""
		address = (self.server_address or "").strip().rstrip("/")

		if "://" not in address:
			address = f"http://{address}"

		parsed = urlparse(address)

		if parsed.scheme not in ALLOWED_URL_SCHEMES:
			frappe.throw(
				_("The Server Address must use http or https, not {0}.").format(frappe.bold(parsed.scheme))
			)

		if not parsed.hostname:
			frappe.throw(_("The Server Address is missing a hostname."))

		if parsed.username or parsed.password:
			frappe.throw(
				_("Do not put credentials in the Server Address. Use the {0} field instead.").format(
					frappe.bold(_("API Key"))
				)
			)

		if parsed.query or parsed.fragment:
			frappe.throw(_("The Server Address must not contain a query string or fragment."))

		if not parsed.path.endswith(API_PATH_SUFFIX):
			address = f"{address}{API_PATH_SUFFIX}"

		self.server_address = address

	def validate_one_active_setting_per_company(self) -> None:
		"""Invoices resolve their device by company, so the match must be unique."""
		if not self.is_active:
			return

		existing = frappe.db.get_value(
			"TIMS Settings",
			{"company": self.company, "is_active": 1, "name": ("!=", self.name)},
			"name",
		)

		if existing:
			frappe.throw(
				_("{0} is already the active TIMS Setting for {1}. Deactivate it first.").format(
					frappe.bold(existing), frappe.bold(self.company)
				)
			)

	@frappe.whitelist()
	def test_connection(self) -> dict:
		"""Read the device's status to prove the address and Sender ID work."""
		from ...api.api_connector import RemoteRequestError
		from ...api.tims_api import fetch_device_status

		self.check_permission("write")

		try:
			status = fetch_device_status(self)
		except RemoteRequestError as error:
			frappe.throw(_("Could not reach the TIMS device: {0}").format(error))

		return {
			key: status.get(key)
			for key in (
				"CurrentState",
				"DeviceNumber",
				"MiddlewareType",
				"PINOfSupplier",
				"VATRates",
			)
		}

	def on_update(self) -> None:
		for frequency_field, cron_field, method_pattern in CONFIGURABLE_JOBS:
			frequency = self.get(frequency_field)
			if frequency and self.has_value_changed(frequency_field):
				set_job_frequency(method_pattern, frequency, self.get(cron_field))


def reapply_job_frequencies() -> None:
	"""``after_migrate``: migrate resets every job to its hooks.py frequency."""
	for name in frappe.get_all("TIMS Settings", filters={"is_active": 1}, pluck="name"):
		settings = frappe.get_doc("TIMS Settings", name)
		for frequency_field, cron_field, method_pattern in CONFIGURABLE_JOBS:
			if frequency := settings.get(frequency_field):
				set_job_frequency(method_pattern, frequency, settings.get(cron_field))


def set_job_frequency(method_pattern: str, frequency: str, cron_format: str | None) -> None:
	"""Repoint a Scheduled Job Type at the frequency chosen in these settings."""
	name = frappe.db.get_value("Scheduled Job Type", {"method": ("like", method_pattern)}, "name")

	if not name:
		frappe.log_error(
			title=_("TIMS: Scheduled Job Type not found"),
			message=_("No Scheduled Job Type matches {0}, so its frequency was left unchanged.").format(
				method_pattern
			),
		)
		return

	job = frappe.get_doc("Scheduled Job Type", name)
	job.frequency = frequency

	if frequency == "Cron":
		job.cron_format = cron_format

	job.save(ignore_permissions=True)
