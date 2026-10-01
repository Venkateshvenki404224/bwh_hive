# Copyright (c) 2026, BWH Studios and contributors
# For license information, please see license.txt

"""Amazon Bedrock connection for the AI agent control plane.

Credentials live on Hive Settings, the secret in a Password field so it is
encrypted at rest. Nothing here returns the secret to a caller.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

# The connection test lists inference profiles rather than invoking a model:
# it needs no model id, costs nothing, and still fails on exactly the
# credential and permission problems a real invoke would hit.
REQUIRED_ACTIONS = (
	"bedrock:ListInferenceProfiles",
	"bedrock:ListFoundationModels",
	"bedrock:InvokeModel",
	"bedrock:InvokeModelWithResponseStream",
)

DEFAULT_REGION = "us-east-1"


def get_credentials() -> dict:
	"""Return stored Bedrock credentials, or throw if they are not set."""
	settings = frappe.get_single("Hive Settings")
	key_id = (settings.aws_access_key_id or "").strip()
	secret = settings.get_password("aws_secret_access_key", raise_exception=False)

	if not key_id or not secret:
		frappe.throw(
			_("Amazon Bedrock credentials are not set. Add them in Settings > AI Agents."),
			title=_("Bedrock Not Configured"),
		)

	return {
		"aws_access_key_id": key_id,
		"aws_secret_access_key": secret,
		"region_name": (settings.aws_region or DEFAULT_REGION).strip(),
	}


def get_client(service: str = "bedrock"):
	"""Build a boto3 client from the stored credentials.

	`bedrock` is the control plane (listing models); `bedrock-runtime` is the
	one that actually invokes them.
	"""
	try:
		import boto3
	except ImportError:
		frappe.throw(
			_("The boto3 package is not installed on this bench. Run: bench pip install boto3"),
			title=_("Missing Dependency"),
		)

	return boto3.client(service, **get_credentials())


@frappe.whitelist()
def status() -> dict:
	"""Report whether Bedrock is configured, without exposing the secret."""
	settings = frappe.get_single("Hive Settings")
	has_secret = bool(settings.get_password("aws_secret_access_key", raise_exception=False))

	return {
		"configured": bool((settings.aws_access_key_id or "").strip() and has_secret),
		"access_key_id": _mask(settings.aws_access_key_id),
		"region": settings.aws_region or DEFAULT_REGION,
		"connected": bool(settings.bedrock_connected),
		"verified_at": settings.bedrock_verified_at,
		"last_error": settings.bedrock_last_error,
		"required_actions": list(REQUIRED_ACTIONS),
	}


@frappe.whitelist(methods=["POST"])
def test_connection() -> dict:
	"""Call Bedrock for real and record the outcome on Hive Settings.

	The real AWS error is stored and returned. A generic "connection failed"
	would leave the user guessing between a typo, a wrong region, and a
	missing IAM action, which are three different fixes.
	"""
	frappe.has_permission("Hive Settings", "write", throw=True)

	# Built before the try block: "no credentials entered" is a different
	# problem from "AWS rejected these credentials", and must not be recorded
	# as a failed connection test.
	client = get_client("bedrock")

	settings = frappe.get_single("Hive Settings")
	try:
		profiles = client.list_inference_profiles(maxResults=5)
		count = len(profiles.get("inferenceProfileSummaries", []))
	except Exception as exc:
		_record_result(settings, connected=False, error=_describe(exc))
		return {"connected": False, "error": settings.bedrock_last_error}

	_record_result(settings, connected=True, error=None)
	return {
		"connected": True,
		"verified_at": settings.bedrock_verified_at,
		"inference_profiles_visible": count,
	}


@frappe.whitelist(methods=["POST"])
def disconnect() -> dict:
	"""Forget the stored credentials."""
	frappe.has_permission("Hive Settings", "write", throw=True)

	settings = frappe.get_single("Hive Settings")
	settings.aws_access_key_id = None
	settings.aws_secret_access_key = None
	settings.bedrock_connected = 0
	settings.bedrock_verified_at = None
	settings.bedrock_last_error = None
	settings.save(ignore_permissions=True)

	return {"disconnected": True}


def _record_result(settings, connected: bool, error: str | None) -> None:
	settings.bedrock_connected = 1 if connected else 0
	settings.bedrock_verified_at = now_datetime() if connected else None
	settings.bedrock_last_error = error
	settings.save(ignore_permissions=True)


def _describe(exc: Exception) -> str:
	"""Turn a botocore exception into something a human can act on."""
	response = getattr(exc, "response", None)
	if isinstance(response, dict):
		error = response.get("Error", {})
		code = error.get("Code") or exc.__class__.__name__
		message = error.get("Message") or str(exc)
		return f"{code}: {message}"
	return f"{exc.__class__.__name__}: {exc}"


def _mask(key_id: str | None) -> str | None:
	"""Show enough of the key id to recognise it, not enough to use it."""
	key_id = (key_id or "").strip()
	if not key_id:
		return None
	return key_id[:4] + "•" * max(len(key_id) - 8, 0) + key_id[-4:]
