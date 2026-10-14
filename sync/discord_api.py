import json
import time
import urllib.request
import urllib.error
from email.generator import _make_boundary


BASE = "https://discord.com/api/v10"


class DiscordError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class DiscordAPI:
    """Single serialized queue. All requests go through ._request()."""

    def __init__(self, token, cfg=None):
        cfg = cfg or {}
        rl = cfg.get("rate_limit", {})
        self.token = token
        self.min_interval_ms = rl.get("min_interval_ms", 350)
        self.max_retries = rl.get("max_retries", 5)
        self._last = 0.0
        self.calls = 0  # HTTP layer counter; dry-run asserts this stays 0

    def _sleep_between(self):
        now = time.monotonic()
        wait = self.min_interval_ms / 1000.0 - (now - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _request(self, method, path, json_body=None, multipart=None):
        url = BASE + path
        headers = {
            "Authorization": f"Bot {self.token}",
            "User-Agent": "DiscordBot (slop-wiki-sync, 1.0)",
        }
        data = None
        if multipart is not None:
            data, ctype = multipart
            headers["Content-Type"] = ctype
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        for attempt in range(self.max_retries + 1):
            self._sleep_between()
            self.calls += 1
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req) as resp:
                    body = resp.read().decode("utf-8")
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code == 429:
                    retry_after = None
                    try:
                        retry_after = float(e.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        pass
                    if not retry_after:
                        try:
                            retry_after = float(json.loads(body).get("retry_after", 1.0))
                        except Exception:
                            retry_after = 1.0
                    global_block = e.headers.get("X-RateLimit-Global") == "true"
                    time.sleep(max(retry_after, 0.1))
                    continue
                if 500 <= e.code < 600:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise DiscordError(e.code, body)
            except urllib.error.URLError:
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 30))
        raise DiscordError(429, "rate limited: max retries exceeded")

    # --- endpoints ---
    def create_thread(self, forum_id, name, message, applied_tags=None, flags=4096):
        payload = {"name": name, "message": message, "flags": flags}
        if applied_tags:
            payload["applied_tags"] = applied_tags
        return self._request("POST", f"/channels/{forum_id}/threads", json_body=payload)

    def edit_channel(self, channel_id, payload):
        return self._request("PATCH", f"/channels/{channel_id}", json_body=payload)

    def create_message(self, channel_id, content, flags=4096, attachments=None, files=None, description=None):
        if files:
            return self._create_message_multipart(channel_id, content, flags, files, description)
        payload = {
            "content": content,
            "flags": flags,
            "allowed_mentions": {"parse": []},
        }
        if attachments is not None:
            payload["attachments"] = attachments
        return self._request("POST", f"/channels/{channel_id}/messages", json_body=payload)

    def _create_message_multipart(self, channel_id, content, flags, files, description):
        payload = {
            "content": content,
            "flags": flags,
            "allowed_mentions": {"parse": []},
            "attachments": [
                {"id": i, "filename": fn, "description": description or ""}
                for i, (fn, _data) in enumerate(files)
            ],
        }
        data, ctype = _encode_multipart(payload, files)
        return self._request("POST", f"/channels/{channel_id}/messages", multipart=(data, ctype))

    def edit_message(self, channel_id, message_id, content, attachments, files=None, flags=None):
        payload = {"content": content, "attachments": attachments}
        if flags is not None:
            payload["flags"] = flags
        if files:
            data, ctype = _encode_multipart(payload, files)
            return self._request("PATCH", f"/channels/{channel_id}/messages/{message_id}",
                                 multipart=(data, ctype))
        return self._request("PATCH", f"/channels/{channel_id}/messages/{message_id}",
                             json_body=payload)

    def get_message(self, channel_id, message_id):
        return self._request("GET", f"/channels/{channel_id}/messages/{message_id}")

    def get_channel(self, channel_id):
        return self._request("GET", f"/channels/{channel_id}")


def _encode_multipart(payload, files):
    boundary = _make_boundary()
    lines = []
    lines.append(f"--{boundary}\r\n".encode())
    lines.append(b'Content-Disposition: form-data; name="payload_json"\r\n')
    lines.append(b"Content-Type: application/json\r\n\r\n")
    lines.append(json.dumps(payload).encode("utf-8"))
    lines.append(b"\r\n")
    for i, (fn, data) in enumerate(files):
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(
            f'Content-Disposition: form-data; name="files[{i}]"; filename="{fn}"\r\n'.encode()
        )
        lines.append(b"Content-Type: application/octet-stream\r\n\r\n")
        lines.append(data)
        lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode())
    body = b"".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"
