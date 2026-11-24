"""
Smart Scammer Reporter + Auto-Add System
Single-file server for Render free tier (512MB)
All data persisted to GitHub via db.json
"""
import os
import asyncio
import base64
import json
import time
import threading
import traceback
from datetime import datetime, date
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests

# ============================================================
# CONFIG
# ============================================================
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO = os.environ.get('GITHUB_REPO', '')
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', '')
GITHUB_DB_FILE = os.environ.get('GITHUB_DB_FILE', '')

TELEGRAM_API_ID = int(os.environ.get('TELEGRAM_API_ID', 0))
TELEGRAM_API_HASH = os.environ.get('TELEGRAM_API_HASH', '')
TARGET_GROUP = os.environ.get('TARGET_GROUP', '')

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# ============================================================
# GITHUB STORAGE (db.json persistence)
# ============================================================
class GitHubDB:
    """Persistent storage on GitHub - survives Render restarts."""
    
    def __init__(self):
        self.base_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DB_FILE}"
        self.headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "SmartReporter"
        }
        self._cache = None
        self._cache_time = 0
        self._cache_ttl = 15  # seconds
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
                'reports_today': 0,
                'reports_total': 0,
                'added_today': 0,
                'added_total': 0,
                'last_date': date.today().isoformat()
            }
        }
    
    def _fetch_sha(self):
        try:
            r = requests.get(
                f"{self.base_url}?ref={GITHUB_BRANCH}",
                headers=self.headers,
                timeout=10
            )
            if r.status_code == 200:
                return r.json().get('sha')
        except Exception as e:
            print(f"SHA fetch error: {e}")
        return None
    
    def read(self, force=False):
        """Read db.json from GitHub"""
        if not self._available:
            return self._local_read()
        
        with self._lock:
            if not force and self._cache and (time.time() - self._cache_time < self._cache_ttl):
                return self._cache
            
            try:
                r = requests.get(
                    f"{self.base_url}?ref={GITHUB_BRANCH}",
                    headers=self.headers,
                    timeout=10
                )
                if r.status_code == 404:
                    # File doesn't exist - create it
                    data = self._default_db()
                    self._write(data)
                    return data
                if r.status_code == 200:
                    resp = r.json()
                    self._last_sha = resp.get('sha')
                    content = base64.b64decode(resp['content']).decode('utf-8')
                    data = json.loads(content)
                    # Merge with defaults to handle schema changes
                    default = self._default_db()
                    for k, v in default.items():
                        if k not in data:
                            data[k] = v
                    self._cache = data
                    self._cache_time = time.time()
                    return data
                else:
                    print(f"GitHub read error: {r.status_code}")
                    return self._cache or self._default_db()
            except Exception as e:
                print(f"GitHub read exception: {e}")
                return self._cache or self._default_db()
    
    def _write(self, data, message="Update db"):
        """Write db.json to GitHub"""
        with self._lock:
            if not self._available:
                self._local_write(data)
                self._cache = data
                self._cache_time = time.time()
                return True
            
            try:
                content = json.dumps(data, indent=2)
                encoded = base64.b64encode(content.encode()).decode()
                
                sha = self._last_sha or self._fetch_sha()
                
                payload = {
                    "message": message,
                    "content": encoded,
                    "branch": GITHUB_BRANCH
                }
                if sha:
                    payload["sha"] = sha
                
                r = requests.put(
                    self.base_url,
                    headers=self.headers,
                    json=payload,
                    timeout=15
                )
                
                if r.status_code in (200, 201):
                    self._last_sha = r.json().get('content', {}).get('sha')
                    self._cache = data
                    self._cache_time = time.time()
                    return True
                else:
                    print(f"GitHub write error: {r.status_code} - {r.text[:200]}")
                    # Retry once with fresh sha
                    if r.status_code == 409:
                        self._last_sha = self._fetch_sha()
                        if self._last_sha:
                            payload["sha"] = self._last_sha
                            r2 = requests.put(self.base_url, headers=self.headers, json=payload, timeout=15)
                            if r2.status_code in (200, 201):
                                self._last_sha = r2.json().get('content', {}).get('sha')
                                self._cache = data
                                self._cache_time = time.time()
                                return True
                    return False
            except Exception as e:
                print(f"GitHub write exception: {e}")
                return False
    
    def update(self, updater_func, message="Update db"):
        """Atomically update db with a function"""
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
# ASYNC HELPER
# ============================================================
_loop = None
_loop_lock = threading.Lock()

def get_loop():
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
        return _loop

def run_async(coro):
    loop = get_loop()
    if loop.is_running():
        # Run in a new thread with its own loop
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
        t.join(timeout=120)
        if 'error' in result_box:
            raise result_box['error']
        return result_box.get('result')
    return loop.run_until_complete(coro)

# ============================================================
# TELEGRAM OPERATIONS
# ============================================================
def _make_client(session_string):
    from telethon import TelegramClient
    return TelegramClient(
        session_string,
        TELEGRAM_API_ID,
        TELEGRAM_API_HASH,
        connection_retries=2,
        retry_delay=1,
        timeout=30,
        request_retries=2
    )


async def tg_join_group(session_string, group_link):
    """Join a group using stored session"""
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
            try:
                await client.disconnect()
            except:
                pass


async def tg_report_user(session_string, username, reason='scam', message=''):
    """Report a user from one account"""
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
        
        target = username.lstrip('@').strip()
        try:
            entity = await client.get_entity(target)
        except Exception as e:
            return {'success': False, 'error': f'Cannot find: {str(e)[:80]}'}
        
        reason_obj = reason_map.get(reason, InputReportReasonSpam())
        await client(ReportPeerRequest(
            peer=entity,
            reason=reason_obj,
            message=message or "Scam / fake account report"
        ))
        return {'success': True}
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def tg_send_code(phone):
    """Send login code"""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    
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
    """Verify login code and return session string"""
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    
    client = TelegramClient(StringSession(session_string), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                return {'success': False, 'need_password': True, 'error': '2FA required'}
            await client.sign_in(password=password)
        
        me = await client.get_me()
        new_session = client.session.save()
        
        return {
            'success': True,
            'session_string': new_session,
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
    """Get chat list"""
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
                'id': d.id,
                'title': d.name or 'Unknown',
                'type': t,
                'lastMessage': d.message.text if d.message and d.message.text else '',
                'lastMessageDate': int(d.message.date.timestamp()) if d.message else None,
                'lastMessageMedia': media,
                'unread': d.unread_count or 0
            })
        return {'success': True, 'chats': chats}
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


async def tg_get_group_members(session_string, group_link, limit=50):
    """Get members from target group for auto-add"""
    client = None
    try:
        client = _make_client(session_string)
        await client.connect()
        if not await client.is_user_authorized():
            return {'success': False, 'error': 'Not authorized'}
        
        entity = await client.get_entity(group_link)
        members = await client.get_participants(entity, limit=limit)
        
        result = [{
            'id': m.id,
            'username': m.username,
            'first_name': m.first_name,
            'last_name': m.last_name
        } for m in members if not m.bot and not m.deleted]
        
        return {'success': True, 'members': result}
    except Exception as e:
        return {'success': False, 'error': str(e)[:150]}
    finally:
        if client:
            try:
                await client.disconnect()
            except:
                pass


# ============================================================
# MASS REPORT ENGINE
# ============================================================
def mass_report(username, reason='scam', message=''):
    """
    Report a scammer using ALL available accounts.
    Returns aggregated results.
    """
    data = db.read()
    accounts = [a for a in data.get('accounts', []) if a.get('active', True) and a.get('session_string')]
    
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
    
    # Report from each account (sequentially to avoid rate limits, but fast)
    for acc in accounts:
        try:
            r = run_async(tg_report_user(
                acc['session_string'], username, reason, message
            ))
            if r.get('success'):
                successful += 1
                results.append({'account': acc.get('name', acc['id']), 'success': True})
                # Update account report stats
                _bump_account_stat(acc['id'], 'report')
            else:
                failed += 1
                results.append({
                    'account': acc.get('name', acc['id']),
                    'success': False,
                    'error': r.get('error', 'unknown')
                })
        except Exception as e:
            failed += 1
            results.append({
                'account': acc.get('name', acc['id']),
                'success': False,
                'error': str(e)[:100]
            })
        # Small delay between accounts to reduce flood-wait
        time.sleep(0.5)
    
    record = {
        'id': report_id,
        'username': username,
        'reason': reason,
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
        d['history'] = d['history'][-500:]
        d.setdefault('stats', {})
        d['stats']['reports_today'] = d['stats'].get('reports_today', 0) + 1
        d['stats']['reports_total'] = d['stats'].get('reports_total', 0) + 1
    db.update(_upd, f"Report @{username}")
    
    print(f"✅ Report done: {successful}/{len(accounts)} success")
    
    return {
        'success': True,
        'report_id': report_id,
        'username': username,
        'total_accounts': len(accounts),
        'successful': successful,
        'failed': failed,
        'message': f'Reported @{username} from {successful}/{len(accounts)} accounts'
    }


def _bump_account_stat(account_id, stat_type):
    """Increment a per-account stat"""
    def _upd(d):
        for acc in d.get('accounts', []):
            if acc['id'] == account_id:
                s = acc.setdefault('stats', {})
                s[f'{stat_type}_today'] = s.get(f'{stat_type}_today', 0) + 1
                s[f'{stat_type}_total'] = s.get(f'{stat_type}_total', 0) + 1
                break
    db.update(_upd, f"Bump {stat_type}")


# ============================================================
# AUTO-JOIN + AUTO-ADD WORKERS
# ============================================================
_auto_join_pending = set()

def schedule_auto_join(account_id):
    """Auto-join target group after account added"""
    if account_id in _auto_join_pending:
        return
    _auto_join_pending.add(account_id)
    
    def worker():
        try:
            # Small delay to let db settle
            time.sleep(2)
            data = db.read(force=True)
            acc = next((a for a in data.get('accounts', []) if a['id'] == account_id), None)
            if not acc or not acc.get('session_string'):
                return
            print(f"📥 Auto-joining {TARGET_GROUP} for account {account_id}")
            result = run_async(tg_join_group(acc['session_string'], TARGET_GROUP))
            
            def _upd(d):
                for a in d.get('accounts', []):
                    if a['id'] == account_id:
                        a['joined_target'] = bool(result.get('success'))
                        break
            db.update(_upd, f"Auto-join result {account_id}")
            print(f"📥 Auto-join: {'OK' if result.get('success') else 'FAILED'}")
        except Exception as e:
            print(f"Auto-join error: {e}")
        finally:
            _auto_join_pending.discard(account_id)
    
    threading.Thread(target=worker, daemon=True).start()


# Background auto-add loop
_auto_add_thread = None
_auto_add_stop = threading.Event()

def _auto_add_loop():
    """Background worker that adds users to target group"""
    print("🔄 Auto-add worker started")
    while not _auto_add_stop.is_set():
        try:
            data = db.read()
            config = data.get('config', {})
            if not config.get('auto_add_enabled'):
                time.sleep(10)
                continue
            
            target = config.get('target_group', TARGET_GROUP)
            delay = max(30, int(config.get('delay_seconds', 30)))
            
            accounts = [a for a in data.get('accounts', []) 
                       if a.get('active', True) and a.get('session_string')]
            
            if not accounts:
                time.sleep(15)
                continue
            
            # Get members from group to add
            acc = accounts[0]
            try:
                members = run_async(tg_get_group_members(acc['session_string'], target, limit=50))
                if members.get('success'):
                    # Track which we've tried
                    added_ids = set(data.get('added_user_ids', []))
                    new_members = [m for m in members['members'] if m['id'] not in added_ids][:5]
                    
                    for m in new_members:
                        if _auto_add_stop.is_set():
                            break
                        # Add from each account
                        for a in accounts:
                            try:
                                run_async(_add_user(a['session_string'], target, m['username'] or m['id']))
                            except:
                                pass
                            time.sleep(1)
                        
                        def _upd(d):
                            d.setdefault('added_user_ids', []).append(m['id'])
                            d['added_user_ids'] = d['added_user_ids'][-1000:]
                            d.setdefault('stats', {})
                            d['stats']['added_today'] = d['stats'].get('added_today', 0) + 1
                            d['stats']['added_total'] = d['stats'].get('added_total', 0) + 1
                        db.update(_upd, "Auto-add user")
                        
                        time.sleep(delay)
            except Exception as e:
                print(f"Auto-add cycle error: {e}")
            
            time.sleep(5)
        except Exception as e:
            print(f"Auto-add loop error: {e}")
            time.sleep(15)
    print("🛑 Auto-add worker stopped")


async def _add_user(session_string, group_link, user_id_or_username):
    """Add a single user to a group"""
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
            try:
                await client.disconnect()
            except:
                pass


def start_auto_add_worker():
    global _auto_add_thread
    if _auto_add_thread is None or not _auto_add_thread.is_alive():
        _auto_add_stop.clear()
        _auto_add_thread = threading.Thread(target=_auto_add_loop, daemon=True)
        _auto_add_thread.start()


# ============================================================
# STATS HELPERS
# ============================================================
def refresh_daily_stats():
    """Reset today's counters if date changed"""
    def _upd(d):
        s = d.setdefault('stats', {})
        today = date.today().isoformat()
        if s.get('last_date') != today:
            s['reports_today'] = 0
            s['added_today'] = 0
            s['last_date'] = today
            for acc in d.get('accounts', []):
                acc.setdefault('stats', {})['report_today'] = 0
                acc.setdefault('stats', {})['add_today'] = 0
    db.update(_upd, "Daily reset")


# ============================================================
# FLASK ROUTES
# ============================================================

# -------- Static pages --------
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


# -------- Health --------
@app.route('/api/health')
def api_health():
    try:
        data = db.read()
        return jsonify({
            'success': True,
            'github_connected': db._available,
            'accounts': len(data.get('accounts', [])),
            'server_name': os.environ.get('RENDER_SERVICE_NAME', 'local'),
            'server_time': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# -------- Accounts --------
@app.route('/api/accounts', methods=['GET'])
def api_accounts():
    try:
        data = db.read()
        accounts = []
        for a in data.get('accounts', []):
            # Strip session_string from response
            safe = {k: v for k, v in a.items() if k != 'session_string'}
            accounts.append(safe)
        return jsonify({'success': True, 'accounts': accounts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/remove-account', methods=['POST'])
def api_remove_account():
    try:
        account_id = request.json.get('accountId')
        def _upd(d):
            d['accounts'] = [a for a in d.get('accounts', []) if a['id'] != account_id]
        db.update(_upd, f"Remove account {account_id}")
        return jsonify({'success': True, 'message': 'Account removed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# -------- Auth flow --------
@app.route('/api/send-code', methods=['POST'])
def api_send_code():
    try:
        phone = request.json.get('phone', '').strip()
        if not phone.startswith('+'):
            phone = '+' + phone.lstrip('+')
        result = run_async(tg_send_code(phone))
        if not result.get('success'):
            return jsonify(result), 400
        # Store temp login session in memory
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
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/verify-code', methods=['POST'])
def api_verify_code():
    try:
        data = request.json
        session_id = data.get('session_id')
        code = data.get('code', '').strip()
        password = data.get('password') or None
        
        pending = _pending_logins.get(session_id)
        if not pending:
            return jsonify({'success': False, 'error': 'Session expired'}), 400
        
        result = run_async(tg_verify_code(
            session_id, pending['phone'], code,
            pending['phone_code_hash'], password
        ))
        
        if not result.get('success'):
            if result.get('need_password'):
                return jsonify({'success': False, 'need_password': True, 'error': '2FA password required'})
            return jsonify(result), 400
        
        # Save account to db
        user = result['user']
        account_id = user['id']
        name = f"{user['first_name']} {user['last_name']}".strip() or user['username'] or str(account_id)
        
        def _upd(d):
            # Remove if exists
            d['accounts'] = [a for a in d.get('accounts', []) if a['id'] != account_id]
            d['accounts'].append({
                'id': account_id,
                'name': name,
                'phone': pending['phone'],
                'username': user.get('username', ''),
                'session_string': result['session_string'],
                'active': True,
                'auto_add_enabled': False,
                'joined_target': False,
                'added_at': datetime.now().isoformat(),
                'stats': {
                    'report_today': 0, 'report_total': 0,
                    'add_today': 0, 'add_total': 0
                }
            })
        db.update(_upd, f"Add account {name}")
        
        _pending_logins.pop(session_id, None)
        
        # Auto-join target group
        schedule_auto_join(account_id)
        
        return jsonify({
            'success': True,
            'account': {'id': account_id, 'name': name, 'phone': pending['phone']}
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# Pending login sessions (in-memory, temp)
_pending_logins = {}


# -------- REPORT (MAIN FEATURE) --------
@app.route('/api/report-scammer', methods=['POST'])
def api_report_scammer():
    try:
        data = request.json
        username = data.get('username', '').strip()
        reason = data.get('reason', 'scam')
        message = data.get('message', '')
        
        if not username:
            return jsonify({'success': False, 'error': 'Username required'}), 400
        
        result = mass_report(username, reason, message)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/report-history', methods=['GET'])
def api_report_history():
    try:
        limit = int(request.args.get('limit', 50))
        data = db.read()
        history = data.get('history', [])[-limit:]
        return jsonify({'success': True, 'history': list(reversed(history))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# -------- Chats --------
@app.route('/api/get-chats', methods=['POST'])
def api_get_chats():
    try:
        account_id = request.json.get('accountId')
        data = db.read()
        acc = next((a for a in data.get('accounts', []) if a['id'] == account_id), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'}), 404
        result = run_async(tg_get_chats(acc['session_string']))
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# -------- Config / Auto-add --------
@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    try:
        if request.method == 'GET':
            data = db.read()
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


# -------- Stats --------
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


# ============================================================
# STARTUP
# ============================================================
def _bootstrap():
    """Initialize db and start background workers"""
    try:
        # Ensure db exists
        db.read(force=True)
        print("✅ DB loaded")
    except Exception as e:
        print(f"DB init error: {e}")
    
    # Start auto-add worker
    start_auto_add_worker()
    print("✅ Workers started")


# Start background worker at import
_bootstrap()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False)
