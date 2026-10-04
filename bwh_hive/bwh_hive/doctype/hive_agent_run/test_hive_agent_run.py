# Copyright (c) 2026, BWH Studios and contributors
# For license information, please see license.txt

"""Tests for the agent runner.

These do not call Bedrock. The live model path is verified separately against
the real account; what matters here is the behaviour around the call: the run
row's lifecycle, the guards before queueing, and that a failure is recorded
rather than swallowed.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from bwh_hive.bwh_hive import agent_runner

MODEL = "us.anthropic.claude-sonnet-5"

FAKE_RESULT = {
	"text": "Here is the analysis.",
	"stop_reason": "end_turn",
	"input_tokens": 120,
	"output_tokens": 45,
	"total_tokens": 165,
	"latency_ms": 1234,
}


class TestAgentRunner(IntegrationTestCase):
	def setUp(self):
		self.project = frappe.get_doc(
			{"doctype": "Hive Project", "title": frappe.generate_hash("proj", 8), "status": "Open"}
		).insert()
		self.task = frappe.get_doc(
			{
				"doctype": "Hive Task",
				"title": "Agent runner test task",
				"project": self.project.name,
				"status": "To Do",
				"description": "Something for the agent to read.",
			}
		).insert()
		self.agent = frappe.get_doc(
			{
				"doctype": "Hive Agent",
				"agent_name": frappe.generate_hash("agent", 8),
				"executor_type": "Bedrock Direct",
				"model": MODEL,
				"max_tokens": 2048,
			}
		).insert()
		frappe.db.commit()

	def test_queue_creates_row_before_the_job_runs(self):
		"""A worker that never starts must be visible as a stuck row."""
		with patch.object(frappe, "enqueue") as enqueue:
			result = agent_runner.run_agent_on_task(self.task.name, self.agent.name)
			enqueue.assert_called_once()

		run = frappe.get_doc("Hive Agent Run", result["run"])
		self.assertEqual(run.status, "Queued")
		self.assertEqual(run.task, self.task.name)
		self.assertEqual(run.model, MODEL)

	def test_successful_run_records_usage_and_posts_a_comment(self):
		with patch.object(frappe, "enqueue"):
			name = agent_runner.run_agent_on_task(self.task.name, self.agent.name)["run"]

		with patch.object(agent_runner.bedrock, "converse", return_value=FAKE_RESULT):
			agent_runner.execute_run(name)

		run = frappe.get_doc("Hive Agent Run", name)
		self.assertEqual(run.status, "Done")
		self.assertEqual(run.total_tokens, 165)
		self.assertEqual(run.latency_ms, 1234)
		self.assertTrue(run.comment)
		self.assertEqual(frappe.db.get_value("Hive Task Comment", run.comment, "task"), self.task.name)

	def test_failure_is_recorded_not_swallowed(self):
		with patch.object(frappe, "enqueue"):
			name = agent_runner.run_agent_on_task(self.task.name, self.agent.name)["run"]

		with patch.object(agent_runner.bedrock, "converse", side_effect=RuntimeError("boom")):
			agent_runner.execute_run(name)

		run = frappe.get_doc("Hive Agent Run", name)
		self.assertEqual(run.status, "Failed")
		self.assertIn("boom", run.error)
		self.assertFalse(run.comment)

	def test_empty_output_at_token_limit_names_the_limit(self):
		"""A 5-era model can spend the whole budget reasoning and emit nothing."""
		with patch.object(frappe, "enqueue"):
			name = agent_runner.run_agent_on_task(self.task.name, self.agent.name)["run"]

		starved = {**FAKE_RESULT, "text": "", "stop_reason": "max_tokens"}
		with patch.object(agent_runner.bedrock, "converse", return_value=starved):
			agent_runner.execute_run(name)

		run = frappe.get_doc("Hive Agent Run", name)
		self.assertEqual(run.status, "Failed")
		self.assertIn("token output limit", run.error)

	def test_redelivery_does_not_double_post(self):
		with patch.object(frappe, "enqueue"):
			name = agent_runner.run_agent_on_task(self.task.name, self.agent.name)["run"]

		with patch.object(agent_runner.bedrock, "converse", return_value=FAKE_RESULT):
			agent_runner.execute_run(name)
			before = frappe.db.count("Hive Task Comment", {"task": self.task.name})
			agent_runner.execute_run(name)
			after = frappe.db.count("Hive Task Comment", {"task": self.task.name})

		self.assertEqual(before, after)

	def test_inactive_agent_is_refused(self):
		self.agent.db_set("is_active", 0)
		with self.assertRaises(frappe.ValidationError):
			agent_runner.run_agent_on_task(self.task.name, self.agent.name)

	def test_project_scoped_agent_refuses_other_projects(self):
		other = frappe.get_doc(
			{"doctype": "Hive Project", "title": frappe.generate_hash("other", 8), "status": "Open"}
		).insert()
		self.agent.db_set("project", other.name)

		with self.assertRaises(frappe.ValidationError):
			agent_runner.run_agent_on_task(self.task.name, self.agent.name)

	def test_task_status_is_untouched(self):
		"""can_write is off, so the agent must not close its own work."""
		with patch.object(frappe, "enqueue"):
			name = agent_runner.run_agent_on_task(self.task.name, self.agent.name)["run"]

		with patch.object(agent_runner.bedrock, "converse", return_value=FAKE_RESULT):
			agent_runner.execute_run(name)

		self.assertEqual(frappe.db.get_value("Hive Task", self.task.name, "status"), "To Do")

	def test_long_description_is_truncated(self):
		self.task.db_set("description", "x" * (agent_runner.MAX_DESCRIPTION_CHARS + 500))
		prompt = agent_runner._build_prompt(frappe.get_doc("Hive Task", self.task.name))
		self.assertIn("[truncated]", prompt)
