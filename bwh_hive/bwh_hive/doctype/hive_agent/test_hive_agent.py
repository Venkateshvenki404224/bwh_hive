# Copyright (c) 2026, BWH Studios and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

VALID_MODEL = "us.anthropic.claude-sonnet-5"


class TestHiveAgent(IntegrationTestCase):
	def _agent(self, **overrides):
		values = {
			"doctype": "Hive Agent",
			"agent_name": frappe.generate_hash("agent", 8),
			"executor_type": "Bedrock Direct",
			"model": VALID_MODEL,
		}
		values.update(overrides)
		return frappe.get_doc(values)

	def test_valid_agent_saves(self):
		agent = self._agent()
		agent.insert()
		self.assertEqual(agent.model, VALID_MODEL)

	def test_can_write_defaults_off(self):
		"""An agent must not be able to close its own work unless asked."""
		agent = self._agent()
		agent.insert()
		self.assertEqual(agent.can_write, 0)

	def test_bare_model_id_is_rejected(self):
		"""The bare id fails at invoke time, so it must fail at save time."""
		agent = self._agent(model="anthropic.claude-opus-5")
		with self.assertRaises(frappe.ValidationError):
			agent.insert()

	def test_model_is_required(self):
		agent = self._agent(model="   ")
		with self.assertRaises(frappe.ValidationError):
			agent.insert()

	def test_model_whitespace_is_stripped(self):
		agent = self._agent(model=f"  {VALID_MODEL}  ")
		agent.insert()
		self.assertEqual(agent.model, VALID_MODEL)

	def test_token_limit_bounds(self):
		with self.assertRaises(frappe.ValidationError):
			self._agent(max_tokens=0).insert()
		with self.assertRaises(frappe.ValidationError):
			self._agent(max_tokens=10_000_000).insert()

	def test_temperature_bounds(self):
		with self.assertRaises(frappe.ValidationError):
			self._agent(temperature=1.5).insert()
		with self.assertRaises(frappe.ValidationError):
			self._agent(temperature=-0.2).insert()

	def test_duplicate_name_is_rejected(self):
		name = frappe.generate_hash("dupe", 8)
		self._agent(agent_name=name).insert()
		with self.assertRaises(frappe.DuplicateEntryError):
			self._agent(agent_name=name).insert()
