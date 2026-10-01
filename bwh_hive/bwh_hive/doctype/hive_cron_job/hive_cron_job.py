# Copyright (c) 2026, BWH Studios and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class HiveCronJob(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		deliver_to: DF.Data | None
		enabled: DF.Check
		job_id: DF.Data | None
		job_name: DF.Data
		last_run_at: DF.Datetime | None
		last_status: DF.Literal["", "ok", "error", "never run"]
		model: DF.Data | None
		next_run_at: DF.Datetime | None
		notes: DF.TextEditor | None
		project: DF.Link | None
		schedule: DF.Data | None
	# end: auto-generated types
