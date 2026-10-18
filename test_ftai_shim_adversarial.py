#!/usr/bin/env python3
"""Adversarial probes of ftai_tool_shim's real HTTP entry point.

Every case drives the actual Handler.do_POST over loopback HTTP; only the
upstream destination is a scripted local fake (never the real gateway, and
never the quota'd /v1/chat/completions routes). Cases target boundaries the
proxy realistically receives from OpenAI-compatible clients and upstream:
non-dict JSON bodies, empty/null upstream choices, multimodal list content,
non-dict history entries, missing finish_reason on the SSE path, and the
refusal-retry adopting an unusable fresh sample.
"""
import json
import http.server
import os
import threading
import unittest
import urllib.request
import urllib.error

import ftai_tool_shim as shim

os.environ["FTAI_CHAT_KEY"] = "test-key"

# distinct fake-model names; each model maps to a FIFO of (status, payload)
DEFAULT = {"choices": [{"message": {"content": None, "tool_calls": []}}]}
QUEUE = {}


class _FakeUpstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, st, obj):
        body = json.dumps(obj).encode()
        self.send_response(st)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        model = ""
        try:
            model = str(json.loads(raw).get("model") or "")
        except Exception:
            pass
        if QUEUE.get(model):
            st, obj = QUEUE[model].pop(0)
        else:
            st, obj = 200, DEFAULT
        self._send(st, obj)

    def do_GET(self):
        self._send(200, {"object": "list", "data": []})


def _start(server_cls, handler):
    srv = server_cls(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


class AdversarialShimTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake, _ = _start(http.server.ThreadingHTTPServer, _FakeUpstream)
        shim.UPSTREAM = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        shim.PLANNER_MODEL = "z/planner"
        cls.shim_srv, _ = _start(
            shim.ThreadingHTTPServer, shim.Handler)
        cls.port = cls.shim_srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.shim_srv.shutdown()
        cls.fake.shutdown()

    def setUp(self):
        QUEUE.clear()

    def _post(self, body, headers=None, raw=None):
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=data, method="POST",
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _chat(self, **kw):
        body = {"model": "rail/lion/cat", "messages": kw.get("messages"), "tools": kw.get("tools", [])}
        body.update({k: v for k, v in kw.items() if k in ("stream",)})
        return self._post(body)

    def _dummy_tool(self):
        return [{"type": "function",
                 "function": {"name": "noop", "parameters": {"type": "object", "properties": {}}}}]

    def _resident(self, content, **extra):  # scripted resident (lion) reply
        return (200, {"choices": [{"message": {"role": "assistant", "content": content},
                                    "finish_reason": "stop", **extra}]})

    # 1. garbage-in that a proxy realistically receives --------------------
    def test_non_dict_json_body_is_400_not_reset(self):
        st, body = self._post(None, raw=b"[1,2,3]")
        self.assertEqual(st, 400)

    # 2. upstream 200 with unusable choices shapes --------------------------
    def test_planner_empty_choices_is_honest_502(self):
        QUEUE["z/planner"] = [(200, {"choices": []})]
        st, body = self._chat(messages=[{"role": "user", "content": "hi"}],
                              tools=[{"type": "function", "function": {"name": "t"}}])
        self.assertEqual(st, 502)
        self.assertIn(b"planner", body)

    def test_planner_null_choices_is_honest_502(self):
        QUEUE["z/planner"] = [(200, {"choices": None})]
        st, _ = self._chat(messages=[{"role": "user", "content": "hi"}],
                           tools=[{"type": "function", "function": {"name": "t"}}])
        self.assertEqual(st, 502)

    # 3. multimodal list content on the user message ------------------------
    def test_list_content_user_message_completes(self):
        QUEUE["rail/lion/cat"] = [self._resident("42")]
        st, body = self._chat(messages=[
            {"role": "user", "content": ["plain text part",
             {"type": "text", "text": "What is 2+2?"}]}],
            tools=self._dummy_tool())
        self.assertEqual(st, 200)
        got = json.loads(body)
        self.assertEqual(got["choices"][0]["message"]["content"], "42")

    # 4. non-dict entries inside the history list ---------------------------
    def test_non_dict_history_entries_do_not_crash(self):
        QUEUE["rail/lion/cat"] = [self._resident("ok")]
        st, body = self._chat(messages=[
            {"role": "user", "content": "What is 2+2?"},
            "garbage-string-entry"],
            tools=self._dummy_tool())
        self.assertEqual(st, 200)

    # 5. SSE with a resident reply missing finish_reason --------------------
    def test_stream_missing_finish_reason_still_serves_sse(self):
        QUEUE["rail/lion/cat"] = [(200, {"choices": [{"message":
            {"role": "assistant", "content": "hello"}}]})]
        st, body = self._chat(messages=[{"role": "user", "content": "hi"}], stream=True,
                              tools=self._dummy_tool())
        self.assertEqual(st, 200)
        self.assertIn(b"data: [DONE]", body)
        self.assertIn(b'"finish_reason": null', body)

    # 6. refusal-retry must not adopt an unusable fresh sample ---------------
    def test_retry_keeps_good_answer_when_fresh_sample_is_empty(self):
        QUEUE["rail/lion/cat"] = [
            self._resident("I cannot access that information."),
            self._resident(""),  # fresh sample: empty content, non-refusing
        ]
        st, body = self._chat(messages=[{"role": "user", "content": "weather in Berlin?"}],
                              tools=[{"type": "function", "function": {"name": "get_weather"}}])
        self.assertEqual(st, 200)
        got = json.loads(body)
        self.assertEqual(got["choices"][0]["message"]["content"],
                         "I cannot access that information.")
        self.assertEqual(got["model"], "rail/lion/cat")

    def test_retry_keeps_good_answer_when_fresh_choices_empty(self):
        QUEUE["rail/lion/cat"] = [
            self._resident("I cannot access that information."),
            (200, {"choices": []}),
        ]
        st, body = self._chat(messages=[{"role": "user", "content": "hi"}],
                              tools=self._dummy_tool())
        self.assertEqual(st, 200)
        got = json.loads(body)
        self.assertEqual(got["choices"][0]["message"]["content"],
                         "I cannot access that information.")

    # positive control: retry with a genuinely good fresh sample still swaps
    def test_retry_adopts_usable_fresh_sample(self):
        QUEUE["rail/lion/cat"] = [
            self._resident("I do not have access to that."),
            self._resident("The answer is 42."),
        ]
        st, body = self._chat(messages=[{"role": "user", "content": "hi"}],
                              tools=self._dummy_tool())
        got = json.loads(body)
        self.assertEqual(got["choices"][0]["message"]["content"], "The answer is 42.")
        self.assertEqual(got["model"], "rail/lion/cat")


if __name__ == "__main__":
    unittest.main(verbosity=2)
