from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import ParseResult, urlparse

import requests
from frappe import _
from frappe.integrations.doctype.integration_request.integration_request import (
	IntegrationRequest,
)
from frappe.integrations.utils import create_request_log

HTTPMethod = Literal["GET", "POST"]

SUPPORTED_HTTP_METHODS: tuple[str, ...] = ("GET", "POST")
ALLOWED_URL_SCHEMES: tuple[str, ...] = ("http", "https")

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300

MAX_LOGGED_RESPONSE_CHARS = 2000

DEFAULT_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

SENSITIVE_HEADERS = frozenset({"authorization", "x-api-key", "proxy-authorization", "cookie"})
REDACTED = "********"

ErrorExtractor = Callable[[dict], str | None]


@dataclass(eq=False)
class RemoteRequestError(Exception):
	message: str
	status_code: int | None = None
	response_body: str | None = None
	integration_request: str | None = None

	def __str__(self) -> str:
		return self.message


@dataclass
class APIConnector:
	"""Builds and executes a single logged HTTP request."""

	base_url: str | None = None
	endpoint: str | None = None
	http_method: HTTPMethod = "POST"
	headers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADERS))
	payload: dict | None = None
	params: dict | None = None
	timeout: int = DEFAULT_TIMEOUT_SECONDS
	verify_tls: bool = True
	service_name: str = "TIMS"
	reference_doctype: str | None = None
	reference_docname: str | None = None
	error_extractor: ErrorExtractor | None = None

	def set_base_url(self, url: str | None) -> APIConnector:
		self.base_url = (url or "").strip() or None
		return self

	def set_endpoint(self, endpoint: str | None) -> APIConnector:
		self.endpoint = (endpoint or "").strip() or None
		return self

	def set_http_method(self, method: HTTPMethod) -> APIConnector:
		self.http_method = (method or "").strip().upper()
		return self

	def set_headers(self, headers: dict[str, str] | None = None) -> APIConnector:
		"""Replace the headers, always keeping the JSON defaults."""
		self.headers = {**DEFAULT_HEADERS, **(headers or {})}
		return self

	def add_header(self, key: str, value: str) -> APIConnector:
		self.headers[key] = value
		return self

	def set_payload(self, payload: dict | None) -> APIConnector:
		self.payload = payload
		return self

	def set_params(self, params: dict | None) -> APIConnector:
		self.params = params
		return self

	def set_timeout(self, timeout: int | None) -> APIConnector:
		"""Clamp the request timeout so a socket can never block a worker indefinitely."""
		try:
			timeout = int(timeout)
		except (TypeError, ValueError):
			timeout = DEFAULT_TIMEOUT_SECONDS

		self.timeout = min(max(timeout, 1), MAX_TIMEOUT_SECONDS)
		return self

	def set_verify_tls(self, verify: bool) -> APIConnector:
		self.verify_tls = bool(verify)
		return self

	def set_service_name(self, service_name: str) -> APIConnector:
		self.service_name = service_name
		return self

	def set_reference(self, doctype: str | None, docname: str | None) -> APIConnector:
		"""Link the Integration Request back to the document that triggered it."""
		self.reference_doctype = doctype
		self.reference_docname = docname
		return self

	def set_error_extractor(self, extractor: ErrorExtractor | None) -> APIConnector:
		"""Treat a response body the extractor flags as a failed request."""
		self.error_extractor = extractor
		return self

	@property
	def loggable_headers(self) -> dict[str, str]:
		"""The request headers with secrets masked, safe to persist."""
		return {
			key: REDACTED if key.lower() in SENSITIVE_HEADERS else value
			for key, value in self.headers.items()
		}

	@property
	def absolute_url(self) -> str:
		"""Resolve and validate the URL this connector will call."""
		if not self.base_url or not self.endpoint:
			raise RemoteRequestError(_("The remote URL has not been fully configured"))

		base = self._validate_url(self.base_url)

		if self.endpoint.startswith(("http://", "https://")):
			endpoint = self._validate_url(self.endpoint)
			if (endpoint.scheme, endpoint.netloc) != (base.scheme, base.netloc):
				raise RemoteRequestError(
					_("Refusing to call {0}: it does not belong to the configured host {1}").format(
						self.endpoint, base.netloc
					)
				)
			return self.endpoint

		return f"{self.base_url.rstrip('/')}/{self.endpoint.lstrip('/')}"

	@staticmethod
	def _validate_url(url: str) -> ParseResult:
		parsed = urlparse(url)

		if parsed.scheme not in ALLOWED_URL_SCHEMES:
			raise RemoteRequestError(
				_("Unsupported URL scheme {0}. Only http and https are allowed.").format(parsed.scheme or url)
			)

		if not parsed.hostname:
			raise RemoteRequestError(_("The URL {0} is missing a host").format(url))

		if parsed.username or parsed.password:
			raise RemoteRequestError(_("Credentials must not be embedded in the server address"))

		return parsed

	def make_remote_call(self) -> dict:
		if self.http_method not in SUPPORTED_HTTP_METHODS:
			raise RemoteRequestError(_("Unsupported HTTP method {0}").format(self.http_method))

		url = self.absolute_url

		integration_request: IntegrationRequest = create_request_log(
			data=self.payload or self.params or {},
			is_remote_request=1,
			service_name=self.service_name,
			request_headers=self.loggable_headers,
			url=url,
			reference_doctype=self.reference_doctype,
			reference_docname=self.reference_docname,
		)

		try:
			response = requests.request(
				method=self.http_method,
				url=url,
				headers=self.headers,
				json=self.payload,
				params=self.params,
				timeout=self.timeout,
				verify=self.verify_tls if url.startswith("https://") else True,
				allow_redirects=False,
			)
			response.raise_for_status()

		except requests.exceptions.Timeout as error:
			self._fail(
				integration_request,
				_("Request to {0} timed out after {1} seconds").format(url, self.timeout),
				error=error,
			)

		except requests.exceptions.ConnectionError as error:
			self._fail(
				integration_request,
				_("Could not connect to {0}").format(url),
				error=error,
			)

		except requests.exceptions.HTTPError as error:
			message = _("{0} returned HTTP {1}").format(url, response.status_code)
			if remote_error := self.extract_error(self.parse_response(response)):
				message = f"{message}: {remote_error}"

			self._fail(
				integration_request,
				message,
				error=error,
				status_code=response.status_code,
				response_body=response.text,
			)

		except requests.exceptions.RequestException as error:
			self._fail(integration_request, _("Request to {0} failed").format(url), error=error)

		data = self.parse_response(response)

		if remote_error := self.extract_error(data):
			self._fail(
				integration_request,
				remote_error,
				status_code=response.status_code,
				response_body=response.text,
			)

		integration_request.handle_success(data)

		return data

	def extract_error(self, data: dict) -> str | None:
		return self.error_extractor(data) if self.error_extractor and data else None

	@staticmethod
	def _fail(
		integration_request: IntegrationRequest,
		message: str,
		error: Exception | None = None,
		status_code: int | None = None,
		response_body: str | None = None,
	) -> None:
		"""Mark the Integration Request failed and raise a uniform error."""
		details = {"message": message}
		if status_code:
			details["status_code"] = status_code
		if response_body:
			details["response"] = response_body[:MAX_LOGGED_RESPONSE_CHARS]
		elif error is not None:
			details["error"] = str(error)

		integration_request.handle_failure(details)

		raise RemoteRequestError(
			message,
			status_code=status_code,
			response_body=response_body,
			integration_request=integration_request.name,
		)

	@staticmethod
	def parse_response(response: requests.Response) -> dict:
		"""Decode the JSON body, tolerating an empty or non-JSON response."""
		try:
			decoded = response.json()
		except ValueError:
			return {}

		return decoded if isinstance(decoded, dict) else {"data": decoded}
