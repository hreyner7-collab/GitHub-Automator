"""ftai_test_support.py — shared scaffolding for the ftai_tool_shim suites.

Owns exactly the pieces the three suites used to fork privately (measured:
behavior ~95-line Mock, edge ~60-line differently-styled Mock, adversarial
_FakeUpstream FIFO — three planners rendered in three shapes, so a broken
planner contract could pass in one harness and fail in another):

  - the mock-gateway HTTP envelope (parse POST, delegate, encode reply)
  - the ONE canonical planner-reply shape, one builder for every fake
  - server lifecycle (mock gateway + shim host) and the shim module loader
    with the established FTAI_UPSTREAM env seam
  - the check() harness, the shim post() helper, and the shared model-id /
    tool-spec constants

Suites keep only their distinct scenario logic, expressed as a policy
object with two hooks on top of this skeleton:
    policy.serve_get() -> (code, obj)
    policy.serve(model, body, raw) -> (code, obj)
The adversarial suite additionally records bodies via its FIFO policy
("FifoPolicy" lives there; capture-per-model is its genuinely distinct
behavior, not a fork of this envelope).
"""
import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# shared identity/policy constants (were re-declared in every suite)
LION = "fta/lion/claude-opus-5-5"

def tool_spec(name, desc, **props):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object",
                           "properties": {k: {"type": v} for k, v in props.items()}}}}

DEFAULT_TOOL = tool_spec("get_weather", "Get current weather for a city", city="string")
COFFEE_TOOL = tool_spec("make_coffee", "Make coffee", size="string")

# the ONE canonical planner-envelope shape every fake renders
def planner_tool_call(cid, name, arguments):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": arguments}}

def planner_tool_calls_reply(calls, content=None):
    return {"object": "chat.completion", "choices": [{
        "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": content,
                    "tool_calls": list(calls)}}]}

def planner_stop_reply(content):
    return {"object": "chat.completion", "choices": [{
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": content}}]}

def resident_reply(content, model=None, usage=False):
    ch = {"index": 0, "finish_reason": "stop",
          "message": {"role": "assistant", "content": content}}
    reply = {"object": "chat.completion", "choices": [ch]}
    if model:
        reply["model"] = model
    if usage:
        reply["usage"] = {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}
    return reply

def load_shim(env_upstream=None):
    """Load ftai_tool_shim fresh with the FTAI_UPSTREAM seam env-set BEFORE
    import (the shim reads it once at startup). Returns the loaded module."""
    if env_upstream is not None:
        os.environ["FTAI_UPSTREAM"] = env_upstream
    spec = importlib.util.spec_from_file_location("shimmod", "ftai_tool_shim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class MockGatewayHandler(BaseHTTPRequestHandler):
    """Envelope only: parse the OpenAI-shaped POST, delegate the whole
    scenario to policy.serve(model, body, raw), encode the reply."""
    protocol_version = "HTTP/1.1"
    policy = None

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        code, obj = self.policy.serve_get()
        self._send(code, obj)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = None
        model = str(body.get("model") or "") if isinstance(body, dict) else ""
        code, obj = self.policy.serve(model, body, raw)
        self._send(code, obj)

def start_server(handler_cls, port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def start_mock(policy, port=0):
    handler_cls = type("MockGateway_" + type(policy).__name__,
                       (MockGatewayHandler,), {"policy": policy})
    return start_server(handler_cls, port)

def make_post(url):
    """The shim post() helper, bound to one shim URL (semantics identical to
    the per-suite copies it replaces: parsed JSON, or raw text when
    want_status, and HTTPError as (code, text))."""
    def post(body, timeout=60, want_status=False):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
                return (r.status, raw if want_status else json.loads(raw))
        except urllib.error.HTTPError as e:
            return (e.code, e.read().decode())
    return post

class Checker:
    """The shared PASS/FAIL harness; exact print text of the per-suite copies."""
    def __init__(self):
        self.fails = []

    def __call__(self, name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  | {detail}"))
        if not cond:
            self.fails.append(name)
