"""
Smart Scammer Reporter + Auto-Add System - FIXED VERSION
"""
import os
import asyncio
import base64
import json
import time
import threading
import traceback
from datetime import datetime, date

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# ============================================================
# CONFIG
# ============================================================
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO = os.environ.get('GITHUB_REPO', '')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
GITHUB_DB_FILE = os.environ.get('GITHUB_DB_FILE', 'db.json')

TELEGRAM_API_ID = int(os.environ.get('TELEGRAM_API_ID', 0))
TELEGRAM_API_HASH = os.environ.get('TELEGRAM_API_HASH', '')
TARGET_GROUP = os.environ.get('TARGET_GROUP', 'https://t.me/abe_armygrou')

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
        if not self._available:
            print("⚠️  GitHub credentials missing - using local-only storage")
            self._local_path = '/tmp/db.json'

    def _local_read(self):
        try:
            with open(self._local_path, 'r') as f:
                return json.load(f)
        except:
            return self._default_db()

    def _local_write(self, data):
        try:
            with open(self._local_path, 'w') as f:
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
            'added_user_ids': []
        }

    def _fetch_sha(self):
        try:
            r = requests.get(f"{self.base_url}?ref={GITHUB_BRANCH}",
                             headers=self.headers, timeout=10)
            if r.status_code == 200:
                return r.json().get('sha')
        except Exception as e:
            print(f"SHA fetch error: {e}")
        return None

    def read(self, force=False):
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
                    self._write(data)
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
                print(f"GitHub read error: {r.status_code}")
                return self._cache or self._default_db()
            except Exception as e:
                print(f"GitHub read exception: {e}")
                return self._cache or self._default_db()

    def _write(self, data, message="Update db"):
        with self._lock:
            if not self._available:
                self._local_write(data)
                self._cache = data
                self._cache_time = time.time()
                return True
            for attempt in range(4):
                try:
                    content = json.dumps(data, indent=2)
                    encoded = base64.b64encode(content.encode()).decode()
                    sha = self._last_sha or self._fetch_sha()
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
                    elif r.status_code == 409:
                        print(f"⚠️ conflict (attempt {attempt+1}), refreshing SHA...")
                        time.sleep(0.5)
                        self._last_sha = self._fetch_sha()
                    else:
                        print(f"GitHub write error: {r.status_code} - {r.text[:200]}")
                        time.sleep(0.5)
                except Exception as e:
                    print(f"GitHub write exception: {e}")
                    time.sleep(0.5)
            return False

    def update(self, updater_func, message="Update db"):
        with self._lock:
            data = self.read(force=True)
            updater_func(data)
            return self._write(data, message)

    def invalidate(self):
        with self._lock:
            self._cache = None
            self._cache_time = 0

db = GitHubDB()

# ============================================================
# ✅ FIXED ASYNC HELPER — one fresh loop per call, per-thread
# ============================================================
def run_async(coro):
    """
    Runs an async coroutine synchronously.
    Creates a NEW event loop in the CURRENT thread every time.
    This avoids all cross-thread event loop bugs.
    """
    # If there's already a loop running in this thread, we can't run_until_complete.
    # Spin up a worker thread that owns its own fresh loop.
    try:
        asyncio.get_running_loop()
        # We're inside an existing running loop — use a thread
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
        t = threading.Thread(target=runner)
        t.start()
        t.join(timeout=90)
        if 'error' in result_box:
            raise result_box['error']
        if 'result' not in result_box:
            raise TimeoutError("Async operation timed out after 90s")
        return result_box['result']
    except RuntimeError:
        # No loop running in this thread — safe to create one
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
        await client(JoinChannelRequest(entity))
        return {'success': True}
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
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try: await client.disconnect()
            except: pass

async def tg_send_code(phone):
    client = TelegramClient(StringSession(), TELEGRAM_API_ID, TELEGRAM_API_HASH)
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
        await client.disconnect()

async def tg_verify_code(session_string, phone, code, phone_code_hash, password=None):
    client = TelegramClient(StringSession(session_string),
                            TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code,
                                 phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                return {'success': False, 'need_password': True,
                        'error': '2FA required'}
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
        await client.disconnect()

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

async def tg_get_group_members(session_string, group_link, limit=50):
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
            'first_name': m.first_name, 'last_name': m.last_name
        } for m in members if not m.bot and not getattr(m, 'deleted', False)]
        return {'success': True, 'members': result}
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
            return {'success': False}
        group = await client.get_entity(group_link)
        user = await client.get_entity(user_id_or_username)
        await client(InviteToChannelRequest(channel=group, users=[user]))
        return {'success': True}
    except Exception as e:
        return {'success': False, 'error': str(e)[:100]}
    finally:
        if client:
            try: await client.disconnect()
            except: pass

# ============================================================
# MASS REPORT
# ============================================================
def mass_report(username, reason='scam', message=''):
    data = db.read(force=True)
    accounts = [a for a in data.get('accounts', [])
                if a.get('active', True) and a.get('session_string')]
    if not accounts:
        return {'success': False, 'error': 'No active accounts available'}
    username = username.lstrip('@').strip()
    if not username:
        return {'success': False, 'error': 'Invalid username'}
    print(f"🚨 MASS REPORT: @{username} from {len(accounts)} accounts")
    report_id = f"rpt_{int(time.time() * 1000)}"
    results = []
    successful = 0
    failed = 0
    for acc in accounts:
        try:
            r = run_async(tg_report_user(acc['session_string'],
                                         username, reason, message))
            if r.get('success'):
                successful += 1
                results.append({'account': acc.get('name', acc['id']),
                                'success': True})
            else:
                failed += 1
                results.append({'account': acc.get('name', acc['id']),
                                'success': False,
                                'error': r.get('error', 'unknown')})
        except Exception as e:
            failed += 1
            results.append({'account': acc.get('name', acc['id']),
                            'success': False, 'error': str(e)[:100]})
        time.sleep(0.5)

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
        d['history'] = d['history'][-500:]
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

def schedule_auto_join(account_id):
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
            _auto_join_pending.discard(account_id)

    threading.Thread(target=worker, daemon=True).start()

# ============================================================
# AUTO-ADD WORKER
# ============================================================
_auto_add_thread = None
_auto_add_stop = threading.Event()

def _auto_add_loop():
    print("🔄 Auto-add worker started")
    # Ensure every account has joined target
    _ensure_all_joined()

    while not _auto_add_stop.is_set():
        try:
            data = db.read(force=True)
            config = data.get('config', {})
            if not config.get('auto_add_enabled'):
                time.sleep(10)
                continue

            target = config.get('target_group', TARGET_GROUP)
            delay = max(30, int(config.get('delay_seconds', 30)))

            accounts = [a for a in data.get('accounts', [])
                        if a.get('active', True) and a.get('session_string')
                        and a.get('auto_add_enabled', False)]
            if not accounts:
                # No per-account auto-add enabled? Fall back to all active if global is on
                accounts = [a for a in data.get('accounts', [])
                            if a.get('active', True) and a.get('session_string')]
            if not accounts:
                time.sleep(15)
                continue

            # Use the first account to fetch members
            fetcher = accounts[0]
            try:
                members_res = run_async(tg_get_group_members(
                    fetcher['session_string'], target, limit=100))
            except Exception as e:
                print(f"Get members error: {e}")
                time.sleep(15)
                continue

            if not members_res.get('success'):
                print(f"Get members failed: {members_res.get('error')}")
                time.sleep(20)
                continue

            data = db.read(force=True)  # refresh added_ids
            added_ids = set(data.get('added_user_ids', []))
            # Skip our own accounts + already added + non-username (some can't be invited)
            own_ids = {a['id'] for a in data.get('accounts', [])}
            candidates = [
                m for m in members_res['members']
                if m['id'] not in added_ids
                and m['id'] not in own_ids
                and m.get('username')  # only public usernames are reliably invitable
            ][:3]

            if not candidates:
                print("😴 No new members to add. Sleeping 60s...")
                time.sleep(60)
                continue

            for m in candidates:
                if _auto_add_stop.is_set():
                    break
                target_user = m.get('username') or m['id']
                any_success = False
                for a in accounts:
                    try:
                        r = run_async(_add_user(a['session_string'], target, target_user))
                        if r.get('success'):
                            any_success = True
                    except Exception as e:
                        print(f"add user {target_user} by {a['id']} failed: {e}")
                    time.sleep(2)

                def _upd(d):
                    d.setdefault('added_user_ids', []).append(m['id'])
                    d['added_user_ids'] = d['added_user_ids'][-2000:]
                    d.setdefault('stats', {})
                    d['stats']['added_today'] = d['stats'].get('added_today', 0) + (1 if any_success else 0)
                    d['stats']['added_total'] = d['stats'].get('added_total', 0) + (1 if any_success else 0)
                db.update(_upd, f"Auto-add {target_user}")
                print(f"➕ {'Added' if any_success else 'Failed to add'} @{target_user}")
                time.sleep(delay)
        except Exception as e:
            print(f"Auto-add loop error: {e}")
            traceback.print_exc()
            time.sleep(15)
    print("🛑 Auto-add worker stopped")

def _ensure_all_joined():
    """At startup, make sure every account has joined the target group."""
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
            time.sleep(2)
    except Exception as e:
        print(f"_ensure_all_joined error: {e}")

def start_auto_add_worker():
    global _auto_add_thread
    if _auto_add_thread is None or not _auto_add_thread.is_alive():
        _auto_add_stop.clear()
        _auto_add_thread = threading.Thread(target=_auto_add_loop, daemon=True)
        _auto_add_thread.start()

# ============================================================
# STATS
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
    db.update(_upd, "Daily reset")

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
        account_id = request.json.get('accountId')
        def _upd(d):
            d['accounts'] = [a for a in d.get('accounts', [])
                             if a['id'] != account_id]
        db.update(_upd, f"Remove account {account_id}")
        return jsonify({'success': True, 'message': 'Account removed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

_pending_logins = {}
_pending_logins_lock = threading.Lock()

@app.route('/api/send-code', methods=['POST'])
def api_send_code():
    try:
        phone = (request.json.get('phone') or '').strip()
        if not phone.startswith('+'):
            phone = '+' + phone.lstrip('+')
        if not phone or len(phone) < 8:
            return jsonify({'success': False, 'error': 'Invalid phone'}), 400
        result = run_async(tg_send_code(phone))
        if not result.get('success'):
            return jsonify(result), 400
        with _pending_logins_lock:
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
        data = request.json
        session_id = data.get('session_id')
        code = (data.get('code') or '').strip()
        password = data.get('password') or None
        with _pending_logins_lock:
            pending = _pending_logins.get(session_id)
        if not pending:
            return jsonify({'success': False,
                            'error': 'Session expired. Request a new code.'}), 400

        # CRITICAL FIX: make sure we don't hang forever, surface errors
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

        def _upd(d):
            d['accounts'] = [a for a in d.get('accounts', [])
                             if a['id'] != account_id]
            d['accounts'].append({
                'id': account_id, 'name': name, 'phone': pending['phone'],
                'username': user.get('username', ''),
                'session_string': result['session_string'],
                'active': True, 'auto_add_enabled': False,
                'joined_target': False,
                'added_at': datetime.now().isoformat(),
                'stats': {'report_today': 0, 'report_total': 0,
                          'add_today': 0, 'add_total': 0}
            })

        saved = db.update(_upd, f"Add account {name}")
        if not saved:
            # CRITICAL: surface the failure to the frontend
            return jsonify({
                'success': False,
                'error': 'Account verified but failed to save to DB. '
                         'Check GitHub token/repo config.'
            }), 500

        # Fire-and-forget auto-join
        schedule_auto_join(account_id)

        return jsonify({
            'success': True,
            'account': {'id': account_id, 'name': name,
                        'phone': pending['phone']}
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/report-scammer', methods=['POST'])
def api_report_scammer():
    try:
        data = request.json
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
        account_id = request.json.get('accountId')
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
        new_config = request.json
        def _upd(d):
            d.setdefault('config', {}).update(new_config)
        db.update(_upd, "Update config")
        return jsonify({'success': True, 'config': new_config})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/auto-add/toggle', methods=['POST'])
def api_auto_add_toggle():
    try:
        enabled = bool(request.json.get('enabled'))
        account_id = request.json.get('accountId')
        def _upd(d):
            if account_id:
                for a in d.get('accounts', []):
                    if a['id'] == account_id:
                        a['auto_add_enabled'] = enabled
                        break
            else:
                d.setdefault('config', {})['auto_add_enabled'] = enabled
        db.update(_upd, f"Toggle auto-add {enabled}")
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
            'server_name': os.environ.get('RENDER_SERVICE_NAME', 'local')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def _bootstrap():
    try:
        db.read(force=True)
        print("✅ DB loaded")
    except Exception as e:
        print(f"DB init error: {e}")
    start_auto_add_worker()
    print("✅ Workers started")

_bootstrap()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False)
