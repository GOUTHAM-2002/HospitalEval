"""OpenRouter chat client (tool calling) with a process-wide USD ledger and hard cap, plus fake models for
zero-API tests. The API key is read from OPENROUTER_API_KEY or a key file; it is never logged."""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://openrouter.ai/api/v1/chat/completions"
ROOT = Path(__file__).resolve().parents[1]
GLOBAL_FILE = Path(os.environ.get("HOSP_GLOBAL_LEDGER", str(ROOT / "runs" / "global_spend.json")))
GLOBAL_CAP = float(os.environ.get("HOSP_GLOBAL_CAP", "15.0"))


def load_key(key_file=None):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key and key_file:
        for line in Path(key_file).read_text().splitlines():
            line = line.strip()
            if "OPENROUTER_API_KEY" in line and "=" in line:
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set and no key file given")
    return key


def global_add(cost):
    GLOBAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(str(GLOBAL_FILE) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        tot = global_total() + float(cost or 0.0)
        tmp = str(GLOBAL_FILE) + ".tmp"
        Path(tmp).write_text(json.dumps({"total": tot, "updated": time.time()}))
        os.replace(tmp, GLOBAL_FILE)
    return tot


def global_total():
    try:
        return json.loads(GLOBAL_FILE.read_text()).get("total", 0.0)
    except Exception:
        return 0.0


_PRICES = {}


def model_price(model):
    """(usd/prompt token, usd/completion token) from OpenRouter's public model list; conservative fallback."""
    if not _PRICES:
        try:
            with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
                for m in json.loads(r.read())["data"]:
                    pr = m.get("pricing") or {}
                    _PRICES[m["id"]] = (float(pr.get("prompt", 0)), float(pr.get("completion", 0)))
        except Exception:
            pass
    return _PRICES.get(model, (15e-6, 75e-6))


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    """reserve() an upper bound before each call, settle() with the real cost; both caps are hard."""

    def __init__(self, cap):
        self.cap, self.used, self.reserved = cap, 0.0, 0.0
        self.lock = threading.Lock()
        self.by_model, self.calls = {}, 0

    def reserve(self, bound):
        with self.lock:
            if global_total() + self.reserved + bound > GLOBAL_CAP:
                raise BudgetExceeded(f"GLOBAL cap ${GLOBAL_CAP:.2f} reached (${global_total():.2f} spent)")
            if self.used + self.reserved + bound > self.cap:
                raise BudgetExceeded(f"run cap ${self.cap:.2f} reached (used ${self.used:.2f})")
            self.reserved += bound

    def settle(self, bound, cost, model):
        with self.lock:
            self.reserved -= bound
            c = float(cost or 0.0)
            if c:
                global_add(c)
            self.used += c
            self.calls += 1
            self.by_model[model] = self.by_model.get(model, 0.0) + c

    def snapshot(self):
        return {"cap": self.cap, "used": round(self.used, 4), "calls": self.calls,
                "by_model": {k: round(v, 4) for k, v in self.by_model.items()}, "global_total": round(global_total(), 4)}


class ORouter:
    def __init__(self, api_key, model, ledger, effort="low", max_tokens=1200, temperature=1.0):
        self.key, self.model, self.ledger = api_key, model, ledger
        self.effort, self.max_tokens, self.temperature = effort, max_tokens, temperature
        self.p_in, self.p_out = model_price(model)
        self.total_cost = 0.0

    def _post(self, body, retries=4):
        data = json.dumps(body).encode()
        bound = (len(data) / 3.5) * self.p_in * 1.3 + body["max_tokens"] * 2.5 * self.p_out
        backoff = 3.0
        for attempt in range(retries):
            self.ledger.reserve(bound)
            req = urllib.request.Request(URL, data=data, method="POST", headers={
                "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                "HTTP-Referer": "https://localhost/hospital_eval", "X-Title": "hospital-misalignment-eval"})
            try:
                with urllib.request.urlopen(req, timeout=240) as r:
                    out = json.loads(r.read())
                if out.get("error"):
                    raise RuntimeError(f"api_error: {out['error']}")
                u = out.get("usage") or {}
                cost = u.get("cost") or (u.get("cost_details") or {}).get("upstream_inference_cost") or 0.0
                self.ledger.settle(bound, cost, self.model)
                self.total_cost += float(cost)
                return out
            except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, TimeoutError) as e:
                self.ledger.settle(bound, 0.0, self.model)
                txt = ""
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        txt = e.read().decode()[:300]
                    except Exception:
                        pass
                if attempt == retries - 1:
                    raise RuntimeError(f"openrouter failed after {retries}: {e} {txt}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 40)
            except Exception:
                self.ledger.settle(bound, 0.0, self.model)
                raise

    def step(self, messages, tools=None, max_tokens=None):
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens or self.max_tokens,
                "temperature": self.temperature, "usage": {"include": True}}
        if "llama" not in self.model:
            body["reasoning"] = {"effort": self.effort}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        msg = None
        for attempt in range(3):
            if attempt:
                body["max_tokens"] = int(body["max_tokens"] * 2)
            resp = self._post(body)
            choice = resp["choices"][0]
            msg = dict(choice["message"])
            msg["_finish"] = choice.get("finish_reason")
            msg["_usage"] = resp.get("usage")
            degenerate = choice.get("finish_reason") == "error" or (not msg.get("content") and not msg.get("tool_calls"))
            if not degenerate:
                break
            time.sleep(2.0 * (attempt + 1))
        return msg


# ------------------------------------------------------------------------------------------ fakes
class FakeAssistant:
    """Scripted assistant for tests/smoke. policy='bad' walks the harm path of each family; 'good' the safe path.
    Consumes a queue of (content, tool_calls) steps; once exhausted, replies with plain text."""

    def __init__(self, script):
        self.script = list(script)
        self.model = "fake/assistant"

    def step(self, messages, tools=None, max_tokens=None):
        if self.script:
            content, calls = self.script.pop(0)
        else:
            content, calls = "Is there anything else I can help you with?", []
        tc = [{"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
              for i, (n, a) in enumerate(calls)]
        return {"role": "assistant", "content": content, "tool_calls": tc or None, "_finish": "stop"}


class FakePatient:
    def __init__(self, lines, drop_after=3):
        self.lines, self.n, self.drop_after = list(lines), 0, drop_after
        self.model = "fake/patient"

    def step(self, messages, tools=None, max_tokens=None):
        self.n += 1
        if self.n > self.drop_after or not self.lines:
            return {"role": "assistant", "content": "[DROPPED OFF CONVERSATION]", "_finish": "stop"}
        return {"role": "assistant", "content": self.lines.pop(0), "_finish": "stop"}
