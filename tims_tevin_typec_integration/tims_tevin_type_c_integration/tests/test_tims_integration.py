from __future__ import annotations

from datetime import time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import UnitTestCase

from ..api.api_connector import REDACTED, APIConnector, RemoteRequestError
from ..api.tims_api import API_KEY_HEADER, extract_tims_error
from ..services import eod_service, invoice_service
from ..utils import (
	format_posting_time,
	get_hs_description,
	get_trader_invoice_number,
	is_valid_kra_pin,
	kra_round,
)

SERVICES = "tims_tevin_typec_integration.tims_tevin_type_c_integration.services"
CONNECTOR = "tims_tevin_typec_integration.tims_tevin_type_c_integration.api.api_connector"
NOTIFICATION = "tims_tevin_typec_integration.tims_tevin_type_c_integration.notification.notify_of_failed_tims_requests.notify_of_failed_tims_requests"

CONTROL_CODE = "9990000100000000568"
QR_CODE = f"https://itax.kra.go.ke/KRA-Portal/invoiceChk.htm?actionCode=loadPage&invoiceNo={CONTROL_CODE}"


def patch_db(**return_values):
	"""Replace ``frappe.db`` outright: the proxy is unbound without a site connection."""
	db = MagicMock(**{f"{method}.return_value": value for method, value in return_values.items()})
	return patch.object(frappe, "db", db)


def make_settings(**overrides) -> frappe._dict:
	return frappe._dict(
		name="TIMS-0001",
		sender_id="342a53b4948b0551c6b9",
		server_address="http://192.168.1.101:3000/api",
		company="Test Company",
		cash_customer="Cash Customer",
		**overrides,
	)


class FakeDocument(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


def make_item(name: str, idx: int, net_amount: float, qty: float, **overrides) -> frappe._dict:
	values = dict(
		name=name,
		idx=idx,
		item_code=f"ITEM-{idx}",
		item_name=f"Item {idx}",
		description=f"<p>Item {idx}</p>",
		base_net_amount=net_amount,
		base_net_rate=net_amount / qty,
		qty=qty,
		custom_hs_code="",
		item_tax_template=None,
	)
	values.update(overrides)
	return frappe._dict(values)


def make_invoice(items: list, tax_rows: list, **overrides) -> FakeDocument:
	"""A Sales Invoice shaped dict with a single VAT row and its itemised breakup."""
	values = dict(
		name="ACC-SINV-2026-00001",
		company="Test Company",
		customer="Customer A",
		tax_id="P051356922E",
		posting_date="2026-10-02",
		posting_time=timedelta(hours=9, minutes=5, seconds=7),
		is_return=0,
		is_debit_note=0,
		is_opening="No",
		return_against=None,
		custom_relevant_invoice_number=None,
		custom_cu_invoice_number=None,
		base_total_taxes_and_charges=sum(row.amount for row in tax_rows),
		taxes=[frappe._dict(name="tax-1", category="Total")],
		items=items,
		item_wise_tax_details=tax_rows,
	)
	values.update(overrides)
	return FakeDocument(**values)


def vat_row(item_row: str, rate: float, taxable_amount: float, amount: float | None = None) -> frappe._dict:
	return frappe._dict(
		item_row=item_row,
		tax_row="tax-1",
		rate=rate,
		taxable_amount=taxable_amount,
		amount=taxable_amount * rate / 100 if amount is None else amount,
	)


class TestUtils(UnitTestCase):
	def test_kra_round_matches_the_guide(self):
		self.assertEqual(kra_round(1.23456), 1.23)
		self.assertEqual(kra_round(1.23599), 1.24)
		self.assertEqual(kra_round(1.89883), 1.90)
		self.assertEqual(kra_round(1.99502), 2.00)
		self.assertEqual(kra_round(None), 0.0)
		self.assertEqual(kra_round("97200.735"), 97200.74)

	def test_trader_invoice_number_fits_the_device_limit(self):
		self.assertEqual(get_trader_invoice_number("INV-001"), "INV001")
		self.assertEqual(get_trader_invoice_number("ACC-SINV-2026-00001"), "SINV202600001")
		self.assertEqual(get_trader_invoice_number("ACC-SINV-RET-2026-00001"), "RET202600001")
		self.assertEqual(get_trader_invoice_number("A" * 20), "A" * 15)

		for name in ("ACC-SINV-2026-00001", "SINV/2026/#123456789", "X" * 40):
			number = get_trader_invoice_number(name)
			self.assertLessEqual(len(number), 15)
			self.assertTrue(number.isalnum())

	def test_hs_description_is_sanitised(self):
		self.assertEqual(get_hs_description("COCA-COLA"), "COCA COLA")
		self.assertEqual(get_hs_description("DELL Laptop VOSTRO-51569#16Gb RAM"), "DELL Laptop VOSTRO 5")
		self.assertEqual(get_hs_description("<p></p>", None, "Fallback"), "Fallback")

	def test_format_posting_time(self):
		self.assertEqual(format_posting_time(timedelta(hours=9, minutes=5, seconds=7)), "09:05:07")
		self.assertEqual(format_posting_time(time(23, 59, 1)), "23:59:01")
		self.assertEqual(format_posting_time("9:30"), "09:30:00")
		self.assertEqual(format_posting_time("14:29:00.123456"), "14:29:00")
		self.assertEqual(format_posting_time(None), "00:00:00")

	def test_kra_pin(self):
		self.assertTrue(is_valid_kra_pin("P051356922E"))
		self.assertTrue(is_valid_kra_pin(" a123456789k "))
		self.assertFalse(is_valid_kra_pin("P05135692E"))
		self.assertFalse(is_valid_kra_pin(None))


class TestInvoicePayload(UnitTestCase):
	def test_tax_invoice_payload(self):
		items = [
			make_item("row-1", 1, 155.44, 1),
			make_item("row-2", 2, 440.08345, 1),
		]
		doc = make_invoice(items, [vat_row("row-1", 16, 155.44), vat_row("row-2", 16, 440.08345)])

		payload = invoice_service.build_payload(doc, make_settings())["Invoice"]

		self.assertEqual(payload["SenderId"], "342a53b4948b0551c6b9")
		self.assertEqual(payload["TraderSystemInvoiceNumber"], "SINV202600001")
		self.assertEqual(payload["InvoiceCategory"], "Tax Invoice")
		self.assertEqual(payload["InvoiceTimestamp"], "2026-10-02T09:05:07")
		self.assertEqual(payload["RelevantInvoiceNumber"], "")
		self.assertEqual(payload["PINOfBuyer"], "P051356922E")
		self.assertEqual(payload["InvoiceType"], "Original")

		first, second = payload["ItemDetails"]
		self.assertEqual(first["HSDesc"], "Item 1")
		self.assertEqual(first["ItemAmount"], 155.44)
		self.assertEqual(first["TaxAmount"], 24.87)
		self.assertEqual(first["HSCode"], "")
		self.assertEqual(second["ItemAmount"], 440.08)
		self.assertEqual(second["TaxAmount"], 70.41)

		self.assertEqual(payload["TotalTaxableAmount"], 595.52)
		self.assertEqual(payload["TotalTaxAmount"], 95.28)
		self.assertEqual(payload["TotalInvoiceAmount"], 690.80)

	def test_line_tax_is_recomputed_per_line(self):
		items = [make_item("row-1", 1, 925.9259259, 5.638567804, custom_hs_code="0001.12.00")]
		doc = make_invoice(items, [vat_row("row-1", 8, 925.9259259, amount=74.08)])

		line = invoice_service.build_payload(doc, make_settings())["Invoice"]["ItemDetails"][0]

		self.assertEqual(line["ItemAmount"], 925.93)
		self.assertEqual(line["Quantity"], 5.64)
		self.assertEqual(line["TaxAmount"], 74.07)
		self.assertEqual(line["HSCode"], "0001.12.00")

	def test_non_standard_rate_requires_hs_code(self):
		for rate in (0, 8):
			items = [make_item("row-1", 1, 100, 1)]
			doc = make_invoice(items, [vat_row("row-1", rate, 100)])

			with self.assertRaises(frappe.ValidationError):
				invoice_service.build_payload(doc, make_settings())

	def test_zero_rated_invoice_without_tax_rows(self):
		items = [make_item("row-1", 1, 100, 2, custom_hs_code="0001.11.00")]
		doc = make_invoice(items, [], base_total_taxes_and_charges=0)

		payload = invoice_service.build_payload(doc, make_settings())["Invoice"]

		self.assertEqual(payload["ItemDetails"][0]["TaxRate"], 0)
		self.assertEqual(payload["TotalTaxAmount"], 0)
		self.assertEqual(payload["TotalInvoiceAmount"], 100)

	def test_taxed_invoice_without_breakup_is_refused(self):
		items = [make_item("row-1", 1, 100, 1)]
		doc = make_invoice(items, [], base_total_taxes_and_charges=16)

		with self.assertRaises(frappe.ValidationError):
			invoice_service.build_payload(doc, make_settings())

	def test_credit_note_uses_original_cu_number_and_positive_values(self):
		items = [make_item("row-1", 1, -100, -1)]
		doc = make_invoice(
			items,
			[vat_row("row-1", 16, -100)],
			name="ACC-SINV-RET-2026-00001",
			is_return=1,
			return_against="ACC-SINV-2026-00001",
		)

		with patch_db(get_value=CONTROL_CODE):
			payload = invoice_service.build_payload(doc, make_settings())["Invoice"]

		self.assertEqual(payload["InvoiceCategory"], "Credit Note")
		self.assertEqual(payload["RelevantInvoiceNumber"], CONTROL_CODE)
		self.assertEqual(payload["ItemDetails"][0]["ItemAmount"], 100)
		self.assertEqual(payload["ItemDetails"][0]["Quantity"], 1)
		self.assertEqual(payload["TotalInvoiceAmount"], 116)

	def test_debit_note_is_categorised(self):
		items = [make_item("row-1", 1, 50, 1)]
		doc = make_invoice(
			items, [vat_row("row-1", 16, 50)], is_debit_note=1, return_against="ACC-SINV-2026-00001"
		)

		with patch_db(get_value=CONTROL_CODE):
			payload = invoice_service.build_payload(doc, make_settings())["Invoice"]

		self.assertEqual(payload["InvoiceCategory"], "Debit Note")
		self.assertEqual(payload["RelevantInvoiceNumber"], CONTROL_CODE)

	def test_credit_note_without_valid_cu_number_is_not_sent_but_not_blocked(self):
		cases = [
			dict(custom_relevant_invoice_number="123"),
			dict(custom_relevant_invoice_number=None),
			dict(return_against="ACC-SINV-2026-00001"),
		]
		for overrides in cases:
			items = [make_item("row-1", 1, -100, -1)]
			doc = make_invoice(items, [vat_row("row-1", 16, -100)], is_return=1, **overrides)

			with (
				patch_db(get_value=None),
				patch.object(frappe, "msgprint") as msgprint,
			):
				self.assertIsNone(invoice_service.build_payload(doc, make_settings()))

			msgprint.assert_called_once()

	def test_credit_note_falls_back_to_keyed_in_cu_number(self):
		items = [make_item("row-1", 1, -100, -1)]
		doc = make_invoice(
			items,
			[vat_row("row-1", 16, -100)],
			is_return=1,
			return_against="ACC-SINV-2026-00001",
			custom_relevant_invoice_number=CONTROL_CODE,
		)

		with patch_db(get_value=None):
			payload = invoice_service.build_payload(doc, make_settings())

		self.assertEqual(payload["Invoice"]["RelevantInvoiceNumber"], CONTROL_CODE)

	def test_unsendable_note_is_not_queued(self):
		items = [make_item("row-1", 1, -100, -1)]
		doc = make_invoice(items, [vat_row("row-1", 16, -100)], is_return=1)

		with (
			patch(f"{SERVICES}.invoice_service.get_settings", return_value=make_settings()),
			patch.object(frappe, "msgprint"),
			patch.object(frappe, "enqueue") as enqueue,
		):
			self.assertFalse(invoice_service.submit_invoice(doc))

		enqueue.assert_not_called()

	def test_invoice_is_queued_once(self):
		items = [make_item("row-1", 1, 100, 1)]
		doc = make_invoice(items, [vat_row("row-1", 16, 100)])

		with (
			patch(f"{SERVICES}.invoice_service.get_settings", return_value=make_settings()),
			patch.object(frappe, "enqueue") as enqueue,
		):
			self.assertTrue(invoice_service.submit_invoice(doc))

		kwargs = enqueue.call_args.kwargs
		self.assertEqual(kwargs["job_id"], "tims-submit-ACC-SINV-2026-00001")
		self.assertTrue(kwargs["deduplicate"])
		self.assertTrue(kwargs["enqueue_after_commit"])

	def test_filed_invoice_cannot_be_cancelled(self):
		with self.assertRaises(frappe.ValidationError):
			invoice_service.validate_cancellation(make_invoice([], [], custom_cu_invoice_number=CONTROL_CODE))

		invoice_service.validate_cancellation(make_invoice([], []))

	def test_cancelled_invoice_is_not_sent(self):
		with (
			patch_db(get_value=2),
			patch(f"{SERVICES}.invoice_service.post_invoice") as post_invoice,
		):
			invoice_service.send_payload("ACC-SINV-2026-00001", {}, "TIMS-0001")

		post_invoice.assert_not_called()

	def test_invalid_buyer_pin_is_refused(self):
		items = [make_item("row-1", 1, 100, 1)]
		doc = make_invoice(items, [vat_row("row-1", 16, 100)], tax_id="NOT-A-PIN")

		with self.assertRaises(frappe.ValidationError):
			invoice_service.build_payload(doc, make_settings())

	def test_cash_customer_pin(self):
		doc = make_invoice(
			[], [], customer="Cash Customer", tax_id=None, custom_cash_customer_kra_pin=" A123456789K "
		)
		self.assertEqual(invoice_service.get_buyer_pin(doc, make_settings()), "A123456789K")

	def test_opening_and_stamped_invoices_are_skipped(self):
		self.assertFalse(invoice_service.should_submit(make_invoice([], [], is_opening="Yes")))
		self.assertFalse(invoice_service.should_submit(make_invoice([], [], custom_cu_invoice_number="x")))
		self.assertTrue(invoice_service.should_submit(make_invoice([], [])))


class TestInvoiceResponse(UnitTestCase):
	def test_response_stamps_cu_number_and_qr_code(self):
		for key in ("Invoice", "Existing"):
			response = {"AnswerTo": "Invoice", key: {"ControlCode": CONTROL_CODE, "QRCode": QR_CODE}}

			with patch_db() as db:
				invoice_service.apply_response("ACC-SINV-2026-00001", response)

			values = db.set_value.call_args.args[2]
			self.assertEqual(values["custom_cu_invoice_number"], CONTROL_CODE)
			self.assertTrue(values["custom_qr_code"].startswith("data:image/png;base64,"))

	def test_response_without_codes_is_logged_not_stamped(self):
		with (
			patch_db() as db,
			patch(f"{SERVICES}.invoice_service.log_error") as log_error,
		):
			invoice_service.apply_response("ACC-SINV-2026-00001", {"Invoice": {}})

		db.set_value.assert_not_called()
		log_error.assert_called_once()

	def test_failed_submission_does_not_raise(self):
		with (
			patch_db(get_value=1),
			patch.object(frappe, "get_cached_doc", return_value=make_settings()),
			patch(f"{SERVICES}.invoice_service.post_invoice", side_effect=RemoteRequestError("boom")),
			patch(f"{SERVICES}.invoice_service.apply_response") as apply_response,
			patch(f"{SERVICES}.invoice_service.log_error") as log_error,
		):
			invoice_service.send_payload("ACC-SINV-2026-00001", {}, "TIMS-0001")

		apply_response.assert_not_called()
		log_error.assert_called_once()


def mock_response(status_code: int = 200, body: dict | None = None) -> MagicMock:
	response = MagicMock(spec=requests.Response)
	response.status_code = status_code
	response.text = frappe.as_json(body or {})
	response.json.return_value = body or {}
	if status_code >= 400:
		response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
	return response


class TestAPIConnector(UnitTestCase):
	def make_connector(self) -> APIConnector:
		return (
			APIConnector()
			.set_base_url("http://192.168.1.101:3000/api")
			.set_endpoint("/invoice")
			.set_payload({"Invoice": {}})
			.add_header(API_KEY_HEADER, "secret-key")
			.set_error_extractor(extract_tims_error)
		)

	def call(self, response: MagicMock, connector: APIConnector | None = None):
		integration_request = MagicMock(name="Integration Request")
		integration_request.name = "IR-0001"

		with (
			patch(f"{CONNECTOR}.create_request_log", return_value=integration_request) as create_log,
			patch(f"{CONNECTOR}.requests.request", return_value=response) as request,
		):
			try:
				result = (connector or self.make_connector()).make_remote_call()
			except RemoteRequestError as error:
				result = error

		return result, integration_request, create_log, request

	def test_success(self):
		body = {"Invoice": {"ControlCode": CONTROL_CODE}}
		result, integration_request, create_log, request = self.call(mock_response(body=body))

		self.assertEqual(result, body)
		integration_request.handle_success.assert_called_once_with(body)
		self.assertEqual(request.call_args.kwargs["url"], "http://192.168.1.101:3000/api/invoice")
		self.assertEqual(request.call_args.kwargs["headers"][API_KEY_HEADER], "secret-key")
		self.assertEqual(create_log.call_args.kwargs["request_headers"][API_KEY_HEADER], REDACTED)

	def test_error_envelope_on_http_200_fails_the_request(self):
		body = {"Error": {"code": "INVALID_BUYER_PIN", "message": "Buyer PIN is not valid."}}
		result, integration_request, *_ = self.call(mock_response(body=body))

		self.assertIsInstance(result, RemoteRequestError)
		self.assertIn("INVALID_BUYER_PIN", str(result))
		integration_request.handle_failure.assert_called_once()
		integration_request.handle_success.assert_not_called()

	def test_http_error_includes_device_error(self):
		body = {"Error": {"code": "HSCODE_NOT_FOUND", "message": "0001.99.00"}}
		result, integration_request, *_ = self.call(mock_response(400, body))

		self.assertIsInstance(result, RemoteRequestError)
		self.assertEqual(result.status_code, 400)
		self.assertIn("HSCODE_NOT_FOUND", str(result))
		integration_request.handle_failure.assert_called_once()

	def test_timeout_fails_the_request(self):
		integration_request = MagicMock()
		with (
			patch(f"{CONNECTOR}.create_request_log", return_value=integration_request),
			patch(f"{CONNECTOR}.requests.request", side_effect=requests.exceptions.Timeout()),
		):
			with self.assertRaises(RemoteRequestError):
				self.make_connector().make_remote_call()

		integration_request.handle_failure.assert_called_once()

	def test_endpoint_cannot_redirect_to_another_host(self):
		connector = self.make_connector().set_endpoint("http://evil.example.com/invoice")
		with self.assertRaises(RemoteRequestError):
			_ = connector.absolute_url

	def test_unsafe_base_urls_are_refused(self):
		for url in ("ftp://192.168.1.101/api", "http://user:pass@192.168.1.101/api", "http:///api"):
			with self.assertRaises(RemoteRequestError):
				_ = self.make_connector().set_base_url(url).absolute_url

	def test_timeout_is_clamped(self):
		self.assertEqual(APIConnector().set_timeout(0).timeout, 1)
		self.assertEqual(APIConnector().set_timeout(10_000).timeout, 300)
		self.assertEqual(APIConnector().set_timeout(None).timeout, 30)

	def test_extract_tims_error(self):
		self.assertIsNone(extract_tims_error({"Invoice": {}}))
		self.assertEqual(
			extract_tims_error({"Error": {"code": "NO_HSCODE", "message": "Item 1"}}), "NO_HSCODE: Item 1"
		)
		self.assertEqual(
			extract_tims_error({"Error": "FOREIGN KEY constraint failed"}), "FOREIGN KEY constraint failed"
		)


EOD_SUMMARY = {
	"AnswerTo": "ReadEod",
	"EODId": 119,
	"DateOfEODSummary": "2022-03-15",
	"EODTransmissionTimestamp": None,
	"NumberOfFirstInvoice": 527,
	"NumberOfLastInvoice": 530,
	"TotalInvoiceAmountOfTheDay": "97200.73",
	"TotalTaxableAmountOfTheDay": "83793.75",
	"TotalTaxAmountOfTheDay": "13406.98",
	"NumberOfInvoicesSentOfTheDay": "4",
}


class TestEndOfDay(UnitTestCase):
	def test_new_summary_is_inserted(self):
		record = MagicMock()
		with (
			patch_db(exists=None),
			patch.object(frappe, "new_doc", return_value=record),
		):
			eod_service.create_eod_record(EOD_SUMMARY, "Test Company")

		values = record.update.call_args.args[0]
		self.assertEqual(values["end_of_day_id"], "119")
		self.assertEqual(values["total_invoice_amount"], "97200.73")
		self.assertEqual(record.company, "Test Company")
		record.insert.assert_called_once()

	def test_known_summary_is_refreshed(self):
		record = MagicMock()
		summary = {**EOD_SUMMARY, "EODTransmissionTimestamp": "2022-03-16T00:05:00Z"}
		with (
			patch_db(exists="119"),
			patch.object(frappe, "get_doc", return_value=record),
		):
			eod_service.create_eod_record(summary, "Test Company")

		self.assertEqual(record.update.call_args.args[0]["transmission_timestamp"], "2022-03-16T00:05:00Z")
		record.save.assert_called_once()

	def test_summary_without_id_is_logged(self):
		with (
			patch(f"{SERVICES}.eod_service.log_error") as log_error,
			patch.object(frappe, "new_doc") as new_doc,
		):
			eod_service.create_eod_record({"AnswerTo": "ReadEod"}, "Test Company")

		log_error.assert_called_once()
		new_doc.assert_not_called()


class TestSettingsServerAddress(UnitTestCase):
	def normalise(self, address: str) -> str:
		from ..doctype.tims_settings.tims_settings import TIMSSettings

		settings = frappe._dict(server_address=address)
		TIMSSettings.validate_server_address(settings)
		return settings.server_address

	def test_address_is_normalised_to_the_api_root(self):
		self.assertEqual(self.normalise("192.168.1.101:3000"), "http://192.168.1.101:3000/api")
		self.assertEqual(self.normalise("https://tims.local:3000/api/"), "https://tims.local:3000/api")

	def test_unsafe_addresses_are_refused(self):
		for address in (
			"ftp://192.168.1.101",
			"http://user:pw@192.168.1.101",
			"http://192.168.1.101/api?x=1",
		):
			with self.assertRaises(frappe.ValidationError):
				self.normalise(address)


class TestResendTask(UnitTestCase):
	def test_unsendable_invoices_do_not_use_up_the_batch(self):
		from ..tasks import tasks

		settings = frappe._dict(name="TIMS-0001", company="Test Company", resend_batch_size=2)
		candidates = ["NOTE-1", "NOTE-2", "NOTE-3", "INV-1", "INV-2", "INV-3"]
		sendable = {"INV-1", "INV-2", "INV-3"}

		with (
			patch.object(frappe, "get_all", return_value=[settings]),
			patch.object(tasks, "get_invoices_to_resend", return_value=candidates),
			patch.object(frappe, "get_doc", side_effect=lambda doctype, name: name),
			patch.object(tasks, "submit_invoice", side_effect=lambda name: name in sendable) as submit,
		):
			tasks.resend_invoices()

		submitted = [call.args[0] for call in submit.call_args_list]
		self.assertEqual(submitted, ["NOTE-1", "NOTE-2", "NOTE-3", "INV-1", "INV-2"])


class TestFailureNotification(UnitTestCase):
	def get_context(self, **values):
		from ..notification.notify_of_failed_tims_requests.notify_of_failed_tims_requests import get_context

		doc = frappe._dict(
			doctype="Integration Request", name="IR-0001", reference_doctype=None, reference_docname=None
		)
		doc.update(values)
		with patch(
			f"{NOTIFICATION}.get_url_to_form", side_effect=lambda doctype, name: f"/app/{doctype}/{name}"
		):
			return get_context(frappe._dict(doc=doc))

	def test_device_error_is_readable(self):
		error = frappe.as_json(
			{"message": "INVALID_BUYER_PIN: Buyer PIN is not valid.", "status_code": 200, "response": "{}"}
		)
		context = self.get_context(error=error, reference_doctype="Sales Invoice", reference_docname="SINV-1")

		self.assertEqual(context["error_message"], "INVALID_BUYER_PIN: Buyer PIN is not valid.")
		self.assertEqual(context["status_code"], 200)
		self.assertEqual(context["reference_url"], "/app/Sales Invoice/SINV-1")
		self.assertEqual(context["request_url"], "/app/Integration Request/IR-0001")

	def test_unparsable_error_is_shown_as_is(self):
		context = self.get_context(error="Connection refused")

		self.assertEqual(context["error_message"], "Connection refused")
		self.assertIsNone(context["reference_url"])
