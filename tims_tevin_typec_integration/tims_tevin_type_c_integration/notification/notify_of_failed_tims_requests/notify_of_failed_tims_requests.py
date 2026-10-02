import json

from frappe.utils import get_url_to_form


def get_context(context):
	doc = context.get("doc")
	if not doc:
		return {}

	details = parse_error(doc.get("error"))

	return {
		"error_message": details.get("message") or doc.get("error") or "",
		"status_code": details.get("status_code"),
		"device_response": details.get("response") or details.get("error"),
		"request_url": get_url_to_form(doc.doctype, doc.name),
		"reference_url": get_url_to_form(doc.reference_doctype, doc.reference_docname)
		if doc.get("reference_doctype") and doc.get("reference_docname")
		else None,
	}


def parse_error(error: str | None) -> dict:
	try:
		details = json.loads(error or "{}")
	except ValueError:
		return {}

	return details if isinstance(details, dict) else {}
