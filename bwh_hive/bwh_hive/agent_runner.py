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
from frappe.utils import add_to_date, get_datetime, now_datetime, today

from bwh_hive.bwh_hive import bedrock

MAX_DESCRIPTION_CHARS = 6000
QUEUE_TIMEOUT = 600

# One task per tick, always. There are 177 To Do tasks on this site today;
# a batch loop is one bad filter away from a 177-call burst, so throughput
# comes from tick frequency, which is visible and controllable, rather than
# from batch size, which is not.
TASKS_PER_TICK = 1


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


# -- scheduling -----------------------------------------------------------


def run_hourly_agents() -> None:
	_tick("Hourly")


def run_daily_agents() -> None:
	_tick("Daily")


def _tick(interval: str) -> None:
	"""Give every scheduled agent on this interval at most one task."""
	agents = frappe.get_all(
		"Hive Agent",
		filters={"is_active": 1, "is_scheduled": 1, "schedule_interval": interval},
		pluck="name",
	)
	for name in agents:
		try:
			dispatch_for_agent(name)
		except Exception:
			# One misconfigured agent must not stop the others.
			frappe.log_error(title=f"Hive agent schedule tick failed for {name}")


def dispatch_for_agent(agent_name: str) -> dict:
	"""Queue at most TASKS_PER_TICK runs for one agent, respecting the guards.

	A blocked tick writes a Blocked run row with the reason. Silence would make
	"the cap stopped it" indistinguishable from "nothing was eligible", which
	are very different things to debug.
	"""
	agent = frappe.get_doc("Hive Agent", agent_name)

	blocked = _guard_reason(agent)
	if blocked:
		_record_blocked(agent, blocked)
		return {"queued": [], "blocked": blocked}

	queued = []
	for task in _eligible_tasks(agent, limit=TASKS_PER_TICK):
		queued.append(run_agent_on_task(task, agent.name)["run"])

	return {"queued": queued, "blocked": None}


def _guard_reason(agent) -> str | None:
	"""Why this agent must not run right now, or None."""
	hourly_cap = agent.max_runs_per_hour or 0
	if hourly_cap > 0:
		recent = frappe.db.count(
			"Hive Agent Run",
			{
				"agent": agent.name,
				"status": ("in", ("Queued", "Running", "Done", "Failed")),
				"creation": (">", add_to_date(now_datetime(), hours=-1)),
			},
		)
		if recent >= hourly_cap:
			return f"{recent} runs in the last hour reaches the cap of {hourly_cap}"

	spent, ceiling = tokens_used_today()
	if ceiling > 0 and spent >= ceiling:
		return f"{spent} tokens used today reaches the daily ceiling of {ceiling}"

	return None


def tokens_used_today() -> tuple[int, int]:
	"""Real tokens consumed today across every agent, and the configured ceiling.

	Summed from run rows rather than tracked in a counter: a counter drifts from
	reality the first time a run is deleted or a worker dies mid-write.
	"""
	spent = (
		frappe.db.sql(
			"""select coalesce(sum(total_tokens), 0)
			from `tabHive Agent Run`
			where date(creation) = %s""",
			(today(),),
		)[0][0]
		or 0
	)
	ceiling = frappe.db.get_single_value("Hive Settings", "max_agent_tokens_per_day") or 0
	return int(spent), int(ceiling)


def _eligible_tasks(agent, limit: int) -> list[str]:
	"""Tasks this agent should work, newest-updated first.

	A task that already has a Done run is skipped unless it was modified after
	that run finished. Without that, a scheduled agent re-answers the same
	ticket every tick and buries it in duplicate comments.
	"""
	filters = {
		"status": agent.task_status_filter or "To Do",
		"is_archived": 0,
	}
	if agent.project:
		filters["project"] = agent.project

	candidates = frappe.get_all(
		"Hive Task",
		filters=filters,
		fields=["name", "modified"],
		order_by="modified desc",
		limit=limit * 20,
	)

	eligible = []
	for task in candidates:
		last_done = frappe.db.get_value(
			"Hive Agent Run",
			{"task": task.name, "agent": agent.name, "status": "Done"},
			"finished_at",
			order_by="finished_at desc",
		)
		if last_done and get_datetime(last_done) >= get_datetime(task.modified):
			continue
		if frappe.db.exists(
			"Hive Agent Run",
			{"task": task.name, "agent": agent.name, "status": ("in", ("Queued", "Running"))},
		):
			continue
		eligible.append(task.name)
		if len(eligible) >= limit:
			break

	return eligible


def _record_blocked(agent, reason: str) -> None:
	"""Leave a visible trace that a guard fired."""
	task = frappe.db.get_value("Hive Agent Run", {"agent": agent.name}, "task", order_by="creation desc")
	if not task:
		# Nothing to attach it to yet; the log is the only honest record.
		frappe.log_error(title=f"Hive agent {agent.name} blocked", message=reason)
		return

	frappe.get_doc(
		{
			"doctype": "Hive Agent Run",
			"task": task,
			"agent": agent.name,
			"model": agent.model,
			"status": "Blocked",
			"error": reason,
			"finished_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
