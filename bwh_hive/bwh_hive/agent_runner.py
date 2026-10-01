# Copyright (c) 2026, BWH Studios and contributors
# For license information, please see license.txt

"""Run a Hive Agent against a Hive Task.

The run row is created before the job is enqueued, and completion is proved by
reading back the comment the worker produced rather than by the worker
declaring itself finished. A worker that writes its own success row inherits
the exact unreliability class it was meant to detect: if it dies between
producing the artifact and marking itself done, a self-reported scheme either
loses the work or claims work it never did.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from bwh_hive.bwh_hive import bedrock

MAX_DESCRIPTION_CHARS = 6000
QUEUE_TIMEOUT = 600


@frappe.whitelist(methods=["POST"])
def run_agent_on_task(task: str, agent: str) -> dict:
	"""Queue an agent run and return the run row immediately.

	The row exists before the job is queued, so a worker that never starts is
	visible as a stuck Queued row rather than as nothing at all.
	"""
	frappe.has_permission("Hive Task", "write", doc=task, throw=True)

	agent_doc = frappe.get_doc("Hive Agent", agent)
	if not agent_doc.is_active:
		frappe.throw(_("{0} is not active.").format(agent), title=_("Agent Inactive"))

	if agent_doc.project:
		task_project = frappe.db.get_value("Hive Task", task, "project")
		if task_project != agent_doc.project:
			frappe.throw(
				_("{0} is limited to project {1}, and this task is in {2}.").format(
					agent, agent_doc.project, task_project
				),
				title=_("Wrong Project"),
			)

	run = frappe.get_doc(
		{
			"doctype": "Hive Agent Run",
			"task": task,
			"agent": agent,
			"model": agent_doc.model,
			"status": "Queued",
		}
	)
	run.insert()
	# Committed before enqueue so the worker can read the row the moment it
	# picks the job up. enqueue_after_commit is deliberately NOT used: the row
	# is already durable here, and deferring the push to a later commit hook
	# meant the job was silently never queued at all.
	frappe.db.commit()

	frappe.enqueue(
		"bwh_hive.bwh_hive.agent_runner.execute_run",
		queue="long",
		timeout=QUEUE_TIMEOUT,
		run_name=run.name,
	)

	return {"run": run.name, "status": run.status}


def execute_run(run_name: str) -> None:
	"""Worker entry point. Never raises: a failure is a Failed row."""
	run = frappe.get_doc("Hive Agent Run", run_name)
	if run.status != "Queued":
		# Re-delivery of an already-handled job must not double-post.
		return

	run.db_set({"status": "Running", "started_at": now_datetime()}, commit=True)

	try:
		agent = frappe.get_doc("Hive Agent", run.agent)
		task = frappe.get_doc("Hive Task", run.task)

		result = bedrock.converse(
			model=agent.model,
			prompt=_build_prompt(task),
			system_prompt=agent.system_prompt or None,
			max_tokens=agent.max_tokens or 2048,
			temperature=agent.temperature,
		)

		if not result["text"]:
			# Claude 5-era models can spend the whole budget on internal
			# reasoning and emit nothing. Verified: a 1172-char task
			# description with maxTokens=300 returns 300 output tokens and
			# zero text. Name the cause, since "no text" alone sends the
			# reader looking at the prompt instead of the limit.
			if result["stop_reason"] == "max_tokens":
				raise ValueError(
					f"the model hit its {agent.max_tokens}-token output limit before "
					f"producing any text. Raise Max Output Tokens on agent {agent.name} "
					f"(1500+ is a safe floor for a task of this size)."
				)
			raise ValueError(f"the model returned no text (stop reason: {result['stop_reason']})")

		comment = frappe.get_doc(
			{
				"doctype": "Hive Task Comment",
				"task": run.task,
				"content": _format_comment(agent, result),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		# Read the comment back before calling the run done. The row is only
		# marked Done on the strength of an artifact that is really there.
		if not frappe.db.exists("Hive Task Comment", comment.name):
			raise RuntimeError("the comment could not be read back after insert")

		run.db_set(
			{
				"status": "Done",
				"finished_at": now_datetime(),
				"output": result["text"],
				"stop_reason": result["stop_reason"],
				"input_tokens": result["input_tokens"],
				"output_tokens": result["output_tokens"],
				"total_tokens": result["total_tokens"],
				"latency_ms": result["latency_ms"],
				"comment": comment.name,
			},
			commit=True,
		)
	except Exception as exc:
		frappe.db.rollback()
		frappe.log_error(title=f"Hive agent run {run_name} failed")
		frappe.get_doc("Hive Agent Run", run_name).db_set(
			{
				"status": "Failed",
				"finished_at": now_datetime(),
				"error": f"{exc.__class__.__name__}: {exc}"[:1000],
			},
			commit=True,
		)


def _build_prompt(task) -> str:
	description = (task.description or "").strip()
	if len(description) > MAX_DESCRIPTION_CHARS:
		description = description[:MAX_DESCRIPTION_CHARS] + "\n[truncated]"

	return (
		f"Task: {task.title}\n"
		f"Status: {task.status}\n"
		f"Priority: {task.priority or 'unset'}\n\n"
		f"Description:\n{description or '(none)'}\n\n"
		"Work this task. Be specific and concrete. If the task cannot be completed "
		"from this information alone, say exactly what is missing rather than "
		"guessing."
	)


def _format_comment(agent, result: dict) -> str:
	"""Attribute the output to the agent, with its real usage figures.

	The usage line is there so an unattended run's cost is visible on the task
	itself, not only in the run record.
	"""
	return (
		f"<p><b>{frappe.utils.escape_html(agent.agent_name)}</b> "
		f"<i>({frappe.utils.escape_html(agent.model)})</i></p>"
		f"<p>{frappe.utils.escape_html(result['text']).replace(chr(10), '<br>')}</p>"
		f"<p><small>{result['input_tokens']} in / {result['output_tokens']} out tokens"
		f" · {result['latency_ms']} ms · stop: {result['stop_reason']}</small></p>"
	)
