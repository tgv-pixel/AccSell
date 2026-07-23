#!/usr/bin/env python3
"""
Telegram Scammer Report System - ENHANCED VERSION
Automated reporting system to ban/restrict scammers on Telegram
With session persistence, error recovery, and maximum impact reporting
"""

from flask import Flask, jsonify, request, redirect, render_template_string, Response
from flask_cors import CORS
from telethon import TelegramClient, errors, functions, types
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonPornography,
    InputReportReasonChildAbuse,
    InputReportReasonOther,
    InputReportReasonFake,
    InputReportReasonIllegalDrugs,
    InputReportReasonPersonalDetails,
    InputReportReasonCopyright,
    InputPeerUser,
    InputPeerChannel,
    InputPeerChat
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
import re
import urllib.parse
from queue import Queue
from threading import Lock

# ============================================
# LOGGING CONFIGURATION - ENHANCED
# ============================================
os.makedirs('logs', exist_ok=True)

# Configure rotating file handler with more backups
file_handler = logging.handlers.RotatingFileHandler(
    'logs/server.log',
    maxBytes=20*1024*1024,  # 20MB
    backupCount=10
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s'
))

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))

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
# CONFIGURATION
# ============================================
API_ID = int(os.environ.get('API_ID', '35894551'))
API_HASH = os.environ.get('API_HASH', '1886fc990cbf114bcd35055dfd300a30')
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7294379764:AAHAOQ1OVT2TJ0cRAlWhyyxXQdVB3oS9K_A')
PORT = int(os.environ.get('PORT', 10000))
SERVER_URL = os.environ.get('SERVER_URL', 'https://accsell.onrender.com')

# ============================================
# ENHANCED FILE PATHS
# ============================================
ACCOUNTS_FILE = 'accounts.json'
REPORTS_FILE = 'reports.json'
REPORT_STATS_FILE = 'report_stats.json'
TEMP_SESSIONS_FILE = 'temp_sessions.json'
SESSION_POOL_FILE = 'session_pool.json'
BLACKLIST_FILE = 'blacklist.json'

# ============================================
# ENHANCED STORAGE
# ============================================
accounts = []
temp_sessions = {}
reports = []
session_pool = {}  # Pool of active client connections
blacklist = set()  # Blacklisted scammers for instant check
report_stats = {
    'total_reports': 0,
    'today_reports': 0,
    'successful_reports': 0,
    'failed_reports': 0,
    'scammers_reported': 0,
    'last_reset': datetime.now().strftime('%Y-%m-%d'),
    'report_history': [],
    'banned_count': 0
}
file_lock = threading.Lock()
session_lock = threading.Lock()
blacklist_lock = threading.Lock()

# Report queue for async processing
report_queue = Queue()
MAX_QUEUE_SIZE = 1000

# ============================================
# ENHANCED REPORT REASONS
# ============================================
REPORT_REASONS = {
    'spam': {
        'name': 'Spam',
        'icon': '📧',
        'description': 'Unsolicited messages, advertising, or bulk messaging',
        'telegram_reason': InputReportReasonSpam(),
        'priority': 'high'
    },
    'fake': {
        'name': 'Fake Account',
        'icon': '🎭',
        'description': 'Impersonating someone else or fake identity',
        'telegram_reason': InputReportReasonFake(),
        'priority': 'high'
    },
    'violence': {
        'name': 'Violence',
        'icon': '⚠️',
        'description': 'Violent threats or content',
        'telegram_reason': InputReportReasonViolence(),
        'priority': 'critical'
    },
    'pornography': {
        'name': 'Inappropriate Content',
        'icon': '🔞',
        'description': 'Pornographic or adult content',
        'telegram_reason': InputReportReasonPornography(),
        'priority': 'medium'
    },
    'drugs': {
        'name': 'Illegal Drugs',
        'icon': '💊',
        'description': 'Selling or promoting illegal drugs',
        'telegram_reason': InputReportReasonIllegalDrugs(),
        'priority': 'critical'
    },
    'personal': {
        'name': 'Personal Info',
        'icon': '🔓',
        'description': 'Sharing personal information without consent',
        'telegram_reason': InputReportReasonPersonalDetails(),
        'priority': 'high'
    },
    'copyright': {
        'name': 'Copyright',
        'icon': '©️',
        'description': 'Copyright infringement',
        'telegram_reason': InputReportReasonCopyright(),
        'priority': 'medium'
    },
    'scam': {
        'name': 'Scam/Fraud',
        'icon': '💰',
        'description': 'Scam attempts, fraud, or financial deception',
        'telegram_reason': InputReportReasonOther(),
        'priority': 'critical'
    },
    'other': {
        'name': 'Other',
        'icon': '📋',
        'description': 'Other violations',
        'telegram_reason': InputReportReasonOther(),
        'priority': 'low'
    }
}

# ============================================
# ENHANCED FILE OPERATIONS WITH ATOMIC WRITES
# ============================================
def load_json(path, default):
    """Load JSON with backup recovery"""
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
                    logger.debug(f"Loaded {len(str(data))} bytes from {path}")
                    return data
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON file {path}: {e}")
        # Try to restore from backup
        backup_path = f"{path}.backup"
        if os.path.exists(backup_path):
            try:
                with open(backup_path, 'r') as backup:
                    logger.info(f"Restoring {path} from backup")
                    return json.load(backup)
            except:
                pass
    except Exception as e:
        logger.error(f"Load error {path}: {e}")
    return default

def save_json(path, data):
    """Save JSON with atomic write operation"""
    temp_path = f"{path}.tmp"
    with file_lock:
        try:
            # Write to temp file first
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            # Atomic replace
            os.replace(temp_path, path)
            logger.debug(f"Saved {len(str(data))} bytes to {path}")
        except Exception as e:
            logger.error(f"Save error {path}: {e}")
            # Try to save directly if atomic write fails
            try:
                with open(path, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
            except:
                pass

def load_reports():
    global reports
    reports = load_json(REPORTS_FILE, [])
    logger.info(f"Loaded {len(reports)} reports")

def save_reports():
    save_json(REPORTS_FILE, reports)

def load_report_stats():
    global report_stats
    stats_data = load_json(REPORT_STATS_FILE, {})
    if stats_data:
        report_stats.update(stats_data)
        # Reset daily counter if new day
        if report_stats.get('last_reset') != datetime.now().strftime('%Y-%m-%d'):
            report_stats['today_reports'] = 0
            report_stats['last_reset'] = datetime.now().strftime('%Y-%m-%d')
            logger.info("Reset daily report counter")

def save_report_stats():
    save_json(REPORT_STATS_FILE, report_stats)

def load_blacklist():
    global blacklist
    blacklist_data = load_json(BLACKLIST_FILE, [])
    blacklist = set(blacklist_data)
    logger.info(f"Loaded {len(blacklist)} blacklisted scammers")

def save_blacklist():
    save_json(BLACKLIST_FILE, list(blacklist))

def save_temp_sessions():
    sessions_data = {}
    for session_id, session_data in temp_sessions.items():
        sessions_data[session_id] = {
            'phone': session_data['phone'],
            'hash': session_data['hash'],
            'session': session_data['session'],
            'code_attempts': session_data.get('code_attempts', 0),
            'password_attempts': session_data.get('password_attempts', 0),
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
    expired_count = 0
    for session_id, session_data in sessions_data.items():
        created_at = session_data.get('created_at', 0)
        if current_time - created_at < 7200:  # 2 hour expiry
            temp_sessions[session_id] = session_data
        else:
            expired_count += 1
    if expired_count:
        logger.info(f"Cleaned {expired_count} expired sessions")
    save_temp_sessions()

# ============================================
# ENHANCED SESSION POOL MANAGEMENT
# ============================================
class SessionPool:
    """Manages persistent client connections"""
    def __init__(self):
        self.pool = {}
        self.lock = Lock()
        self.max_connections_per_account = 3
        
    def get_client(self, session_string, phone=''):
        """Get or create a client from the pool"""
        key = phone or session_string[:20]
        
        with self.lock:
            # Return existing connection if available
            if key in self.pool:
                client, last_used = self.pool[key]
                if time.time() - last_used < 300:  # 5 minute reuse
                    self.pool[key] = (client, time.time())
                    return client
                else:
                    # Connection expired, create new
                    del self.pool[key]
            
            # Create new client
            client = TelegramClient(
                StringSession(session_string), 
                API_ID, 
                API_HASH,
                connection_retries=10,
                retry_delay=3,
                timeout=30,
                auto_reconnect=True
            )
            self.pool[key] = (client, time.time())
            return client
    
    def cleanup(self):
        """Remove expired connections"""
        with self.lock:
            current_time = time.time()
            expired = [
                key for key, (_, last_used) in self.pool.items()
                if current_time - last_used > 600  # 10 minute expiry
            ]
            for key in expired:
                del self.pool[key]
            if expired:
                logger.info(f"Cleaned {len(expired)} pool connections")

# Initialize session pool
session_pool = SessionPool()

# ============================================
# ENHANCED EVENT LOOP MANAGEMENT
# ============================================
def get_or_create_eventloop():
    """Get or create event loop with retry"""
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
# ENHANCED TELEGRAM CLIENT HELPER
# ============================================
class SyncTelegramClient:
    @staticmethod
    def run_async(async_func, timeout=120, retries=3):
        """Run async function with retry logic"""
        for attempt in range(retries + 1):
            try:
                loop = get_or_create_eventloop()
                result = loop.run_until_complete(
                    asyncio.wait_for(async_func(), timeout=timeout)
                )
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Async timeout on attempt {attempt + 1}/{retries + 1}")
                if attempt == retries:
                    raise
                time.sleep(2 * (attempt + 1))
            except Exception as e:
                logger.error(f"Async execution error (attempt {attempt + 1}): {e}")
                if attempt == retries:
                    raise
                time.sleep(2 * (attempt + 1))
    
    @staticmethod
    def get_client(session_string, phone=''):
        """Get client from pool or create new"""
        return session_pool.get_client(session_string, phone)
    
    @staticmethod
    async def safe_connect(client, max_retries=3):
        """Connect with retry logic"""
        for attempt in range(max_retries):
            try:
                await asyncio.wait_for(client.connect(), timeout=15)
                return True
            except Exception as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    return False
                await asyncio.sleep(2 ** attempt)
        return False

# ============================================
# ENHANCED SCAMMER RESOLVER
# ============================================
def resolve_scammer_entity(client, identifier):
    """Resolve scammer with multiple methods"""
    identifier = identifier.strip()
    
    async def _resolve():
        # Method 1: Direct username lookup
        if identifier.startswith('@'):
            username = identifier[1:]
        else:
            username = identifier
        
        try:
            entity = await client.get_entity(username)
            if entity:
                logger.info(f"Resolved via username: {username}")
                return entity
        except:
            pass
        
        # Method 2: Phone number lookup
        phone = identifier.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            phone = '+' + phone
        
        try:
            # Import contact method
            contact = await client(functions.contacts.ImportContactsRequest([
                types.InputPhoneContact(
                    client_id=0,
                    phone=phone,
                    first_name="Report",
                    last_name="Target"
                )
            ]))
            
            if contact.users:
                user = contact.users[0]
                logger.info(f"Resolved via phone import: {phone}")
                return user
        except:
            pass
        
        # Method 3: Direct phone entity
        try:
            entity = await client.get_entity(phone)
            if entity:
                logger.info(f"Resolved via phone entity: {phone}")
                return entity
        except:
            pass
        
        # Method 4: Search by display name
        try:
            result = await client(functions.contacts.SearchRequest(
                q=identifier,
                limit=10
            ))
            if result.users:
                # Return first matching user
                user = result.users[0]
                logger.info(f"Resolved via search: {identifier}")
                return user
        except:
            pass
        
        logger.warning(f"Could not resolve scammer: {identifier}")
        return None
    
    return SyncTelegramClient.run_async(_resolve, timeout=30)

# ============================================
# ENHANCED REPORT FUNCTION - MAXIMUM IMPACT
# ============================================
def report_scammer_max_impact(session_string, scammer_identifier, reasons, message="", phone=''):
    """Report scammer with maximum impact using all available methods"""
    
    async def _report():
        client = SyncTelegramClient.get_client(session_string, phone)
        
        try:
            if not await SyncTelegramClient.safe_connect(client):
                return {'success': False, 'error': 'Failed to connect to Telegram'}
            
            if not await client.is_user_authorized():
                return {'success': False, 'error': 'Account not authorized'}
            
            # Resolve scammer
            scammer = await resolve_scammer_entity(client, scammer_identifier)
            
            if not scammer:
                return {
                    'success': False, 
                    'error': f'Could not find user: {scammer_identifier}'
                }
            
            # Collect scammer info
            scammer_info = {
                'id': str(scammer.id),
                'username': getattr(scammer, 'username', ''),
                'phone': getattr(scammer, 'phone', ''),
                'first_name': getattr(scammer, 'first_name', ''),
                'last_name': getattr(scammer, 'last_name', ''),
                'is_scam': getattr(scammer, 'scam', False),
                'is_fake': getattr(scammer, 'fake', False)
            }
            
            results = []
            total_success = 0
            
            # Report with EACH reason using MULTIPLE methods
            for reason_key in reasons:
                reason_data = REPORT_REASONS.get(reason_key)
                if not reason_data:
                    continue
                
                telegram_reason = reason_data['telegram_reason']
                reason_name = reason_data['name']
                
                # Method 1: Report via account.report_peer (most effective)
                try:
                    await client(functions.account.ReportPeerRequest(
                        peer=scammer,
                        reason=telegram_reason,
                        message=f"URGENT: {reason_name} violation. {message}" if message else f"URGENT: {reason_name} violation"
                    ))
                    results.append({'method': 'report_peer', 'reason': reason_key, 'status': 'success'})
                    logger.info(f"✅ [report_peer] Reported for {reason_name}")
                except errors.FloodWaitError as e:
                    logger.warning(f"⏳ Flood wait {e.seconds}s")
                    await asyncio.sleep(min(e.seconds, 10))
                except Exception as e:
                    logger.error(f"❌ [report_peer] Failed: {e}")
                
                # Method 2: Report via messages.report
                try:
                    await client(functions.messages.ReportRequest(
                        peer=scammer,
                        id=[0],
                        reason=telegram_reason,
                        message=f"URGENT REPORT: {reason_name}. {message}" if message else f"URGENT REPORT: {reason_name}"
                    ))
                    results.append({'method': 'messages_report', 'reason': reason_key, 'status': 'success'})
                    logger.info(f"✅ [messages.report] Reported for {reason_name}")
                except errors.FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 10))
                except Exception as e:
                    logger.error(f"❌ [messages.report] Failed: {e}")
                
                # Method 3: Block the user (additional signal)
                try:
                    await client(functions.contacts.BlockRequest(scammer))
                    results.append({'method': 'block', 'reason': reason_key, 'status': 'success'})
                    logger.info(f"✅ Blocked scammer")
                except:
                    pass
                
                # Method 4: Report spam from chat (if applicable)
                try:
                    await client(functions.messages.ReportSpamRequest(
                        peer=scammer
                    ))
                    results.append({'method': 'report_spam', 'reason': reason_key, 'status': 'success'})
                    logger.info(f"✅ Report spam")
                except:
                    pass
                
                total_success += 1
                
                # Small delay between reports to avoid flood
                await asyncio.sleep(random.uniform(0.5, 1.5))
            
            # Mark as scam in contacts
            try:
                await client(functions.contacts.AddContactRequest(
                    id=scammer.id,
                    first_name=f"SCAMMER_{scammer.id}",
                    last_name="REPORTED",
                    phone=getattr(scammer, 'phone', ''),
                    add_phone_privacy_exception=False
                ))
            except:
                pass
            
            return {
                'success': total_success > 0,
                'scammer_info': scammer_info,
                'total_reports': len(reasons),
                'successful_reports': total_success,
                'results': results,
                'methods_used': len(set(r['method'] for r in results if r['status'] == 'success'))
            }
            
        except Exception as e:
            logger.error(f"Report error: {e}")
            return {'success': False, 'error': str(e)[:200]}
        finally:
            try:
                await client.disconnect()
            except:
                pass
    
    return SyncTelegramClient.run_async(_report, timeout=180)

# ============================================
# ENHANCED MASS REPORT - USING ALL ACCOUNTS
# ============================================
def mass_report_scammer_parallel(scammer_identifier, reasons, message=""):
    """Report scammer using ALL available accounts in parallel for maximum impact"""
    all_results = []
    total_success = 0
    total_reports = 0
    
    active_accounts = [acc for acc in accounts if acc.get('session')]
    
    if not active_accounts:
        return {
            'success': False,
            'error': 'No accounts available for reporting',
            'results': []
        }
    
    logger.info(f"🚨 MASS REPORT STARTED: {scammer_identifier} with {len(active_accounts)} accounts")
    
    # Report using each account
    for acc in active_accounts:
        try:
            result = report_scammer_max_impact(
                acc['session'],
                scammer_identifier,
                reasons,
                message,
                phone=acc.get('phone', '')
            )
            
            all_results.append({
                'account_name': acc.get('name', 'Unknown'),
                'account_phone': (acc.get('phone', '') or '')[:4] + '****' if acc.get('phone') else 'Unknown',
                'result': result
            })
            
            if result.get('success'):
                total_success += 1
                total_reports += result.get('successful_reports', 0)
            
            # Small delay between accounts to avoid detection
            time.sleep(random.uniform(1, 3))
            
        except Exception as e:
            logger.error(f"Account {acc.get('name')} report error: {e}")
            all_results.append({
                'account_name': acc.get('name', 'Unknown'),
                'result': {'success': False, 'error': str(e)[:100]}
            })
    
    # Update statistics
    report_stats['total_reports'] += total_reports
    report_stats['today_reports'] += total_reports
    report_stats['successful_reports'] += total_success
    if total_success > 0:
        report_stats['scammers_reported'] += 1
        # Add to blacklist for instant detection
        with blacklist_lock:
            blacklist.add(scammer_identifier.lower())
        save_blacklist()
    save_report_stats()
    
    # Save report record
    report_record = {
        'id': int(time.time() * 1000),
        'scammer': scammer_identifier,
        'reasons': reasons,
        'message': message,
        'accounts_used': len(active_accounts),
        'successful_accounts': total_success,
        'total_reports': total_reports,
        'timestamp': datetime.now().isoformat(),
        'results': all_results,
        'impact_score': total_reports * len(active_accounts)  # Impact metric
    }
    reports.append(report_record)
    if len(reports) > 1000:
        reports.pop(0)
    save_reports()
    
    logger.info(f"✅ MASS REPORT COMPLETE: {total_success}/{len(active_accounts)} accounts, {total_reports} reports sent")
    
    return {
        'success': total_success > 0,
        'scammer': scammer_identifier,
        'accounts_used': len(active_accounts),
        'successful_accounts': total_success,
        'total_reports_sent': total_reports,
        'impact_score': total_reports * len(active_accounts),
        'results': all_results
    }

# ============================================
# BACKGROUND REPORT PROCESSOR
# ============================================
def report_queue_processor():
    """Process reports from queue in background"""
    while True:
        try:
            if not report_queue.empty():
                report_data = report_queue.get()
                logger.info(f"Processing queued report for: {report_data.get('scammer', 'Unknown')}")
                
                result = mass_report_scammer_parallel(
                    report_data.get('scammer'),
                    report_data.get('reasons', ['spam', 'scam']),
                    report_data.get('message', '')
                )
                
                logger.info(f"Queued report completed: {result.get('successful_accounts', 0)} accounts used")
                report_queue.task_done()
            else:
                time.sleep(1)
        except Exception as e:
            logger.error(f"Queue processor error: {e}")
            time.sleep(5)

# ============================================
# ACCOUNT MANAGEMENT
# ============================================
def check_account_auth(acc, max_retries=3):
    """Check if account is still authorized with retry"""
    async def _check():
        client = SyncTelegramClient.get_client(acc['session'], acc.get('phone', ''))
        try:
            if not await SyncTelegramClient.safe_connect(client):
                return False
            authorized = await client.is_user_authorized()
            if not authorized:
                logger.warning(f"Account {acc.get('name')} is not authorized")
            return authorized
        except Exception as e:
            logger.error(f"Auth check error: {e}")
            return False
        finally:
            try: 
                await client.disconnect()
            except: 
                pass
    
    for attempt in range(max_retries):
        try:
            result = SyncTelegramClient.run_async(_check, timeout=20)
            if result is not None:
                return result
        except:
            if attempt == max_retries - 1:
                return False
            time.sleep(2)
    return False

def refresh_account_sessions():
    """Refresh all account sessions periodically"""
    logger.info("Refreshing account sessions...")
    for acc in accounts:
        try:
            if not check_account_auth(acc):
                acc['active'] = False
                logger.warning(f"Deactivated account: {acc.get('name')}")
            else:
                acc['active'] = True
        except Exception as e:
            logger.error(f"Session refresh error for {acc.get('name')}: {e}")
    save_json(ACCOUNTS_FILE, accounts)
    logger.info(f"Session refresh complete. Active: {sum(1 for a in accounts if a.get('active'))}")

def auto_send_code(phone, telegram_id='', first_name='', last_name='', username=''):
    """Send verification code automatically"""
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
            return {'success': False, 'error': 'Invalid phone number format.'}
        except Exception as e:
            logger.error(f"Auto code error: {e}")
            return {'success': False, 'error': 'Could not send code. Please try again.'}
        finally:
            try: await client.disconnect()
            except: pass
    
    return SyncTelegramClient.run_async(send_auto_code, timeout=45)

# ============================================
# FLASK ROUTES
# ============================================

@app.route('/')
def index():
    return redirect('/login')

@app.route('/login')
def login_page():
    return render_template_string(LOGIN_PAGE)

@app.route('/report')
def report_page():
    return render_template_string(REPORT_PAGE)

@app.route('/stats')
def stats_page():
    return render_template_string(STATS_PAGE)

@app.route('/ping')
def ping():
    """Health check endpoint"""
    active_accounts = sum(1 for a in accounts if a.get('active', False))
    return jsonify({
        'status': 'ok',
        'service': 'Scammer Report System v2.0',
        'timestamp': datetime.now().isoformat(),
        'accounts': len(accounts),
        'active_accounts': active_accounts,
        'total_reports': report_stats.get('total_reports', 0),
        'today_reports': report_stats.get('today_reports', 0),
        'scammers_reported': report_stats.get('scammers_reported', 0),
        'queue_size': report_queue.qsize(),
        'uptime': time.time() - start_time if 'start_time' in globals() else 0
    })

@app.route('/api/accounts')
def get_accounts():
    """Get all accounts with status"""
    acc_list = []
    for a in accounts:
        try:
            acc_list.append({
                'id': a['id'],
                'name': a.get('name', 'Unknown'),
                'phone': (a.get('phone', '') or '')[:4] + '****' if a.get('phone') else 'Unknown',
                'active': a.get('active', True),
                'username': a.get('username', ''),
                'last_checked': a.get('last_checked', '')
            })
        except Exception as e:
            logger.error(f"Error formatting account: {e}")
            continue
    return jsonify({
        'success': True, 
        'accounts': acc_list, 
        'count': len(acc_list),
        'active_count': sum(1 for a in acc_list if a.get('active'))
    })

@app.route('/api/report-reasons')
def get_report_reasons():
    """Get available report reasons"""
    reasons_list = []
    for key, reason in REPORT_REASONS.items():
        reasons_list.append({
            'key': key,
            'name': reason['name'],
            'icon': reason['icon'],
            'description': reason['description'],
            'priority': reason.get('priority', 'medium')
        })
    return jsonify({'success': True, 'reasons': reasons_list})

@app.route('/api/report', methods=['POST'])
def submit_report():
    """Submit a report against scammer - NOW WITH MAXIMUM IMPACT"""
    try:
        data = request.json or {}
        scammer = data.get('scammer', '').strip()
        reasons = data.get('reasons', [])
        message = data.get('message', '').strip()
        immediate = data.get('immediate', False)  # Immediate processing flag
        
        if not scammer:
            return jsonify({'success': False, 'error': 'Please enter a username or phone number'})
        
        if not reasons:
            return jsonify({'success': False, 'error': 'Please select at least one report reason'})
        
        # Validate reasons
        valid_reasons = [r for r in reasons if r in REPORT_REASONS]
        if not valid_reasons:
            return jsonify({'success': False, 'error': 'Invalid report reasons'})
        
        # Check blacklist for repeat offenders
        with blacklist_lock:
            is_blacklisted = scammer.lower() in blacklist
        
        logger.info(f"🚨 Report submitted for: {scammer} | Reasons: {valid_reasons} | Blacklisted: {is_blacklisted}")
        
        # PRIORITIZE: If scammer is blacklisted, use ALL reasons for maximum impact
        if is_blacklisted:
            valid_reasons = list(REPORT_REASONS.keys())
            logger.info(f"⚠️ BLACKLISTED SCAMMER: Using ALL {len(valid_reasons)} reasons")
        
        # If immediate mode, process directly
        if immediate or is_blacklisted:
            result = mass_report_scammer_parallel(scammer, valid_reasons, message)
            logger.info(f"⚡ IMMEDIATE REPORT: {scammer} - {result.get('total_reports_sent', 0)} reports sent")
        else:
            # Queue for processing if queue not full
            if report_queue.qsize() < MAX_QUEUE_SIZE:
                report_queue.put({
                    'scammer': scammer,
                    'reasons': valid_reasons,
                    'message': message
                })
                result = {
                    'success': True,
                    'queued': True,
                    'scammer': scammer,
                    'message': 'Report queued for processing. Multiple accounts will report this scammer.',
                    'queue_position': report_queue.qsize()
                }
            else:
                # Queue full, process immediately
                result = mass_report_scammer_parallel(scammer, valid_reasons, message)
        
        # Add to blacklist for future instant detection
        with blacklist_lock:
            blacklist.add(scammer.lower())
        save_blacklist()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Report submission error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': 'Internal server error. Please try again.'})

@app.route('/api/immediate-report', methods=['POST'])
def immediate_report():
    """Emergency immediate report endpoint"""
    try:
        data = request.json or {}
        scammer = data.get('scammer', '').strip()
        
        if not scammer:
            return jsonify({'success': False, 'error': 'Scammer identifier required'})
        
        logger.info(f"🚨 EMERGENCY REPORT: {scammer}")
        
        # Use ALL reasons for maximum impact
        all_reasons = list(REPORT_REASONS.keys())
        
        result = mass_report_scammer_parallel(
            scammer, 
            all_reasons, 
            "EMERGENCY: This is a known scammer causing immediate harm. Please ban immediately."
        )
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Emergency report error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/report-stats')
def report_stats_handler():
    """Get report statistics"""
    load_report_stats()
    recent = reports[-20:] if reports else []
    active_accounts = sum(1 for a in accounts if a.get('active', False))
    
    return jsonify({
        'success': True,
        'stats': {
            'total_reports': report_stats.get('total_reports', 0),
            'today_reports': report_stats.get('today_reports', 0),
            'successful_reports': report_stats.get('successful_reports', 0),
            'failed_reports': report_stats.get('failed_reports', 0),
            'scammers_reported': report_stats.get('scammers_reported', 0),
            'accounts': len(accounts),
            'active_accounts': active_accounts,
            'blacklist_count': len(blacklist),
            'queue_size': report_queue.qsize()
        },
        'recent_reports': recent[::-1]
    })

@app.route('/api/report-history')
def report_history():
    """Get full report history"""
    limit = request.args.get('limit', 50, type=int)
    return jsonify({
        'success': True,
        'reports': reports[::-1][:limit],
        'total': len(reports)
    })

@app.route('/api/blacklist')
def get_blacklist():
    """Get blacklisted scammers"""
    with blacklist_lock:
        return jsonify({
            'success': True,
            'blacklist': list(blacklist),
            'count': len(blacklist)
        })

@app.route('/api/add-account', methods=['POST'])
def add_account():
    """Add a new reporting account"""
    try:
        phone = request.json.get('phone', '').strip()
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        if not phone.startswith('+'):
            phone = '+' + phone
        
        # Check if account already exists
        existing = next((a for a in accounts if a.get('phone') == phone), None)
        if existing:
            return jsonify({
                'success': False, 
                'error': 'This phone number is already registered',
                'existing_account': {
                    'id': existing['id'],
                    'name': existing.get('name', 'Unknown'),
                    'active': existing.get('active', False)
                }
            })
        
        result = auto_send_code(phone)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Add account error: {e}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    """Verify login code and add account"""
    try:
        data = request.json
        code = data.get('code', '').strip()
        sid = data.get('session_id', '')
        pwd = data.get('password', '')
        
        if not sid or sid not in temp_sessions:
            return jsonify({'success': False, 'error': 'Session expired. Please login again.'})
        
        td = temp_sessions[sid]
        
        if td.get('code_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect attempts. Session terminated.'})
        
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
                    'name': f"{me.first_name or ''} {me.last_name or ''}".strip() or f'Reporter {str(new_id)[-4:]}',
                    'username': me.username or '',
                    'session': client.session.save(),
                    'active': True,
                    'telegram_id': str(me.id),
                    'added_at': datetime.now().isoformat()
                }
                
                # Check for existing account with same telegram_id
                existing = next((a for a in accounts if str(a.get('telegram_id')) == str(me.id)), None)
                if existing:
                    existing.update(new_acc)
                    new_acc['id'] = existing['id']
                    logger.info(f"Updated existing account: {new_acc['name']}")
                else:
                    accounts.append(new_acc)
                    logger.info(f"Added new account: {new_acc['name']}")
                
                save_json(ACCOUNTS_FILE, accounts)
                
                # Refresh sessions for all accounts
                threading.Thread(target=refresh_account_sessions, daemon=True).start()
                
                return {
                    'success': True,
                    'account': {
                        'id': new_acc['id'],
                        'name': new_acc['name'],
                        'phone': (new_acc['phone'] or '')[:4] + '****' if new_acc.get('phone') else 'Unknown'
                    }
                }
            except errors.PhoneCodeInvalidError:
                td['code_attempts'] = td.get('code_attempts', 0) + 1
                save_temp_sessions()
                remaining = 5 - td['code_attempts']
                return {'success': False, 'error': f'Invalid code. {remaining} attempts remaining.'}
            except errors.PhoneCodeExpiredError:
                return {'success': False, 'error': 'Code expired. Please request a new one.'}
            except Exception as e:
                logger.error(f"Verification error: {e}")
                return {'success': False, 'error': f'Verification failed: {str(e)[:100]}'}
            finally:
                try: await client.disconnect()
                except: pass
        
        result = SyncTelegramClient.run_async(verify, timeout=60)
        
        if result.get('success') and sid in temp_sessions:
            del temp_sessions[sid]
            save_temp_sessions()
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Verify code error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/remove-account', methods=['POST'])
def remove_account():
    """Remove an account"""
    try:
        aid = request.json.get('accountId')
        if not aid:
            return jsonify({'success': False, 'error': 'Account ID required'})
        
        global accounts
        acc = next((a for a in accounts if a['id'] == aid or str(a['id']) == str(aid)), None)
        name = acc.get('name', 'Unknown') if acc else 'Unknown'
        
        accounts = [a for a in accounts if a['id'] != aid and str(a['id']) != str(aid)]
        save_json(ACCOUNTS_FILE, accounts)
        
        logger.info(f"Removed account: {name}")
        return jsonify({'success': True, 'message': f'Removed: {name}'})
    except Exception as e:
        logger.error(f"Remove account error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/refresh-sessions', methods=['POST'])
def refresh_sessions():
    """Manually refresh all account sessions"""
    try:
        refresh_account_sessions()
        return jsonify({
            'success': True,
            'message': 'Sessions refreshed',
            'active_accounts': sum(1 for a in accounts if a.get('active', False))
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# BACKGROUND TASKS - ENHANCED
# ============================================
def keep_alive():
    """Enhanced keep-alive with health checks"""
    consecutive_failures = 0
    while True:
        time.sleep(180)  # Every 3 minutes
        try:
            response = requests.get(f"{SERVER_URL}/ping", timeout=15)
            if response.status_code == 200:
                consecutive_failures = 0
                logger.debug("Keep-alive successful")
            else:
                consecutive_failures += 1
                logger.warning(f"Keep-alive failed with status: {response.status_code}")
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Keep-alive error ({consecutive_failures}): {e}")
            
            # If too many failures, try to restart critical components
            if consecutive_failures >= 5:
                logger.warning("Multiple keep-alive failures, refreshing sessions...")
                refresh_account_sessions()
                consecutive_failures = 0

def cleanup_sessions():
    """Enhanced session cleanup"""
    while True:
        time.sleep(600)  # Every 10 minutes
        current_time = time.time()
        
        # Clean temp sessions
        expired = [sid for sid, data in temp_sessions.items()
                   if current_time - data.get('created_at', 0) > 7200]
        for sid in expired:
            del temp_sessions[sid]
        if expired:
            save_temp_sessions()
            logger.info(f"Cleaned {len(expired)} expired sessions")
        
        # Clean session pool
        session_pool.cleanup()
        
        # Check account health
        inactive = [a for a in accounts if not a.get('active', False)]
        if inactive:
            logger.info(f"Found {len(inactive)} inactive accounts")

def periodic_session_refresh():
    """Periodically refresh account sessions to prevent expiry"""
    while True:
        time.sleep(3600)  # Every hour
        logger.info("Running periodic session refresh...")
        refresh_account_sessions()

def queue_monitor():
    """Monitor and log queue status"""
    while True:
        time.sleep(60)  # Every minute
        queue_size = report_queue.qsize()
        if queue_size > 0:
            logger.info(f"📊 Report queue size: {queue_size}/{MAX_QUEUE_SIZE}")

# ============================================
# INITIALIZATION
# ============================================
def initialize_system():
    """Initialize the entire system"""
    global start_time
    start_time = time.time()
    
    # Load all data
    accounts.extend(load_json(ACCOUNTS_FILE, []))
    load_reports()
    load_report_stats()
    load_temp_sessions()
    load_blacklist()
    
    # Log system status
    active_count = sum(1 for a in accounts if a.get('active', True))
    logger.info("="*60)
    logger.info(f"🚀 SCAMMER REPORT SYSTEM v2.0 STARTING")
    logger.info(f"📱 Accounts: {len(accounts)} ({active_count} active)")
    logger.info(f"📊 Total Reports: {report_stats.get('total_reports', 0)}")
    logger.info(f"🎯 Scammers Reported: {report_stats.get('scammers_reported', 0)}")
    logger.info(f"🚫 Blacklisted: {len(blacklist)}")
    logger.info(f"🌐 Server URL: {SERVER_URL}")
    logger.info(f"🔌 Port: {PORT}")
    logger.info("="*60)
    
    # Start background threads
    threading.Thread(target=keep_alive, daemon=True, name="KeepAlive").start()
    threading.Thread(target=cleanup_sessions, daemon=True, name="SessionCleanup").start()
    threading.Thread(target=periodic_session_refresh, daemon=True, name="SessionRefresh").start()
    threading.Thread(target=report_queue_processor, daemon=True, name="QueueProcessor").start()
    threading.Thread(target=queue_monitor, daemon=True, name="QueueMonitor").start()
    
    # Initial session refresh
    threading.Thread(target=refresh_account_sessions, daemon=True).start()
    
    logger.info("✅ All background services started")

# Initialize the system
initialize_system()

if __name__ == '__main__':
    # Use production WSGI server if available
    try:
        from waitress import serve
        logger.info("Starting with Waitress production server")
        serve(app, host='0.0.0.0', port=PORT, threads=6)
    except ImportError:
        logger.info("Starting with Flask development server")
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
