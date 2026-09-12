"""The TIMS endpoints, expressed on top of :class:`~.api_connector.APIConnector`.

This is the only module that knows the shape of the Tevic Type-C middleware's
URLs. Callers pass a TIMS Settings document and a ready payload; everything to
do with transport, logging and error handling stays in the connector.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .api_connector import APIConnector

if TYPE_CHECKING:
	from ..doctype.tims_settings.tims_settings import TIMSSettings

SERVICE_NAME = "TIMS"

INVOICE_ENDPOINT = "/invoice"
EOD_ENDPOINT = "/eod"

API_KEY_HEADER = "X-API-Key"


def build_connector(settings: TIMSSettings) -> APIConnector:
	"""Pre-configure a connector from the company's TIMS Settings."""
	connector = (
		APIConnector()
		.set_service_name(SERVICE_NAME)
		.set_base_url(settings.server_address)
		.set_timeout(settings.request_timeout)
		.set_verify_tls(settings.verify_tls)
	)

	if api_key := settings.get_password("api_key", raise_exception=False):
		connector.add_header(API_KEY_HEADER, api_key)

	return connector


def post_invoice(settings: TIMSSettings, payload: dict, reference_docname: str) -> dict:
	"""Send an invoice or credit note to the TIMS device."""
	return (
		build_connector(settings)
		.set_http_method("POST")
		.set_endpoint(INVOICE_ENDPOINT)
		.set_payload(payload)
		.set_reference("Sales Invoice", reference_docname)
		.make_remote_call()
	)


def fetch_eod_summary(settings: TIMSSettings) -> dict:
	"""Fetch the device's end-of-day summary for the configured sender."""
	return (
		build_connector(settings)
		.set_http_method("GET")
		.set_endpoint(f"{EOD_ENDPOINT}/{settings.sender_id}")
		.set_reference("TIMS Settings", settings.name)
		.make_remote_call()
	)
