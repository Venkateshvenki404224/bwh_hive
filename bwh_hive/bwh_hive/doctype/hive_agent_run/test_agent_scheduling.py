# Copyright (c) 2026, BWH Studios and contributors
# For license information, please see license.txt

"""Tests for scheduled agent dispatch.

The guards are the point of this file. A wrong cap here costs real money, so
each one is tested against real run rows rather than a mocked counter.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from bwh_hive.bwh_hive import agent_runner

MODEL = "us.anthropic.claude-sonnet-5"


class TestAgentScheduling(IntegrationTestCase):
	def setUp(self):
		# Committed rows escape IntegrationTestCase's rollback, so every
		# committed fixture is deleted explicitly. LIFO: commit is registered
		# first so it runs last, after the deletes.
		self.addCleanup(frappe.db.commit)

		self.project = frappe.get_doc(
			{"doctype": "Hive Project", "title": frappe.generate_hash("proj", 8), "status": "Open"}
		).insert()
		self.agent = frappe.get_doc(
			{
				"doctype": "Hive Agent",
				"agent_name": frappe.generate_hash("agent", 8),
				"executor_type": "Bedrock Direct",
				"model": MODEL,
				"max_tokens": 2048,
				"is_scheduled": 1,
				"schedule_interval": "Hourly",
				"task_status_filter": "To Do",
				"max_runs_per_hour": 10,
				"project": self.project.name,
			}
		).insert()

		# The ceiling is a Single: a test that changes it would otherwise leak
		# into every later test, which is exactly what happened the first time.
		self.original_ceiling = frappe.db.get_single_value("Hive Settings", "max_agent_tokens_per_day")
		self.addCleanup(self._restore_ceiling)
		self.addCleanup(self._delete_fixtures)
		frappe.db.commit()

	def _restore_ceiling(self):
		frappe.db.set_single_value("Hive Settings", "max_agent_tokens_per_day", self.original_ceiling)

	def _delete_fixtures(self):
		for run in frappe.get_all("Hive Agent Run", filters={"agent": self.agent.name}, pluck="name"):
			frappe.delete_doc("Hive Agent Run", run, force=True, ignore_permissions=True)
		for comment in frappe.get_all(
			"Hive Task Comment", filters={"task": ("in", self._task_names())}, pluck="name"
		):
			frappe.delete_doc("Hive Task Comment", comment, force=True, ignore_permissions=True)
		for task in self._task_names():
			frappe.delete_doc("Hive Task", task, force=True, ignore_permissions=True)
		for agent in frappe.get_all("Hive Agent", filters={"project": self.project.name}, pluck="name"):
			frappe.delete_doc("Hive Agent", agent, force=True, ignore_permissions=True)
		if frappe.db.exists("Hive Agent", self.agent.name):
			frappe.delete_doc("Hive Agent", self.agent.name, force=True, ignore_permissions=True)
		if frappe.db.exists("Hive Project", self.project.name):
			frappe.delete_doc("Hive Project", self.project.name, force=True, ignore_permissions=True)

	def _task_names(self):
		return frappe.get_all("Hive Task", filters={"project": self.project.name}, pluck="name")

	def _task(self, status="To Do"):
		return frappe.get_doc(
			{
				"doctype": "Hive Task",
				"title": frappe.generate_hash("task", 8),
				"project": self.project.name,
				"status": status,
				"description": "Work this.",
			}
		).insert()

	def _run(self, task, status="Done", tokens=0, created=None):
		run = frappe.get_doc(
			{
				"doctype": "Hive Agent Run",
				"task": task,
				"agent": self.agent.name,
				"model": MODEL,
				"status": status,
				"total_tokens": tokens,
				"finished_at": now_datetime(),
			}
		).insert()
		if created:
			frappe.db.set_value("Hive Agent Run", run.name, "creation", created, update_modified=False)
		return run

	# -- one task per tick -------------------------------------------------

	def test_dispatch_queues_at_most_one_task(self):
		"""177 eligible tasks must never become 177 calls."""
		for _ in range(5):
			self._task()
		frappe.db.commit()

		with patch.object(frappe, "enqueue"):
			result = agent_runner.dispatch_for_agent(self.agent.name)

		self.assertEqual(len(result["queued"]), 1)
		self.assertIsNone(result["blocked"])

	# -- hourly cap --------------------------------------------------------

	def test_hourly_cap_blocks_and_records_the_reason(self):
		task = self._task()
		for _ in range(10):
			self._run(task.name, status="Done")
		frappe.db.commit()

		with patch.object(frappe, "enqueue") as enqueue:
			result = agent_runner.dispatch_for_agent(self.agent.name)
			enqueue.assert_not_called()

		self.assertIn("cap of 10", result["blocked"])
		blocked = frappe.get_all(
			"Hive Agent Run",
			filters={"agent": self.agent.name, "status": "Blocked"},
			fields=["error"],
		)
		self.assertTrue(blocked)
		self.assertIn("cap of 10", blocked[0]["error"])

	def test_runs_older_than_an_hour_do_not_count(self):
		task = self._task()
		old = add_to_date(now_datetime(), hours=-2)
		for _ in range(10):
			self._run(task.name, status="Done", created=old)
		frappe.db.commit()

		with patch.object(frappe, "enqueue"):
			result = agent_runner.dispatch_for_agent(self.agent.name)

		self.assertIsNone(result["blocked"])

	def test_zero_cap_disables_the_hourly_guard(self):
		self.agent.db_set("max_runs_per_hour", 0)
		task = self._task()
		for _ in range(50):
			self._run(task.name, status="Done")
		frappe.db.commit()

		with patch.object(frappe, "enqueue"):
			result = agent_runner.dispatch_for_agent(self.agent.name)

		self.assertIsNone(result["blocked"])

	# -- daily token ceiling ----------------------------------------------

	def test_daily_token_ceiling_blocks(self):
		frappe.db.set_single_value("Hive Settings", "max_agent_tokens_per_day", 1000)
		task = self._task()
		self._run(task.name, status="Done", tokens=1200)
		frappe.db.commit()

		with patch.object(frappe, "enqueue") as enqueue:
			result = agent_runner.dispatch_for_agent(self.agent.name)
			enqueue.assert_not_called()

		self.assertIn("daily ceiling", result["blocked"])

	def test_token_ceiling_counts_real_rows(self):
		frappe.db.set_single_value("Hive Settings", "max_agent_tokens_per_day", 5000)
		task = self._task()
		self._run(task.name, status="Done", tokens=700)
		self._run(task.name, status="Done", tokens=300)
		frappe.db.commit()

		spent, ceiling = agent_runner.tokens_used_today()
		self.assertGreaterEqual(spent, 1000)
		self.assertEqual(ceiling, 5000)

	def test_zero_ceiling_disables_the_budget_guard(self):
		frappe.db.set_single_value("Hive Settings", "max_agent_tokens_per_day", 0)
		task = self._task()
		self._run(task.name, status="Done", tokens=10_000_000)
		frappe.db.commit()

		with patch.object(frappe, "enqueue"):
			result = agent_runner.dispatch_for_agent(self.agent.name)

		self.assertIsNone(result["blocked"])

	# -- re-run protection -------------------------------------------------

	def test_task_with_a_done_run_is_not_reworked(self):
		task = self._task()
		self._run(task.name, status="Done")
		frappe.db.commit()

		with patch.object(frappe, "enqueue"):
			result = agent_runner.dispatch_for_agent(self.agent.name)

		self.assertEqual(result["queued"], [])

	def test_task_modified_after_its_run_is_reworked(self):
		task = self._task()
		self._run(task.name, status="Done")
		frappe.db.set_value(
			"Hive Task", task.name, "modified", add_to_date(now_datetime(), hours=1), update_modified=False
		)
		frappe.db.commit()

		with patch.object(frappe, "enqueue"):
			result = agent_runner.dispatch_for_agent(self.agent.name)

		self.assertEqual(result["queued"] and len(result["queued"]), 1)

	def test_task_with_an_inflight_run_is_skipped(self):
		task = self._task()
		self._run(task.name, status="Running")
		frappe.db.commit()

		with patch.object(frappe, "enqueue"):
			result = agent_runner.dispatch_for_agent(self.agent.name)

		self.assertEqual(result["queued"], [])

	# -- tick selection ----------------------------------------------------

	def test_tick_only_touches_agents_on_that_interval(self):
		"""Scoped to this agent: other tests' agents may also be scheduled."""
		self._task()
		frappe.db.commit()

		with patch.object(agent_runner, "dispatch_for_agent") as dispatch:
			agent_runner.run_daily_agents()
			daily_calls = [c for c in dispatch.call_args_list if c.args[0] == self.agent.name]
			self.assertEqual(daily_calls, [])

			agent_runner.run_hourly_agents()
			dispatch.assert_any_call(self.agent.name)

	def test_unscheduled_agent_is_never_ticked(self):
		self.agent.db_set("is_scheduled", 0)
		self._task()
		frappe.db.commit()

		with patch.object(agent_runner, "dispatch_for_agent") as dispatch:
			agent_runner.run_hourly_agents()

		mine = [c for c in dispatch.call_args_list if c.args[0] == self.agent.name]
		self.assertEqual(mine, [])

	def test_one_broken_agent_does_not_stop_the_others(self):
		other = frappe.get_doc(
			{
				"doctype": "Hive Agent",
				"agent_name": frappe.generate_hash("agent", 8),
				"executor_type": "Bedrock Direct",
				"model": MODEL,
				"max_tokens": 2048,
				"is_scheduled": 1,
				"schedule_interval": "Hourly",
				"project": self.project.name,
			}
		).insert()
		frappe.db.commit()

		seen = []

		def flaky(name):
			seen.append(name)
			if len(seen) == 1:
				raise RuntimeError("first one explodes")
			return {"queued": [], "blocked": None}

		with patch.object(agent_runner, "dispatch_for_agent", side_effect=flaky):
			agent_runner.run_hourly_agents()

		# The first call raising must not prevent later agents being reached.
		self.assertGreaterEqual(len(seen), 2)
		self.assertIn(other.name, seen)
		self.assertIn(self.agent.name, seen)
