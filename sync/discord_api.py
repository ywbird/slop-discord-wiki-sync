"""Discord REST client. Thin sync wrapper over discord.py's HTTPClient.

discord.py's HTTP layer handles the aiohttp session, 429/global rate limits,
and request retries. We keep sync facades because the CLI is synchronous, plus
the configured minimum interval before each request.
"""

import asyncio
import atexit
import io
import json
import time

import aiohttp
import discord
from discord.http import HTTPClient, Route


class DiscordError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class DiscordAPI:
    def __init__(self, token, cfg=None):
        cfg = cfg or {}
        rl = cfg.get("rate_limit", {})
        self.token = token
        self.min_interval_ms = rl.get("min_interval_ms", 350)
        self.calls = 0  # HTTP layer counter; dry-run asserts this stays 0
        self._last = 0.0
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._http = None
        self._atexit = atexit.register(self.close)

    async def _make_client(self):
        http = HTTPClient(self._loop, max_ratelimit_timeout=300.0)
        # Mirror static_login's session setup without the /users/@me call.
        http.connector = aiohttp.TCPConnector(limit=0)
        http._HTTPClient__session = aiohttp.ClientSession(
            connector=http.connector,
            cookie_jar=aiohttp.DummyCookieJar(),
        )
        http._global_over = asyncio.Event()
        http._global_over.set()
        http.token = self.token
        return http

    def _client(self):
        if self._http is None:
            self._http = self._loop.run_until_complete(self._make_client())
        return self._http

    # --- core ---

    def _sleep_between(self):
        now = time.monotonic()
        wait = self.min_interval_ms / 1000.0 - (now - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _run(self, coro):
        try:
            return self._loop.run_until_complete(coro)
        except discord.HTTPException as e:
            raise DiscordError(e.status, e.text) from None

    def _request(self, method, path, json_body=None, files=None):
        route = Route(method, path)
        kwargs = {}
        if files:
            payload = json_body or {}
            form = [{"name": "payload_json", "value": json.dumps(payload),
                     "content_type": "application/json"}]
            for i, (fn, data) in enumerate(files):
                form.append({
                    "name": f"files[{i}]", "value": data,
                    "filename": fn, "content_type": "application/octet-stream",
                })
            kwargs["form"] = form
            kwargs["files"] = [discord.File(io.BytesIO(d), filename=fn) for fn, d in files]
        elif json_body is not None:
            kwargs["json"] = json_body
        self._sleep_between()
        self.calls += 1
        return self._run(self._client().request(route, **kwargs))

    def close(self):
        if self._closed:
            return
        self._closed = True
        atexit.unregister(self._atexit)
        if self._http is not None:
            try:
                self._run(self._http.close())
            except Exception:  # noqa: BLE001
                pass
            self._http = None
        try:
            self._loop.close()
        except Exception:  # noqa: BLE001
            pass

    # --- endpoints ---

    def create_thread(self, forum_id, name, message, applied_tags=None, flags=4096):
        payload = {"name": name, "message": message, "flags": flags}
        if applied_tags:
            payload["applied_tags"] = applied_tags
        return self._request("POST", f"/channels/{forum_id}/threads", json_body=payload)

    def edit_channel(self, channel_id, payload):
        return self._request("PATCH", f"/channels/{channel_id}", json_body=payload)

    def create_message(self, channel_id, content, flags=4096, attachments=None, files=None, description=None):
        payload = {
            "content": content,
            "flags": flags,
            "allowed_mentions": {"parse": []},
        }
        if files:
            payload["attachments"] = [
                {"id": i, "filename": fn, "description": description or ""}
                for i, (fn, _data) in enumerate(files)
            ]
        elif attachments is not None:
            payload["attachments"] = attachments
        return self._request("POST", f"/channels/{channel_id}/messages", json_body=payload, files=files)

    def edit_message(self, channel_id, message_id, content, attachments, files=None, flags=None):
        payload = {"content": content, "attachments": attachments}
        if flags is not None:
            payload["flags"] = flags
        return self._request(
            "PATCH", f"/channels/{channel_id}/messages/{message_id}", json_body=payload, files=files
        )

    def get_message(self, channel_id, message_id):
        return self._request("GET", f"/channels/{channel_id}/messages/{message_id}")

    def get_channel(self, channel_id):
        return self._request("GET", f"/channels/{channel_id}")

    def list_messages(self, channel_id, limit=100):
        return self._request("GET", f"/channels/{channel_id}/messages?limit={int(limit)}") or []
