# Copyright (c) 2026, BWH Studios and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

MAX_OUTPUT_TOKENS = 64000

# Claude 5-era models spend output budget on internal reasoning before emitting
# text. Verified against the live account: a 1172-char task description with
# maxTokens=300 returns 300 output tokens and an empty response. A floor well
# above that stops an agent being configured into guaranteed empty runs.
MIN_OUTPUT_TOKENS = 1024

# Anything that is not a cross-region inference profile id fails at invoke
# time rather than at save time, which is a much worse place to find out.
PROFILE_PREFIXES = ("us", "eu", "apac", "au", "jp", "in", "global")


class HiveAgent(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		agent_name: DF.Data
		can_write: DF.Check
		executor_type: DF.Literal["Bedrock Direct"]
		is_active: DF.Check
		max_tokens: DF.Int
		model: DF.Data
		project: DF.Link | None
		system_prompt: DF.SmallText | None
		temperature: DF.Float
	# end: auto-generated types

	def validate(self):
		self._validate_model()
		self._validate_limits()

	def _validate_model(self):
		"""Reject a model id this account cannot actually invoke.

		Checked here rather than left to run time: a bad id in an agent row
		turns into a failed run on a real task, often on a schedule, where the
		cause is far less obvious than a save-time error.
		"""
		model = (self.model or "").strip()
		if not model:
			frappe.throw(_("Model is required."), title=_("Model Missing"))
		self.model = model

		if model.split(".")[0] not in PROFILE_PREFIXES:
			frappe.throw(
				_(
					"{0} is not an inference profile id. Use a region-prefixed id such as "
					"us.anthropic.claude-sonnet-5 — the bare model id cannot be invoked."
				).format(model),
				title=_("Invalid Model"),
			)

	def _validate_limits(self):
		if self.max_tokens is not None and not MIN_OUTPUT_TOKENS <= self.max_tokens <= MAX_OUTPUT_TOKENS:
			frappe.throw(
				_(
					"Max output tokens must be between {0} and {1}. Below {0} a Claude 5-era "
					"model can spend the whole budget reasoning and return no text at all."
				).format(MIN_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS),
				title=_("Invalid Token Limit"),
			)

		if self.temperature is not None and not 0 <= self.temperature <= 1:
			frappe.throw(_("Temperature must be between 0 and 1."), title=_("Invalid Temperature"))
