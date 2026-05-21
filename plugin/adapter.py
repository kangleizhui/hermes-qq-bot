"""
NapCat (OneBot v11) Platform Adapter for Hermes Agent.

Connects to a NapCatQQ instance via OneBot v11 WebSocket Server (for inbound
events) and OneBot v11 HTTP API (for outbound actions). This enables Hermes to
operate on a real QQ account — see real QQ numbers / group numbers, distinguish
each user in a group, and reply in private chats or groups.

Configuration via environment variables (preferred):
    NAPCAT_WS_URL          ws://127.0.0.1:3001
    NAPCAT_HTTP_URL        http://127.0.0.1:3000
    NAPCAT_ACCESS_TOKEN    shared token configured in NapCat OneBot settings

Or via config.yaml::

    gateway:
      platforms:
        napcat:
          enabled: true
          extra:
            ws_url: "ws://127.0.0.1:3001"
            http_url: "http://127.0.0.1:3000"
            access_token: "..."
            self_id: "3531314097"            # optional, auto-detected
            allowed_users: ["10001"]         # empty = use NAPCAT_ALLOW_ALL_USERS
            allowed_groups: ["123456"]       # empty = allow all groups
            group_respond_only_at_me: true   # only reply in groups when @-ed

Identity model::

    Private chat:  chat_id=<peer QQ>, chat_type="dm",    user_id=<peer QQ>
    Group chat:    chat_id=<group #>, chat_type="group", user_id=<sender QQ>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None  # type: ignore

from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    MessageEvent,
    MessageType,
)
from gateway.config import Platform

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OneBot v11 helpers
# ---------------------------------------------------------------------------

def _extract_text_from_segments(segments: Any) -> str:
    """Pull plain text out of OneBot 'array' message segments.

    NapCat (with messagePostFormat=array) sends messages like::

        [
          {"type": "at", "data": {"qq": "3531314097"}},
          {"type": "text", "data": {"text": " 你好"}},
          {"type": "image", "data": {"url": "..."}},
        ]
    """
    if isinstance(segments, str):
        return segments
    if not isinstance(segments, list):
        return ""
    parts: List[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        data = seg.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        elif seg_type == "at":
            qq = data.get("qq")
            parts.append(f"@{qq} " if qq else "")
        elif seg_type == "face":
            parts.append("[表情]")
        elif seg_type == "image":
            parts.append("[图片]")
        elif seg_type == "record":
            parts.append("[语音]")
        elif seg_type == "video":
            parts.append("[视频]")
        elif seg_type == "file":
            parts.append(f"[文件 {data.get('file', '')}]")
        elif seg_type == "reply":
            # Quoted message — skip the marker, the text will follow
            continue
    return "".join(parts).strip()


def _extract_media_urls(segments: Any) -> tuple[List[str], List[str]]:
    """Return (urls, types) for image / record / video / file segments."""
    if not isinstance(segments, list):
        return [], []
    urls: List[str] = []
    types: List[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        data = seg.get("data") or {}
        url = data.get("url") or data.get("file")
        if not url:
            continue
        if seg_type == "image":
            urls.append(url); types.append("image")
        elif seg_type == "record":
            urls.append(url); types.append("voice")
        elif seg_type == "video":
            urls.append(url); types.append("video")
        elif seg_type == "file":
            urls.append(url); types.append("document")
    return urls, types


def _is_at_me(segments: Any, self_id: str) -> bool:
    """Check whether the message @-mentions our bot."""
    if not self_id or not isinstance(segments, list):
        return False
    for seg in segments:
        if isinstance(seg, dict) and seg.get("type") == "at":
            if str((seg.get("data") or {}).get("qq", "")) == str(self_id):
                return True
    return False


def _strip_at_me(segments: Any, self_id: str) -> Any:
    """Remove '@me' segments so the text passed to the agent is clean."""
    if not isinstance(segments, list):
        return segments
    cleaned = []
    for seg in segments:
        if isinstance(seg, dict) and seg.get("type") == "at":
            if str((seg.get("data") or {}).get("qq", "")) == str(self_id):
                continue
        cleaned.append(seg)
    return cleaned


def _parse_chat_id(chat_id: str) -> tuple[str, str]:
    """Parse 'user:123' / 'group:456' or bare numeric ID.

    Returns (kind, number) where kind ∈ {"user", "group"}.
    Bare numeric IDs default to "user" (private chat).
    """
    s = str(chat_id).strip()
    if ":" in s:
        kind, _, num = s.partition(":")
        kind = kind.lower().strip()
        if kind in ("user", "private", "dm", "friend"):
            return "user", num.strip()
        if kind in ("group", "g"):
            return "group", num.strip()
    return "user", s


# ---------------------------------------------------------------------------
# NapCat Adapter
# ---------------------------------------------------------------------------

class NapCatAdapter(BasePlatformAdapter):
    """OneBot v11 adapter for NapCatQQ.

    Inbound:  WebSocket connection to NapCat's WS server (we are the client).
    Outbound: HTTP POST to NapCat's HTTP API server.
    """

    def __init__(self, config, **kwargs):
        platform = Platform("napcat")
        super().__init__(config=config, platform=platform)

        extra = getattr(config, "extra", {}) or {}

        # Endpoints
        self.ws_url = (os.getenv("NAPCAT_WS_URL") or extra.get("ws_url", "")).rstrip("/")
        self.http_url = (os.getenv("NAPCAT_HTTP_URL") or extra.get("http_url", "")).rstrip("/")
        self.access_token = os.getenv("NAPCAT_ACCESS_TOKEN") or extra.get("access_token", "")

        # Bot identity (auto-detected from meta_event lifecycle if blank)
        self_id = os.getenv("NAPCAT_SELF_ID") or extra.get("self_id", "")
        self.self_id: str = str(self_id) if self_id else ""

        # Auth
        allowed_users = extra.get("allowed_users") or []
        env_users = os.getenv("NAPCAT_ALLOWED_USERS", "").strip()
        if env_users:
            allowed_users = [u.strip() for u in env_users.split(",") if u.strip()]
        self.allowed_users: set = {str(u) for u in allowed_users}

        self.allow_all_users = (
            os.getenv("NAPCAT_ALLOW_ALL_USERS", "").strip().lower() in {"1", "true", "yes"}
            or bool(extra.get("allow_all_users"))
        )

        allowed_groups = extra.get("allowed_groups") or []
        env_groups = os.getenv("NAPCAT_ALLOWED_GROUPS", "").strip()
        if env_groups:
            allowed_groups = [g.strip() for g in env_groups.split(",") if g.strip()]
        self.allowed_groups: set = {str(g) for g in allowed_groups}

        group_at_only = os.getenv("NAPCAT_GROUP_RESPOND_ONLY_AT_ME", "").strip().lower()
        if group_at_only in {"1", "true", "yes"}:
            self.group_respond_only_at_me = True
        elif group_at_only in {"0", "false", "no"}:
            self.group_respond_only_at_me = False
        else:
            self.group_respond_only_at_me = bool(extra.get("group_respond_only_at_me", True))

        # Runtime state
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._echo_seq: int = 0
        self._echo_futures: Dict[str, asyncio.Future] = {}
        # Cache of group_id -> {user_id: nickname/card} for nicer display
        self._group_member_cache: Dict[str, Dict[str, str]] = {}
        # Cache of chat_id -> "user" | "group" so send() can route to the right
        # OneBot action without relying on the caller to namespace chat_id.
        # Populated on every inbound message; survives in-memory only.
        self._chat_kind_cache: Dict[str, str] = {}

    @property
    def name(self) -> str:
        return "NapCat"

    def _resolve_chat_kind(self, chat_id: str) -> tuple[str, str]:
        """Return (kind, numeric_id) for an outbound chat_id.

        Resolution order (most specific first):
          1. Explicit prefix: "user:123" / "group:456" / "private:789"
          2. In-memory cache populated by inbound messages
          3. Fallback: assume private chat (bare numeric ID = user QQ)
        """
        s = str(chat_id).strip()
        if ":" in s:
            return _parse_chat_id(s)
        kind = self._chat_kind_cache.get(s)
        if kind:
            return kind, s
        return "user", s

    # ── Connection lifecycle ──────────────────────────────────────────────

    async def connect(self) -> bool:
        if not AIOHTTP_AVAILABLE:
            logger.error("NapCat: aiohttp not installed")
            self._set_fatal_error("missing_dep", "aiohttp required", retryable=False)
            return False
        if not self.ws_url or not self.http_url:
            logger.error("NapCat: NAPCAT_WS_URL and NAPCAT_HTTP_URL must be set")
            self._set_fatal_error(
                "config_missing",
                "NAPCAT_WS_URL and NAPCAT_HTTP_URL must be set",
                retryable=False,
            )
            return False

        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self._session = aiohttp.ClientSession(timeout=timeout)

        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        try:
            self._ws = await self._session.ws_connect(
                self.ws_url,
                headers=headers,
                heartbeat=30,
                autoping=True,
            )
        except Exception as e:
            logger.error("NapCat: WebSocket connect failed: %s", e)
            await self._session.close()
            self._session = None
            self._set_fatal_error("connect_failed", str(e), retryable=True)
            return False

        # If self_id wasn't pre-configured, fetch it via get_login_info
        if not self.self_id:
            info = await self._call_api("get_login_info", {})
            if isinstance(info, dict) and "user_id" in info:
                self.self_id = str(info["user_id"])
                logger.info("NapCat: detected bot QQ as %s (%s)",
                            self.self_id, info.get("nickname", "?"))

        self._recv_task = asyncio.create_task(self._receive_loop())
        self._mark_connected()
        logger.info("NapCat: connected to %s (HTTP %s) as QQ %s",
                    self.ws_url, self.http_url, self.self_id or "<pending>")
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
        # Reject any pending echo futures so callers don't hang forever
        for fut in self._echo_futures.values():
            if not fut.done():
                fut.set_exception(ConnectionError("NapCat disconnected"))
        self._echo_futures.clear()
        self._ws = None
        self._session = None

    # ── Receive loop & event dispatch ─────────────────────────────────────

    async def _receive_loop(self) -> None:
        """Main WebSocket receive loop."""
        try:
            assert self._ws is not None
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        event = json.loads(msg.data)
                        await self._handle_event(event)
                    except Exception:
                        logger.exception("NapCat: error handling event")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.warning("NapCat: WebSocket error: %s", self._ws.exception())
                    break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("NapCat: receive loop crashed")
        finally:
            if self.is_connected:
                logger.warning("NapCat: WebSocket disconnected")
                self._set_fatal_error(
                    "connection_lost",
                    "NapCat WebSocket closed",
                    retryable=True,
                )
                await self._notify_fatal_error()

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """Dispatch an OneBot v11 event."""
        # API call responses come back with an "echo" field
        if "echo" in event and event["echo"] in self._echo_futures:
            fut = self._echo_futures.pop(event["echo"])
            if not fut.done():
                fut.set_result(event)
            return

        post_type = event.get("post_type")

        # Meta events: lifecycle (connect), heartbeat
        if post_type == "meta_event":
            meta_type = event.get("meta_event_type")
            if meta_type == "lifecycle" and not self.self_id:
                sid = event.get("self_id")
                if sid:
                    self.self_id = str(sid)
                    logger.info("NapCat: bot QQ detected via lifecycle: %s", self.self_id)
            return

        # Notice / request events (group joins, friend requests, recalls...)
        # Currently no-op — log only, future work can route into the agent.
        if post_type in ("notice", "request"):
            logger.debug("NapCat: %s event %s", post_type,
                         event.get("notice_type") or event.get("request_type"))
            return

        if post_type != "message":
            return

        msg_type = event.get("message_type")  # "private" | "group"
        sender = event.get("sender") or {}
        sender_qq = str(event.get("user_id", "") or sender.get("user_id", ""))
        sender_name = (
            sender.get("card")  # group nickname
            or sender.get("nickname")
            or f"QQ{sender_qq}"
        )
        raw_segments = event.get("message", [])
        message_id = str(event.get("message_id", "") or int(time.time() * 1000))

        # Ignore our own messages defensively (NapCat config already has
        # reportSelfMessage=false, but belt-and-suspenders)
        if self.self_id and sender_qq == self.self_id:
            return

        if msg_type == "private":
            await self._handle_private(
                sender_qq=sender_qq,
                sender_name=sender_name,
                segments=raw_segments,
                message_id=message_id,
                raw=event,
            )
        elif msg_type == "group":
            group_id = str(event.get("group_id", ""))
            await self._handle_group(
                group_id=group_id,
                sender_qq=sender_qq,
                sender_name=sender_name,
                segments=raw_segments,
                message_id=message_id,
                raw=event,
            )

    async def _handle_private(
        self,
        sender_qq: str,
        sender_name: str,
        segments: Any,
        message_id: str,
        raw: Dict[str, Any],
    ) -> None:
        # Auth check
        if not self.allow_all_users and self.allowed_users and sender_qq not in self.allowed_users:
            logger.debug("NapCat: ignoring DM from unauthorized QQ %s", sender_qq)
            return
        if not self.allow_all_users and not self.allowed_users:
            logger.debug("NapCat: no allowed users configured, ignoring DM from %s", sender_qq)
            return

        text = _extract_text_from_segments(segments)
        media_urls, media_types = _extract_media_urls(segments)

        await self._dispatch(
            text=text,
            chat_id=sender_qq,
            chat_name=sender_name,
            chat_type="dm",
            user_id=sender_qq,
            user_name=sender_name,
            message_id=message_id,
            raw=raw,
            media_urls=media_urls,
            media_types=media_types,
        )

    async def _handle_group(
        self,
        group_id: str,
        sender_qq: str,
        sender_name: str,
        segments: Any,
        message_id: str,
        raw: Dict[str, Any],
    ) -> None:
        # Group allowlist
        if self.allowed_groups and group_id not in self.allowed_groups:
            return

        # Per-user auth in groups (if not allow_all)
        if not self.allow_all_users and self.allowed_users and sender_qq not in self.allowed_users:
            # In groups, an unauthorized user @-ing us is still ignored;
            # owners typically allowlist groups, not individual users.
            # If allowed_users is empty but allow_all is false, fall through
            # for @-mention-only mode (anyone in the allowed group can @ the bot).
            if self.allowed_users:
                return

        # @-mention gate
        addressed = _is_at_me(segments, self.self_id)
        if self.group_respond_only_at_me and not addressed:
            return

        # Strip our @ from the segments before extracting text
        clean_segments = _strip_at_me(segments, self.self_id)
        text = _extract_text_from_segments(clean_segments)
        media_urls, media_types = _extract_media_urls(clean_segments)

        # Cache group nickname for nicer chat_name
        self._group_member_cache.setdefault(group_id, {})[sender_qq] = sender_name

        # Group display name: try to use group_name from raw if NapCat sent it,
        # otherwise fall back to bare ID
        chat_name = raw.get("group_name") or f"群{group_id}"

        await self._dispatch(
            text=text,
            chat_id=group_id,
            chat_name=chat_name,
            chat_type="group",
            user_id=sender_qq,
            user_name=sender_name,
            message_id=message_id,
            raw=raw,
            media_urls=media_urls,
            media_types=media_types,
        )

    async def _dispatch(
        self,
        text: str,
        chat_id: str,
        chat_name: str,
        chat_type: str,
        user_id: str,
        user_name: str,
        message_id: str,
        raw: Dict[str, Any],
        media_urls: List[str],
        media_types: List[str],
    ) -> None:
        if not self._message_handler:
            return

        # Remember whether this chat_id is a user or a group so that future
        # send() calls (which receive only chat_id from the base class) know
        # which OneBot action to invoke. Without this, bare numeric group IDs
        # get routed to send_private_msg and fail with "无法获取用户信息".
        self._chat_kind_cache[str(chat_id)] = (
            "group" if chat_type == "group" else "user"
        )

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
        )

        # Determine MessageType — text by default, override if media
        if media_types:
            mt_map = {
                "image": MessageType.IMAGE,
                "voice": MessageType.VOICE,
                "video": MessageType.VIDEO,
                "document": MessageType.DOCUMENT,
            }
            message_type = mt_map.get(media_types[0], MessageType.TEXT)
        else:
            message_type = MessageType.TEXT

        event = MessageEvent(
            text=text,
            message_type=message_type,
            source=source,
            message_id=message_id,
            timestamp=datetime.now(),
            raw_message=raw,
            media_urls=media_urls,
            media_types=media_types,
        )
        await self.handle_message(event)

    # ── HTTP API ──────────────────────────────────────────────────────────

    async def _call_api(self, action: str, params: Dict[str, Any]) -> Any:
        """Call an OneBot v11 HTTP action. Returns the `data` field on success."""
        if not self._session or not self.http_url:
            return None
        url = f"{self.http_url}/{action}"
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            async with self._session.post(url, json=params, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("NapCat: API %s -> HTTP %s: %s",
                                   action, resp.status, body[:200])
                    return None
                payload = await resp.json()
                if payload.get("status") == "failed":
                    logger.warning("NapCat: API %s failed: %s", action,
                                   payload.get("message") or payload)
                    return None
                return payload.get("data")
        except Exception as e:
            logger.warning("NapCat: API %s exception: %s", action, e)
            return None

    # ── Sending ───────────────────────────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        kind, num = self._resolve_chat_kind(chat_id)
        if not num:
            return SendResult(success=False, error=f"Invalid chat_id: {chat_id}")

        # Build OneBot message array
        segments = []
        if reply_to:
            segments.append({"type": "reply", "data": {"id": str(reply_to)}})
        segments.append({"type": "text", "data": {"text": content}})

        if kind == "group":
            params = {"group_id": int(num), "message": segments}
            action = "send_group_msg"
        else:
            params = {"user_id": int(num), "message": segments}
            action = "send_private_msg"

        data = await self._call_api(action, params)
        if data and "message_id" in data:
            return SendResult(success=True, message_id=str(data["message_id"]))
        return SendResult(success=False, error=f"send {action} failed")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """QQ has no typing indicator via OneBot — no-op."""
        return None

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: str = "",
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        kind, num = self._resolve_chat_kind(chat_id)
        if not num:
            return SendResult(success=False, error=f"Invalid chat_id: {chat_id}")

        # Build "file=" value: local path → file:// URI; http(s) → as-is; base64 → base64://
        file_arg = self._normalize_media_file(image_url)

        segments = []
        if reply_to:
            segments.append({"type": "reply", "data": {"id": str(reply_to)}})
        segments.append({"type": "image", "data": {"file": file_arg}})
        if caption:
            segments.append({"type": "text", "data": {"text": caption}})

        if kind == "group":
            params = {"group_id": int(num), "message": segments}
            action = "send_group_msg"
        else:
            params = {"user_id": int(num), "message": segments}
            action = "send_private_msg"

        data = await self._call_api(action, params)
        if data and "message_id" in data:
            return SendResult(success=True, message_id=str(data["message_id"]))
        return SendResult(success=False, error=f"send_image failed")

    async def send_image_file(
        self,
        chat_id: str,
        path: str,
        caption: str = "",
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        return await self.send_image(chat_id, path, caption, reply_to, metadata)

    async def send_voice(
        self,
        chat_id: str,
        path: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        kind, num = self._resolve_chat_kind(chat_id)
        if not num:
            return SendResult(success=False, error=f"Invalid chat_id: {chat_id}")
        file_arg = self._normalize_media_file(path)
        segments = [{"type": "record", "data": {"file": file_arg}}]
        action = "send_group_msg" if kind == "group" else "send_private_msg"
        key = "group_id" if kind == "group" else "user_id"
        data = await self._call_api(action, {key: int(num), "message": segments})
        if data and "message_id" in data:
            return SendResult(success=True, message_id=str(data["message_id"]))
        return SendResult(success=False, error="send_voice failed")

    async def send_video(
        self,
        chat_id: str,
        path: str,
        caption: str = "",
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        kind, num = self._resolve_chat_kind(chat_id)
        if not num:
            return SendResult(success=False, error=f"Invalid chat_id: {chat_id}")
        file_arg = self._normalize_media_file(path)
        segments = [{"type": "video", "data": {"file": file_arg}}]
        if caption:
            segments.append({"type": "text", "data": {"text": caption}})
        action = "send_group_msg" if kind == "group" else "send_private_msg"
        key = "group_id" if kind == "group" else "user_id"
        data = await self._call_api(action, {key: int(num), "message": segments})
        if data and "message_id" in data:
            return SendResult(success=True, message_id=str(data["message_id"]))
        return SendResult(success=False, error="send_video failed")

    async def send_document(
        self,
        chat_id: str,
        path: str,
        caption: str = "",
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Upload a file. Group files use upload_group_file; DMs use upload_private_file."""
        kind, num = self._resolve_chat_kind(chat_id)
        if not num:
            return SendResult(success=False, error=f"Invalid chat_id: {chat_id}")

        file_arg = self._normalize_media_file(path)
        # Filename for the receiver
        import os as _os
        filename = _os.path.basename(path) if not path.startswith(("http://", "https://")) else "file"

        if kind == "group":
            action = "upload_group_file"
            params = {"group_id": int(num), "file": file_arg, "name": filename}
        else:
            action = "upload_private_file"
            params = {"user_id": int(num), "file": file_arg, "name": filename}

        data = await self._call_api(action, params)
        if data is not None:
            # Upload actions don't always return a message_id
            return SendResult(success=True, message_id=str(int(time.time() * 1000)))
        return SendResult(success=False, error=f"{action} failed")

    @staticmethod
    def _normalize_media_file(src: str) -> str:
        """Convert a local path / URL / data: URI to an OneBot 'file' value."""
        if not src:
            return ""
        if src.startswith(("http://", "https://", "file://", "base64://")):
            return src
        if src.startswith("data:"):
            # data:image/png;base64,XXXX → base64://XXXX
            try:
                _, b64 = src.split(",", 1)
                return f"base64://{b64}"
            except ValueError:
                return src
        # Local path → file:// URI (absolute)
        import os as _os
        abs_path = _os.path.abspath(src)
        return f"file://{abs_path}"

    # ── Chat info ─────────────────────────────────────────────────────────

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        kind, num = self._resolve_chat_kind(chat_id)
        if kind == "group":
            data = await self._call_api("get_group_info", {"group_id": int(num)}) or {}
            return {
                "name": data.get("group_name") or f"群{num}",
                "type": "group",
                "chat_id": chat_id,
            }
        else:
            data = await self._call_api("get_stranger_info", {"user_id": int(num)}) or {}
            return {
                "name": data.get("nickname") or f"QQ{num}",
                "type": "dm",
                "chat_id": chat_id,
            }


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def check_requirements() -> bool:
    """Setup-time check: env vars present and aiohttp installed."""
    if not AIOHTTP_AVAILABLE:
        return False
    return bool(os.getenv("NAPCAT_WS_URL") and os.getenv("NAPCAT_HTTP_URL"))


def validate_config(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    ws = os.getenv("NAPCAT_WS_URL") or extra.get("ws_url", "")
    http = os.getenv("NAPCAT_HTTP_URL") or extra.get("http_url", "")
    return bool(ws and http)


def is_connected(config) -> bool:
    return validate_config(config)


def _env_enablement():
    """Seed PlatformConfig.extra from env vars before adapter is built."""
    ws = os.getenv("NAPCAT_WS_URL", "").strip()
    http = os.getenv("NAPCAT_HTTP_URL", "").strip()
    if not (ws and http):
        return None
    seed: dict = {"ws_url": ws, "http_url": http}
    if os.getenv("NAPCAT_ACCESS_TOKEN"):
        seed["access_token"] = os.getenv("NAPCAT_ACCESS_TOKEN")
    if os.getenv("NAPCAT_SELF_ID"):
        seed["self_id"] = os.getenv("NAPCAT_SELF_ID")
    home = os.getenv("NAPCAT_HOME_CHANNEL", "").strip()
    if home:
        kind, num = _parse_chat_id(home)
        seed["home_channel"] = {
            "chat_id": num if kind == "user" else f"group:{num}",
            "name": f"{'群' if kind == 'group' else 'QQ'}{num}",
        }
    return seed


def interactive_setup() -> None:
    """`hermes gateway setup` flow."""
    from hermes_cli.setup import (
        prompt, prompt_yes_no, save_env_value, get_env_value,
        print_header, print_info, print_warning, print_success,
    )
    print_header("NapCat (QQ)")
    existing = get_env_value("NAPCAT_WS_URL")
    if existing and not prompt_yes_no(f"NapCat already configured (WS: {existing}). Reconfigure?", False):
        return
    print_info("Connect Hermes to a NapCatQQ instance via OneBot v11.")
    print_info("Configure NapCat first: WebUI → OneBot 配置 → add HTTP + WebSocket Server.")
    print()
    ws = prompt("WebSocket Server URL", default=existing or "ws://127.0.0.1:3001")
    if not ws:
        print_warning("WebSocket URL required — skipping")
        return
    save_env_value("NAPCAT_WS_URL", ws.strip())
    http = prompt("HTTP API URL", default=get_env_value("NAPCAT_HTTP_URL") or "http://127.0.0.1:3000")
    save_env_value("NAPCAT_HTTP_URL", http.strip())
    token = prompt("Access token", default=get_env_value("NAPCAT_ACCESS_TOKEN") or "", password=True)
    save_env_value("NAPCAT_ACCESS_TOKEN", token.strip())
    self_id = prompt("Bot QQ number (leave blank to auto-detect)",
                     default=get_env_value("NAPCAT_SELF_ID") or "")
    if self_id:
        save_env_value("NAPCAT_SELF_ID", self_id.strip())
    print()
    print_info("🔒 Access control")
    if prompt_yes_no("Allow all users to message the bot? (dev only)", False):
        save_env_value("NAPCAT_ALLOW_ALL_USERS", "true")
    else:
        save_env_value("NAPCAT_ALLOW_ALL_USERS", "false")
        allowed = prompt("Allowed user QQ numbers (comma-separated, blank = deny all DMs)",
                         default=get_env_value("NAPCAT_ALLOWED_USERS") or "")
        save_env_value("NAPCAT_ALLOWED_USERS", allowed.replace(" ", ""))
        groups = prompt("Allowed group numbers (comma-separated, blank = all)",
                        default=get_env_value("NAPCAT_ALLOWED_GROUPS") or "")
        save_env_value("NAPCAT_ALLOWED_GROUPS", groups.replace(" ", ""))
    if prompt_yes_no("Only respond in groups when @-mentioned?", True):
        save_env_value("NAPCAT_GROUP_RESPOND_ONLY_AT_ME", "true")
    else:
        save_env_value("NAPCAT_GROUP_RESPOND_ONLY_AT_ME", "false")
    print_success("NapCat configuration saved to ~/.hermes/.env")
    print_info("Restart the gateway: hermes gateway restart")


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    """Out-of-process delivery for cron / send_message tool."""
    extra = getattr(pconfig, "extra", {}) or {}
    http_url = (os.getenv("NAPCAT_HTTP_URL") or extra.get("http_url", "")).rstrip("/")
    access_token = os.getenv("NAPCAT_ACCESS_TOKEN") or extra.get("access_token", "")
    if not http_url:
        return {"error": "NAPCAT_HTTP_URL not configured"}
    if not AIOHTTP_AVAILABLE:
        return {"error": "aiohttp not installed"}

    kind, num = _parse_chat_id(chat_id)
    if not num:
        return {"error": f"invalid chat_id: {chat_id}"}

    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async def _post(action: str, params: dict) -> Optional[dict]:
            try:
                async with session.post(f"{http_url}/{action}", json=params,
                                        headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    payload = await resp.json()
                    if payload.get("status") == "failed":
                        return None
                    return payload.get("data") or {}
            except Exception as e:
                logger.warning("NapCat standalone %s failed: %s", action, e)
                return None

        # Send text first
        text_segments = [{"type": "text", "data": {"text": message}}] if message else []
        if text_segments:
            if kind == "group":
                await _post("send_group_msg",
                            {"group_id": int(num), "message": text_segments})
            else:
                await _post("send_private_msg",
                            {"user_id": int(num), "message": text_segments})

        # Send media as separate messages
        if media_files:
            for path in media_files:
                file_arg = NapCatAdapter._normalize_media_file(path)
                # Guess type by extension
                low = path.lower()
                if any(low.endswith(e) for e in (".png", ".jpg", ".jpeg", ".gif",
                                                  ".webp", ".bmp")) and not force_document:
                    seg = [{"type": "image", "data": {"file": file_arg}}]
                    action = "send_group_msg" if kind == "group" else "send_private_msg"
                    key = "group_id" if kind == "group" else "user_id"
                    await _post(action, {key: int(num), "message": seg})
                elif any(low.endswith(e) for e in (".mp3", ".wav", ".silk", ".amr")):
                    seg = [{"type": "record", "data": {"file": file_arg}}]
                    action = "send_group_msg" if kind == "group" else "send_private_msg"
                    key = "group_id" if kind == "group" else "user_id"
                    await _post(action, {key: int(num), "message": seg})
                else:
                    import os as _os
                    fname = _os.path.basename(path)
                    if kind == "group":
                        await _post("upload_group_file",
                                    {"group_id": int(num), "file": file_arg, "name": fname})
                    else:
                        await _post("upload_private_file",
                                    {"user_id": int(num), "file": file_arg, "name": fname})

    return {"success": True, "message_id": str(int(time.time() * 1000))}


def register(ctx):
    """Plugin entry point — called by Hermes plugin system."""
    ctx.register_platform(
        name="napcat",
        label="NapCat (QQ)",
        adapter_factory=lambda cfg: NapCatAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["NAPCAT_WS_URL", "NAPCAT_HTTP_URL"],
        install_hint="pip install aiohttp  (already a Hermes dependency)",
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="NAPCAT_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="NAPCAT_ALLOWED_USERS",
        allow_all_env="NAPCAT_ALLOW_ALL_USERS",
        max_message_length=4500,
        emoji="🐧",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are on QQ via NapCat (OneBot v11). Each user has a real "
            "QQ number visible as source.user_id; groups have a group number "
            "as source.chat_id. In groups, you only receive messages when "
            "@-mentioned (by default). You can address group members back by "
            "embedding [CQ:at,qq=NUMBER] in your text, or just call them by "
            "the nickname in source.user_name. Keep replies natural and "
            "conversational — QQ supports plain text and basic emoji."
        ),
    )
