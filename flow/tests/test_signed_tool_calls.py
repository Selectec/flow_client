# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt

import base64
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from litellm.llms.vertex_ai.gemini.transformation import _transform_request_body

from flow.lib.agent import Agent
from flow.lib.model import Model
from flow.lib.session import load_session
from flow.lib.tool import tool


class TestSignedToolCalls(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_signed_tool_results_survive_session_reload(self):
		self._round_trip()

	def test_streamed_signed_tool_results_survive_session_reload(self):
		self._round_trip(stream=True)

	def test_signed_confirmation_survives_reload_and_resume(self):
		self._round_trip(confirmation=True)

	def test_streamed_signed_confirmation_survives_reload_and_resume(self):
		self._round_trip(stream=True, confirmation=True)

	def test_denied_signed_confirmation_remains_associated(self):
		self._round_trip(confirmation=True, approve=False)

	def test_short_unsigned_tool_results_survive_session_reload(self):
		self._round_trip(signed=False)

	def _round_trip(self, *, stream=False, confirmation=False, signed=True, approve=True):
		signature = base64.b64encode(b"synthetic-signature-" * 512).decode()
		call_id = "call_fixture__thought__" + signature if signed else "call_fixture"
		calls = []

		@tool(requires_confirmation=confirmation)
		def echo(value: str) -> str:
			"""Return a fake test value."""
			calls.append(value)
			return value

		# Block credential lookup and replace only the provider transport. Flow's
		# normalization, agent loop, session persistence and replay all run normally.
		with patch("flow.lib.model.resolve_provider_credentials", return_value={}):
			model = Model(model_id="gemini/gemini-3.8-flash", api_key="fake-test-key")
		agent = Agent(model=model, tools=[echo])
		requests = []

		def completion(**kwargs):
			requests.append(deepcopy(kwargs["messages"]))
			tool_calls = (
				[
					{
						"id": call_id,
						"type": "function",
						"function": {"name": "echo", "arguments": '{"value":"first"}'},
					},
					{
						"id": "call_short",
						"type": "function",
						"function": {"name": "echo", "arguments": '{"value":"second"}'},
					},
				]
				if len(requests) == 1
				else []
			)
			if kwargs.get("stream"):
				return iter(
					[
						SimpleNamespace(
							choices=[
								SimpleNamespace(
									delta={
										"content": None if tool_calls else "done",
										"tool_calls": [dict(tc, index=i) for i, tc in enumerate(tool_calls)],
									},
									finish_reason="tool_calls" if tool_calls else "stop",
								)
							]
						)
					]
				)
			return SimpleNamespace(
				choices=[
					SimpleNamespace(
						message=SimpleNamespace(
							content=None if tool_calls else "done",
							tool_calls=[
								SimpleNamespace(id=tc["id"], function=SimpleNamespace(**tc["function"]))
								for tc in tool_calls
							],
						),
						finish_reason="tool_calls" if tool_calls else "stop",
					)
				],
				usage=None,
			)

		with patch("litellm.completion", side_effect=completion):
			session = agent.new_session(title="Signed tool persistence fixture")
			result = session.chat("Run both fake tools", stream=stream)
			if stream:
				list(result)
			if confirmation:
				paused = frappe.get_last_doc("Flow Run", filters={"session": session.name})
				self.assertEqual(paused.status, "Paused")
				self.assertEqual(calls, [])
				session = load_session(session.name, agent=agent)
				result = session.resume(
					{
						call_id: "Approve" if approve else "Deny",
						"call_short": "Approve" if approve else "Deny",
					},
					stream=stream,
				)
				if stream:
					list(result)
			session = load_session(session.name, agent=agent)
			transcript = session.transcript()
			tool_messages = [m for m in transcript if m["role"] == "tool"]
			self.assertEqual([m["tool_call_id"] for m in tool_messages], [call_id, "call_short"])
			self.assertEqual(calls, ["first", "second"] if approve else [])
			if approve:
				self.assertEqual([m["content"] for m in tool_messages], ["first", "second"])
			result = session.chat("Continue from stored history", stream=stream)
			if stream:
				list(result)

		self.assertEqual(requests[-1][:-1], transcript)
		stored_calls = next(m["tool_calls"] for m in requests[-1] if m.get("tool_calls"))
		self.assertEqual([tc["id"] for tc in stored_calls], [call_id, "call_short"])
		# Exercise the actual native next-request conversion, including function
		# response association. No provider call or credential is needed here.
		body = _transform_request_body(
			messages=deepcopy(requests[-1]),
			model="gemini-3.8-flash" if signed else "gemini-2.5-flash",
			optional_params={},
			custom_llm_provider="gemini",
			litellm_params={},
			cached_content=None,
		)
		parts = [part for content in body["contents"] for part in content["parts"]]
		invocations = [part for part in parts if "function_call" in part]
		responses = [part["function_response"] for part in parts if "function_response" in part]
		self.assertEqual(invocations[0].get("thoughtSignature"), signature if signed else None)
		self.assertEqual(
			[part["function_call"]["args"] for part in invocations], [{"value": "first"}, {"value": "second"}]
		)
		self.assertEqual([response["name"] for response in responses], ["echo", "echo"])
		if approve:
			self.assertEqual(
				[response["response"] for response in responses],
				[{"content": "first"}, {"content": "second"}],
			)
