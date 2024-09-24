# Copyright (c) 2024, Navari Ltd and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TIMSSettings(Document):
    # TODO: Provide link to Branch

    def validate(self) -> None:
        if self.server_address:
            if not self.server_address.startswith("http"):
                # WARNING: Prepend http for now as hostname is IP address
                self.server_address = f"http://{self.server_address}"

            if not self.server_address.endswith("/api"):
                self.server_address = f"{self.server_address}/api"
