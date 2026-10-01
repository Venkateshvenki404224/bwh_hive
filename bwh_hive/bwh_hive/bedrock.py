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

# Cross-region inference profiles are the only way to invoke the current
# Anthropic models: list_foundation_models reports them as
# inferenceTypesSupported == ["INFERENCE_PROFILE"], so the bare
# `anthropic.claude-opus-5` id fails at invoke time while
# `us.anthropic.claude-opus-5` works. The catalogue therefore lists profiles,
# not foundation models, and an agent stores a profile id.
MODEL_CACHE_KEY = "bwh_hive:bedrock_models"
MODEL_CACHE_TTL = 60 * 60 * 6


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


@frappe.whitelist()
def list_models(refresh: bool = False) -> dict:
	"""Return the inference profiles this account can actually invoke.

	Profiles, not foundation models: the current Anthropic models are
	INFERENCE_PROFILE-only, so a picker built from foundation-model ids would
	offer ids that fail at invoke time.

	Cached because the list changes rarely and every call is a live AWS
	round-trip. `refresh=True` is the explicit escape hatch.
	"""
	if isinstance(refresh, str):
		refresh = refresh.lower() in ("1", "true", "yes")

	if not refresh:
		cached = frappe.cache().get_value(MODEL_CACHE_KEY)
		if cached:
			return {**cached, "cached": True}

	profiles = _fetch_inference_profiles()
	payload = {
		"models": profiles,
		"region": frappe.db.get_single_value("Hive Settings", "aws_region") or DEFAULT_REGION,
		"fetched_at": str(now_datetime()),
	}
	frappe.cache().set_value(MODEL_CACHE_KEY, payload, expires_in_sec=MODEL_CACHE_TTL)
	return {**payload, "cached": False}


def _fetch_inference_profiles() -> list[dict]:
	client = get_client("bedrock")

	summaries, token = [], None
	while True:
		kwargs = {"maxResults": 100}
		if token:
			kwargs["nextToken"] = token
		page = client.list_inference_profiles(**kwargs)
		summaries.extend(page.get("inferenceProfileSummaries", []))
		token = page.get("nextToken")
		if not token:
			break

	models = [
		{
			"id": summary.get("inferenceProfileId"),
			"name": summary.get("inferenceProfileName"),
			"provider": _provider_of(summary.get("inferenceProfileId", "")),
			"scope": _scope_of(summary.get("inferenceProfileId", "")),
			"type": summary.get("type"),
		}
		for summary in summaries
		if summary.get("status") == "ACTIVE" and summary.get("inferenceProfileId")
	]
	models.sort(key=lambda model: (model["provider"], model["id"]))
	return models


def _provider_of(profile_id: str) -> str:
	"""`us.anthropic.claude-opus-5` -> `anthropic`."""
	parts = profile_id.split(".")
	return parts[1] if len(parts) > 2 else (parts[0] if parts else "unknown")


def _scope_of(profile_id: str) -> str:
	"""The routing prefix: `us`, `eu`, `global`, and so on."""
	prefix = profile_id.split(".")[0]
	return prefix if prefix in ("us", "eu", "apac", "au", "jp", "in", "global") else "other"


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
