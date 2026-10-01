# Copyright (c) 2026, BWH Studios and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class HiveAgentRun(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		agent: DF.Link
		comment: DF.Link | None
		error: DF.SmallText | None
		finished_at: DF.Datetime | None
		input_tokens: DF.Int
		latency_ms: DF.Int
		model: DF.Data | None
		output: DF.LongText | None
		output_tokens: DF.Int
		started_at: DF.Datetime | None
		status: DF.Literal["Queued", "Running", "Done", "Failed"]
		stop_reason: DF.Data | None
		task: DF.Link
		total_tokens: DF.Int
	# end: auto-generated types

	pass
