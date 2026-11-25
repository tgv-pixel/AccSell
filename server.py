"""
Smart Scammer Reporter + Auto-Add System - v3.1
Features:
  - Persistent GitHub DB (SHA-safe, no silent empty-DB wipe)
  - Logout / bulk-remove from DB
  - 12h auto-cleanup (never stops worker)
  - Aggressive mass-report (parallel, retries, rotating reasons)
  - Protected auto-add (jitter, per-account caps, warm-up, cool-down)
"""
import os
import asyncio
import base64
import json
import random
import time
import threading
import traceback
from datetime import datetime, date, timedelta
from collections import defaultdict

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError, UserPrivacyRestrictedError,
    UserNotMutualContactError, PeerFloodError, ChatAdminRequiredError,
    UserAlreadyParticipantError, UserChannelsTooMuchError
)

# ============================================================
# CONFIG
# ============================================================
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO = os.environ.get('GITHUB_REPO', '')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
GITHUB_DB_FILE = os.environ.get('GITHUB_DB_FILE', 'db.json')

TELEGRAM_API_ID = int(os.environ.get('TELEGRAM_API_ID', 0))
TELEGRAM_API_HASH = os.environ.get('TELEGRAM_API_HASH', '')
TARGET_GROUP = os.environ.get('TARGET_GROUP', 'https://t.me/oldetb')

# Safety tuning for auto-add (protected mode)
AUTO_ADD_MAX_PER_ACCOUNT_PER_DAY = int(os.environ.get('AUTO_ADD_MAX_PER_DAY', 20))
AUTO_ADD_WARMUP_MINUTES = int(os.environ.get('AUTO_ADD_WARMUP_MIN', 30))   # new account cools down
AUTO_ADD_JITTER_PCT = float(os.environ.get('AUTO_ADD_JITTER_PCT', 0.4))    # ±40% delay jitter
AUTO_ADD_MAX_ATTEMPTS_PER_USER = 2                                        # never spam same user
AUTO_ADD_RECENTLY_ADDED_SKIP_HRS = 6                                      # skip users added in last 6h
PENDING_LOGIN_TTL_SEC = 600                                               # 10 min OTP session lifetime

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
            "User-Agent": "SmartReporter"
        }
        self._cache = None
        self._cache_time = 0
        self._cache_ttl = 3
        self._lock = threading.RLock()
        self._last_sha = None
        self._available = bool(GITHUB_TOKEN and GITHUB_REPO)
        self._local_path = '/tmp/db.json'
        if not self._available:
            print("⚠️  GitHub credentials missing - using local-only storage")

    def _local_read(self):
        try:
            with open(self._local_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            default = self._default_db()
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception:
            return self._default_db()

    def _local_write(self, data):
        try:
            with open(self._local_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Local write error: {e}")

    def _default_db(self):
        return {
            'accounts': [],
            'config': {
                'target_group': TARGET_GROUP,
                'auto_add_enabled': False,
                'delay_seconds': 30,
                'report_reason': 'scam'
            },
            'reports': [],
            'history': [],
            'stats': {
                'reports_today': 0, 'reports_total': 0,
                'added_today': 0, 'added_total': 0,
                'last_date': date.today().isoformat()
            },
            'added_user_ids': [],
            'recently_added': [],   # {uid, ts} for skip window
            'last_cleanup': None
        }

    def _fetch_sha(self):
        try:
            r = requests.get(f"{self.base_url}?ref={GITHUB_BRANCH}",
                             headers=self.headers, timeout=10)
            if r.status_code == 200:
                sha = r.json().get('sha')
                if sha:
                    self._last_sha = sha
                return sha
        except Exception as e:
            print(f"SHA fetch error: {e}")
        return None

    def read(self, force=False, raise_on_error=False):
        if not self._available:
            return self._local_read()
        with self._lock:
            if not force and self._cache and (time.time() - self._cache_time < self._cache_ttl):
                return self._cache
            try:
                r = requests.get(f"{self.base_url}?ref={GITHUB_BRANCH}",
                                 headers=self.headers, timeout=10)
                if r.status_code == 404:
                    data = self._default_db()
                    if not self._write(data, "Initialize db.json"):
                        raise RuntimeError("Failed to initialize db.json on GitHub")
                    return data
                if r.status_code == 200:
                    resp = r.json()
                    self._last_sha = resp.get('sha')
                    content = base64.b64decode(resp['content']).decode('utf-8')
                    data = json.loads(content)
                    default = self._default_db()
                    for k, v in default.items():
                        if k not in data:
                            data[k] = v
                    self._cache = data
                    self._cache_time = time.time()
                    return data
                err = f"GitHub read error: {r.status_code} - {r.text[:150]}"
                print(err)
                if raise_on_error:
                    raise RuntimeError(err)
                return self._cache or self._default_db()
            except Exception as e:
                print(f"GitHub read exception: {e}")
                if raise_on_error:
                    raise
                return self._cache or self._default_db()

    def _write(self, data, message="Update db"):
        with self._lock:
            if not self._available:
                self._local_write(data)
                self._cache = data
                self._cache_time = time.time()
                return True
            for attempt in range(5):
                try:
                    content = json.dumps(data, indent=2)
                    encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
                    # Always fetch latest SHA first so parallel workers never overwrite/409
                    sha = self._fetch_sha() or self._last_sha
                    payload = {"message": message, "content": encoded, "branch": GITHUB_BRANCH}
                    if sha:
                        payload["sha"] = sha
                    r = requests.put(self.base_url, headers=self.headers,
                                     json=payload, timeout=15)
                    if r.status_code in (200, 201):
                        self._last_sha = r.json().get('content', {}).get('sha')
                        self._cache = data
                        self._cache_time = time.time()
                        return True
                    elif r.status_code in (409, 422):
                        print(f"⚠️ GitHub conflict ({r.status_code}) attempt {attempt+1}, refreshing SHA...")
                        self._last_sha = self._fetch_sha()
                        time.sleep(0.5 * (attempt + 1))
                    else:
                        print(f"GitHub write error: {r.status_code} - {r.text[:200]}")
                        time.sleep(0.5 * (attempt + 1))
                except Exception as e:
                    print(f"GitHub write exception: {e}")
                    time.sleep(0.5 * (attempt + 1))
            return False

    def update(self, updater_func, message="Update db"):
        with self._lock:
            # Never silently fall back to an empty default DB when updating
            data = self.read(force=True, raise_on_error=True)
            updater_func(data)
            return self._write(data, message)

    def invalidate(self):
        with self._lock:
            self._cache = None
            self._cache_time = 0

db = GitHubDB()

# ============================================================
# ASYNC HELPER (fresh loop per call, per thread)
# ============================================================
def run_async(coro):
    try:
        asyncio.get_running_loop()
        result_box = {}
        def runner():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                result_box['result'] = new_loop.run_until_complete(coro)
            except Exception as e:
                result_box['error'] = e
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)
        t = threading.Thread(target=runner)
        t.start()
        t.join(timeout=90)
        if 'error' in result_box:
            raise result_box['error']
        if 'result' not in result_box:
            raise TimeoutError("Async operation timed out after 90s")
        return result_box['result']
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
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
        timeout=30, request_retries=2
    )

async def tg_join_group(session_string, group_link):
    client = None
    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'error': 'Not authorized'}
        entity = await client.get_entity(group_link)
        from telethon.tl.functions.channels import JoinChannelRequest
        try:
            await client(JoinChannelRequest(entity))
        except UserAlreadyParticipantError:
            return {'success': True, 'already': True}
        return {'success': True}
    except FloodWaitError as e:
        return {'success': False, 'error': f'FloodWait {e.seconds}s', 'flood': e.seconds}
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        if client:
            try: await client.disconnect()
            except: pass

async def tg_report_user(session_string, username, reason='scam', message=''):
    client = None
    try:
        from telethon.tl.functions.account import ReportPeerRequest
        from telethon.tl.types import (
            InputReportReasonSpam, InputReportReasonFake,
            InputReportReasonViolence, InputReportReasonPornography,
            InputReportReasonChildAbuse, InputReportReasonIllegalDrugs,
            InputReportReasonPersonalDetails, InputReportReasonCopyright
        )
        reason_map = {
            'spam': InputReportReasonSpam(), 'fake': InputReportReasonFake(),
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
        target = username.lstrip('@').strip()
        try:
            entity = await client.get_entity(target)
        except Exception as e:
            return {'success': False, 'error': f'Cannot find: {str(e)[:80]}'}
        await client(ReportPeerRequest(
            peer=entity,
            reason=reason_map.get(reason, InputReportReasonSpam()),
            message=message or "Scam / fake account report"
        ))
        return {'success': True}
    except FloodWaitError as e:
        return {'success': False, 'error': f'FloodWait {e.seconds}s'}
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try: await client.disconnect()
            except: pass

async def tg_send_code(phone):
    client = _make_client("")
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        return {
            'success': True,
            'phone_code_hash': sent.phone_code_hash,
            'session': client.session.save(),
            'phone': phone
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        try: await client.disconnect()
        except: pass

async def tg_verify_code(session_string, phone, code, phone_code_hash, password=None):
    client = _make_client(session_string)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code,
                                 phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                return {'success': False, 'need_password': True, 'error': '2FA required'}
            await client.sign_in(password=password)
        me = await client.get_me()
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
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        try: await client.disconnect()
        except: pass

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
            if d.is_group: t = 'group'
            elif d.is_channel: t = 'channel'
            media = None
            if d.message and d.message.media:
                media = type(d.message.media).__name__.lower()
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
            try: await client.disconnect()
            except: pass

async def tg_get_group_members(session_string, group_link, limit=100):
    client = None
    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'error': 'Not authorized'}
        entity = await client.get_entity(group_link)
        members = await client.get_participants(entity, limit=limit)
        result = [{
            'id': m.id, 'username': m.username,
            'first_name': m.first_name, 'last_name': m.last_name,
            'bot': getattr(m, 'bot', False),
            'deleted': getattr(m, 'deleted', False),
            'mutual': getattr(m, 'mutual_contact', False),
        } for m in members]
        return {'success': True, 'members': result}
    except FloodWaitError as e:
        return {'success': False, 'error': f'FloodWait {e.seconds}s', 'flood': e.seconds}
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try: await client.disconnect()
            except: pass

async def _add_user(session_string, group_link, user_id_or_username):
    client = None
    try:
        from telethon.tl.functions.channels import InviteToChannelRequest
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'reason': 'not_authorized'}
        group = await client.get_entity(group_link)
        user = await client.get_entity(user_id_or_username)
        await client(InviteToChannelRequest(channel=group, users=[user]))
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
        return {'success': False, 'reason': str(e)[:80]}
    finally:
        if client:
            try: await client.disconnect()
            except: pass

# ============================================================
# AGGRESSIVE MASS REPORT ENGINE
# ============================================================
REPORT_REASONS_POOL = [
    'spam', 'scam', 'fake', 'spam', 'scam', 'fake',
    'violence', 'personal_details', 'copyright'
]

REPORT_MESSAGES_POOL = [
    "This account is a confirmed scam — please review and ban.",
    "Reports from many users. Violates Telegram ToS. Please remove.",
    "Fake / impersonation account used for fraud. Please ban.",
    "Scam account targeting innocent users. Needs immediate ban.",
    "This account has scammed multiple users. Please take action.",
    "Reported by community. Violates Telegram Terms of Service.",
    "Fraudulent account — please investigate activity and ban.",
]

def _pick_reason(primary):
    if random.random() < 0.55:
        return primary
    return random.choice(REPORT_REASONS_POOL)

def _pick_message(primary_message):
    if primary_message and random.random() < 0.5:
        return primary_message
    return random.choice(REPORT_MESSAGES_POOL)

def mass_report(username, reason='scam', message=''):
    data = db.read(force=True)
    accounts = [a for a in data.get('accounts', [])
                if a.get('active', True) and a.get('session_string')]
    if not accounts:
        return {'success': False, 'error': 'No active accounts available'}

    username = username.lstrip('@').strip()
    if not username:
        return {'success': False, 'error': 'Invalid username'}

    print(f"🚨 AGGRESSIVE MASS REPORT: @{username} from {len(accounts)} accounts")
    report_id = f"rpt_{int(time.time() * 1000)}"
    results = []
    successful = 0
    failed = 0

    def _run_one(acc, attempt=1):
        try:
            r_reason = _pick_reason(reason) if attempt > 1 else reason
            r_message = _pick_message(message) if attempt > 1 else message
            r = run_async(tg_report_user(acc['session_string'],
                                         username, r_reason, r_message))
            return acc, r
        except Exception as e:
            return acc, {'success': False, 'error': str(e)[:120]}

    def _parallel_batch(batch, attempt=1):
        threads = []
        box = []
        lock = threading.Lock()
        def _wrap(acc):
            _, r = _run_one(acc, attempt)
            with lock:
                box.append((acc, r))
        for acc in batch:
            t = threading.Thread(target=_wrap, args=(acc,))
            t.start()
            threads.append(t)
            time.sleep(random.uniform(0.3, 0.9))
        for t in threads:
            t.join(timeout=45)
        return box

    retry_queue = []
    for i in range(0, len(accounts), 6):
        batch = accounts[i:i+6]
        for acc, r in _parallel_batch(batch, attempt=1):
            if r.get('success'):
                successful += 1
                results.append({'account': acc.get('name', acc['id']), 'success': True})
            else:
                failed += 1
                results.append({
                    'account': acc.get('name', acc['id']),
                    'success': False,
                    'error': r.get('error', 'unknown')
                })
                if 'not authorized' not in (r.get('error') or '').lower():
                    retry_queue.append(acc)
        time.sleep(random.uniform(0.8, 1.5))

    if retry_queue:
        print(f"🔁 Retrying {len(retry_queue)} failed accounts with rotated reason...")
        for i in range(0, len(retry_queue), 5):
            batch = retry_queue[i:i+5]
            for acc, r in _parallel_batch(batch, attempt=2):
                if r.get('success'):
                    successful += 1
                    for row in results:
                        if row.get('account') == acc.get('name', acc['id']) and not row.get('success'):
                            row['success'] = True
                            row.pop('error', None)
                            failed -= 1
                            break
            time.sleep(random.uniform(1.0, 2.0))

    record = {
        'id': report_id, 'username': username, 'reason': reason,
        'message': message,
        'started_at': datetime.now().isoformat(),
        'completed_at': datetime.now().isoformat(),
        'total_accounts': len(accounts), 'successful': successful,
        'failed': failed, 'results': results[:100]
    }

    def _upd(d):
        d.setdefault('history', []).append(record)
        d['history'] = d['history'][-300:]
        d.setdefault('stats', {})
        d['stats']['reports_today'] = d['stats'].get('reports_today', 0) + 1
        d['stats']['reports_total'] = d['stats'].get('reports_total', 0) + 1
    db.update(_upd, f"Report @{username}")

    return {
        'success': True, 'report_id': report_id, 'username': username,
        'total_accounts': len(accounts), 'successful': successful,
        'failed': failed,
        'message': f'Reported @{username} from {successful}/{len(accounts)} accounts'
    }

# ============================================================
# AUTO-JOIN
# ============================================================
_auto_join_pending = set()
_auto_join_lock = threading.Lock()

def schedule_auto_join(account_id):
    with _auto_join_lock:
        if account_id in _auto_join_pending:
            return
        _auto_join_pending.add(account_id)

    def worker():
        try:
            time.sleep(5)
            data = db.read(force=True)
            acc = next((a for a in data.get('accounts', [])
                        if a['id'] == account_id), None)
            if not acc or not acc.get('session_string'):
                print(f"⚠️ Auto-join skipped: account {account_id} not in DB")
                return
            target = data.get('config', {}).get('target_group', TARGET_GROUP)
            print(f"📥 Auto-joining {target} for {account_id}")
            result = run_async(tg_join_group(acc['session_string'], target))
            ok = bool(result.get('success'))
            print(f"📥 Auto-join {'OK' if ok else 'FAILED: ' + str(result.get('error'))}")

            def _upd(d):
                for a in d.get('accounts', []):
                    if a['id'] == account_id:
                        a['joined_target'] = ok
                        break
            db.update(_upd, f"Auto-join result {account_id}")
        except Exception as e:
            print(f"Auto-join error: {e}")
            traceback.print_exc()
        finally:
            with _auto_join_lock:
                _auto_join_pending.discard(account_id)

    threading.Thread(target=worker, daemon=True).start()

# ============================================================
# PROTECTED AUTO-ADD WORKER
# ============================================================
_auto_add_thread = None
_auto_add_stop = threading.Event()

_account_state = defaultdict(lambda: {
    'added_today': 0,
    'day': date.today().isoformat(),
    'cool_until': 0.0,
    'consecutive_failures': 0,
    'last_add_ts': 0.0,
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
    return max(15, int(random.uniform(base_seconds - jitter, base_seconds + jitter)))

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
        s['cool_until'] = time.time() + 1800
        s['consecutive_failures'] = 0
        print(f"❄️  Account {acc['id']} cooled down 30min (too many failures)")
        return True
    return False

def _should_skip_member(m):
    if m.get('bot'): return True
    if m.get('deleted'): return True
    if not m.get('username'): return True
    return False

def _is_recently_added(uid, recently):
    cutoff = time.time() - AUTO_ADD_RECENTLY_ADDED_SKIP_HRS * 3600
    for row in recently:
        if row.get('uid') == uid and row.get('ts', 0) > cutoff:
            return True
    return False

def _auto_add_loop():
    print("🔄 Protected auto-add worker started")
    _ensure_all_joined()

    while not _auto_add_stop.is_set():
        try:
            data = db.read(force=True)
            config = data.get('config', {})
            if not config.get('auto_add_enabled'):
                time.sleep(10)
                continue

            target = config.get('target_group', TARGET_GROUP)
            base_delay = max(30, int(config.get('delay_seconds', 30)))

            candidates = []
            for a in data.get('accounts', []):
                if not (a.get('active', True) and a.get('session_string')):
                    continue
                if not a.get('joined_target'):
                    continue
                if _account_in_warmup(a):
                    continue
                if _account_rate_limited(a):
                    continue
                candidates.append(a)

            if not candidates:
                print("😴 No eligible accounts. Sleeping 60s...")
                time.sleep(60)
                continue

            random.shuffle(candidates)
            inviter = candidates[0]

            try:
                members_res = run_async(tg_get_group_members(
                    inviter['session_string'], target, limit=100))
            except Exception as e:
                print(f"Get members error: {e}")
                time.sleep(20)
                continue

            if not members_res.get('success'):
                if members_res.get('flood'):
                    wait = int(members_res['flood']) + 5
                    print(f"⏳ Flood while fetching members: sleeping {wait}s")
                    time.sleep(wait)
                else:
                    print(f"Get members failed: {members_res.get('error')}")
                    time.sleep(20)
                continue

            data = db.read(force=True)
            added_ids = set(data.get('added_user_ids', []))
            recently = data.get('recently_added', [])
            own_ids = {a['id'] for a in data.get('accounts', [])}

            candidates_members = []
            for m in members_res['members']:
                if m['id'] in own_ids: continue
                if m['id'] in added_ids: continue
                if _should_skip_member(m): continue
                if _is_recently_added(m['id'], recently): continue
                candidates_members.append(m)

            if not candidates_members:
                print("😴 No fresh members to add. Sleeping 90s...")
                time.sleep(90)
                continue

            added_this_cycle = 0
            for m in candidates_members[:2]:
                if _auto_add_stop.is_set(): break

                target_user = m.get('username')
                if not target_user:
                    continue

                ok = False
                for a in candidates:
                    if _account_rate_limited(a):
                        continue
                    s = _state_for(a['id'])
                    try:
                        r = run_async(_add_user(a['session_string'], target, target_user))
                        if r.get('success'):
                            ok = True
                            s['added_today'] += 1
                            s['consecutive_failures'] = 0
                            s['last_add_ts'] = time.time()
                            break
                        else:
                            reason = r.get('reason', '')
                            if r.get('flood'):
                                s['cool_until'] = time.time() + int(r['flood']) + 10
                            elif r.get('cool_down'):
                                s['cool_until'] = time.time() + 3600
                            elif reason in ('not_authorized',):
                                def _mark(d, aid=a['id']):
                                    for acc in d.get('accounts', []):
                                        if acc['id'] == aid:
                                            acc['active'] = False
                                            break
                                db.update(_mark, f"Mark inactive {a['id']}")
                            else:
                                s['consecutive_failures'] += 1
                    except Exception as e:
                        print(f"add error: {e}")
                        s['consecutive_failures'] += 1

                    time.sleep(_human_delay(base_delay // 2))

                if ok:
                    added_this_cycle += 1

                def _upd(d, uid=m['id'], uname=target_user, success=ok):
                    d.setdefault('added_user_ids', []).append(uid)
                    d['added_user_ids'] = d['added_user_ids'][-1500:]
                    d.setdefault('recently_added', []).append({
                        'uid': uid, 'ts': time.time(), 'username': uname,
                        'ok': success
                    })
                    cutoff = time.time() - 12 * 3600
                    d['recently_added'] = [x for x in d['recently_added']
                                           if x.get('ts', 0) > cutoff][-800:]
                    d.setdefault('stats', {})
                    if success:
                        d['stats']['added_today'] = d['stats'].get('added_today', 0) + 1
                        d['stats']['added_total'] = d['stats'].get('added_total', 0) + 1
                db.update(_upd, f"Auto-add {target_user}")
                print(f"➕ {'Added' if ok else 'Skipped/failed'} @{target_user}")

                time.sleep(_human_delay(base_delay))

            if added_this_cycle == 0:
                print("😴 0 added this cycle. Sleeping 60s...")
                time.sleep(60)

        except Exception as e:
            print(f"Auto-add loop error: {e}")
            traceback.print_exc()
            time.sleep(15)
    print("🛑 Auto-add worker stopped")

def _ensure_all_joined():
    try:
        data = db.read(force=True)
        target = data.get('config', {}).get('target_group', TARGET_GROUP)
        for acc in data.get('accounts', []):
            if acc.get('joined_target'):
                continue
            if not acc.get('session_string'):
                continue
            try:
                r = run_async(tg_join_group(acc['session_string'], target))
                ok = bool(r.get('success'))
                def _upd(d, aid=acc['id'], j=ok):
                    for a in d.get('accounts', []):
                        if a['id'] == aid:
                            a['joined_target'] = j
                            break
                db.update(_upd, f"Startup auto-join {acc['id']}")
                print(f"📥 Startup join {acc['id']}: {'OK' if ok else 'FAIL'}")
            except Exception as e:
                print(f"Startup join error for {acc['id']}: {e}")
            time.sleep(random.uniform(2, 4))
    except Exception as e:
        print(f"_ensure_all_joined error: {e}")

def start_auto_add_worker():
    global _auto_add_thread
    if _auto_add_thread is None or not _auto_add_thread.is_alive():
        _auto_add_stop.clear()
        _auto_add_thread = threading.Thread(target=_auto_add_loop, daemon=True)
        _auto_add_thread.start()

# ============================================================
# 12H CLEANUP WORKER (never stops add-worker)
# ============================================================
_cleanup_thread = None
_cleanup_stop = threading.Event()
CLEANUP_INTERVAL_SEC = 12 * 3600

def _run_cleanup_once():
    now = datetime.now()
    print(f"🧹 Cleanup start @ {now.isoformat()}")

    def _upd(d):
        if len(d.get('history', [])) > 100:
            d['history'] = d['history'][-100:]
        if len(d.get('reports', [])) > 100:
            d['reports'] = d['reports'][-100:]
        if len(d.get('added_user_ids', [])) > 800:
            d['added_user_ids'] = d['added_user_ids'][-800:]
        cutoff = time.time() - 12 * 3600
        d['recently_added'] = [x for x in d.get('recently_added', [])
                               if x.get('ts', 0) > cutoff][-500:]
        for rec in d.get('history', []):
            if len(rec.get('results', [])) > 50:
                rec['results'] = rec['results'][-50:]
        d['last_cleanup'] = now.isoformat()
    ok = db.update(_upd, "12h cleanup")
    print(f"🧹 Cleanup {'OK' if ok else 'FAILED'}")

def _cleanup_loop():
    print("🧹 Cleanup worker started")
    _cleanup_stop.wait(30)
    while not _cleanup_stop.is_set():
        try:
            _run_cleanup_once()
        except Exception as e:
            print(f"Cleanup error: {e}")
            traceback.print_exc()
        _cleanup_stop.wait(CLEANUP_INTERVAL_SEC)
    print("🛑 Cleanup worker stopped")

def start_cleanup_worker():
    global _cleanup_thread
    if _cleanup_thread is None or not _cleanup_thread.is_alive():
        _cleanup_stop.clear()
        _cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
        _cleanup_thread.start()

# ============================================================
# DAILY STATS RESET
# ============================================================
def refresh_daily_stats():
    def _upd(d):
        s = d.setdefault('stats', {})
        today = date.today().isoformat()
        if s.get('last_date') != today:
            s['reports_today'] = 0
            s['added_today'] = 0
            s['last_date'] = today
            for acc in d.get('accounts', []):
                acc.setdefault('stats', {})
                acc['stats']['report_today'] = 0
                acc['stats']['add_today'] = 0
    try:
        db.update(_upd, "Daily reset")
    except Exception as e:
        print(f"Daily reset skipped due to read error: {e}")

# ============================================================
# ROUTES
# ============================================================
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
        data = db.read(force=True)
        return jsonify({
            'success': True, 'github_connected': db._available,
            'accounts': len(data.get('accounts', [])),
            'auto_add_worker_alive': bool(_auto_add_thread and _auto_add_thread.is_alive()),
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
        accounts = [{k: v for k, v in a.items() if k != 'session_string'}
                    for a in data.get('accounts', [])]
        return jsonify({'success': True, 'accounts': accounts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/remove-account', methods=['POST'])
def api_remove_account():
    try:
        account_id = (request.json or {}).get('accountId')
        if not account_id:
            return jsonify({'success': False, 'error': 'accountId required'}), 400
        def _upd(d):
            d['accounts'] = [a for a in d.get('accounts', [])
                             if a['id'] != account_id]
        ok = db.update(_upd, f"Remove account {account_id}")
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
            d['accounts'] = [a for a in d.get('accounts', [])
                             if a['id'] not in id_set]
            removed = before - len(d['accounts'])
        ok = db.update(_upd, f"Bulk remove {len(ids)} accounts")
        if not ok:
            return jsonify({'success': False, 'error': 'DB write failed'}), 500
        for i in ids:
            _account_state.pop(i, None)
        return jsonify({'success': True, 'removed': removed,
                        'message': f'Removed {removed} account(s)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def api_logout():
    try:
        account_id = (request.json or {}).get('accountId')
        if not account_id:
            return jsonify({'success': False, 'error': 'accountId required'}), 400
        def _upd(d):
            d['accounts'] = [a for a in d.get('accounts', [])
                             if a['id'] != account_id]
        ok = db.update(_upd, f"Logout account {account_id}")
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

_pending_logins = {}
_pending_logins_lock = threading.Lock()

def _prune_pending_logins():
    now = time.time()
    expired = [k for k, v in _pending_logins.items() if now - v.get('created', 0) > PENDING_LOGIN_TTL_SEC]
    for k in expired:
        _pending_logins.pop(k, None)

@app.route('/api/send-code', methods=['POST'])
def api_send_code():
    try:
        body = request.json or {}
        phone = (body.get('phone') or '').strip()
        if not phone.startswith('+'):
            phone = '+' + phone.lstrip('+')
        if not phone or len(phone) < 8:
            return jsonify({'success': False, 'error': 'Invalid phone'}), 400
        result = run_async(tg_send_code(phone))
        if not result.get('success'):
            return jsonify(result), 400
        with _pending_logins_lock:
            _prune_pending_logins()
            _pending_logins[result['session']] = {
                'phone': phone,
                'phone_code_hash': result['phone_code_hash'],
                'created': time.time()
            }
        return jsonify({
            'success': True,
            'session_id': result['session'],
            'phone_masked': phone[:4] + '****' + phone[-2:]
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/verify-code', methods=['POST'])
def api_verify_code():
    try:
        data = request.json or {}
        session_id = data.get('session_id')
        code = (data.get('code') or '').strip()
        password = data.get('password') or None
        with _pending_logins_lock:
            _prune_pending_logins()
            pending = _pending_logins.get(session_id)
        if not pending:
            return jsonify({'success': False,
                            'error': 'Session expired. Request a new code.'}), 400

        result = run_async(tg_verify_code(
            session_id, pending['phone'], code,
            pending['phone_code_hash'], password
        ))
        if not result.get('success'):
            if result.get('need_password'):
                return jsonify({'success': False, 'need_password': True,
                                'error': '2FA password required'})
            return jsonify({'success': False,
                            'error': result.get('error', 'Invalid code')}), 400

        with _pending_logins_lock:
            _pending_logins.pop(session_id, None)

        user = result['user']
        account_id = user['id']
        name = (f"{user['first_name']} {user['last_name']}".strip()
                or user['username'] or str(account_id))

        new_account = {
            'id': account_id, 'name': name, 'phone': pending['phone'],
            'username': user.get('username', ''),
            'session_string': result['session_string'],
            'active': True, 'auto_add_enabled': False,
            'joined_target': False,
            'added_at': datetime.now().isoformat(),
            'stats': {'report_today': 0, 'report_total': 0,
                      'add_today': 0, 'add_total': 0}
        }

        def _upd(d):
            d['accounts'] = [a for a in d.get('accounts', [])
                             if a['id'] != account_id]
            d['accounts'].append(new_account)

        try:
            saved = db.update(_upd, f"Add account {name}")
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Account verified, but DB read/write failed: {str(e)[:150]}'
            }), 500

        if not saved:
            return jsonify({
                'success': False,
                'error': 'Account verified but failed to save to DB. Check GitHub token/repo.'
            }), 500

        # Confirm persisted account is actually present
        check = db.read(force=True)
        if not any(a.get('id') == account_id for a in check.get('accounts', [])):
            return jsonify({
                'success': False,
                'error': 'Account did not persist in database after save.'
            }), 500

        schedule_auto_join(account_id)
        return jsonify({
            'success': True,
            'redirect_url': '/report',
            'account': {'id': account_id, 'name': name, 'phone': pending['phone']}
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/report-scammer', methods=['POST'])
def api_report_scammer():
    try:
        data = request.json or {}
        username = (data.get('username') or '').strip()
        reason = data.get('reason', 'scam')
        message = data.get('message', '')
        if not username:
            return jsonify({'success': False, 'error': 'Username required'}), 400
        return jsonify(mass_report(username, reason, message))
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/report-history', methods=['GET'])
def api_report_history():
    try:
        limit = int(request.args.get('limit', 50))
        data = db.read(force=True)
        history = data.get('history', [])[-limit:]
        return jsonify({'success': True, 'history': list(reversed(history))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/get-chats', methods=['POST'])
def api_get_chats():
    try:
        account_id = (request.json or {}).get('accountId')
        data = db.read(force=True)
        acc = next((a for a in data.get('accounts', [])
                    if a['id'] == account_id), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'}), 404
        return jsonify(run_async(tg_get_chats(acc['session_string'])))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    try:
        if request.method == 'GET':
            data = db.read(force=True)
            return jsonify({'success': True, 'config': data.get('config', {})})
        new_config = request.json or {}
        merged = {}
        def _upd(d):
            nonlocal merged
            d.setdefault('config', {}).update(new_config)
            merged = dict(d['config'])
        ok = db.update(_upd, "Update config")
        if not ok:
            return jsonify({'success': False, 'error': 'DB write failed'}), 500
        return jsonify({'success': True, 'config': merged})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/auto-add/toggle', methods=['POST'])
def api_auto_add_toggle():
    try:
        body = request.json or {}
        enabled = bool(body.get('enabled'))
        account_id = body.get('accountId')
        def _upd(d):
            if account_id:
                for a in d.get('accounts', []):
                    if a['id'] == account_id:
                        a['auto_add_enabled'] = enabled
                        break
            else:
                d.setdefault('config', {})['auto_add_enabled'] = enabled
        ok = db.update(_upd, f"Toggle auto-add {enabled}")
        if not ok:
            return jsonify({'success': False, 'error': 'DB write failed'}), 500
        return jsonify({'success': True})
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
            'total_accounts': len(data.get('accounts', [])),
            'active_workers': 1 if data.get('config', {}).get('auto_add_enabled') else 0,
            'last_cleanup': data.get('last_cleanup'),
            'server_name': os.environ.get('RENDER_SERVICE_NAME', 'local')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# BOOTSTRAP
# ============================================================
def _bootstrap():
    try:
        db.read(force=True)
        print("✅ DB loaded")
    except Exception as e:
        print(f"DB init error: {e}")
    start_auto_add_worker()
    start_cleanup_worker()
    print("✅ Workers started")

_bootstrap()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False)
