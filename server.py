#!/usr/bin/env python3
"""
Telegram Scammer Report System - ENHANCED VERSION WITH FIXES
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
import functools

# ============================================
# TIMEOUT HANDLING
# ============================================
class TimeoutError(Exception):
    """Custom timeout exception"""
    pass

class TimeoutManager:
    """Context manager for timeout operations"""
    def __init__(self, seconds, error_message=None):
        self.seconds = seconds
        self.error_message = error_message or f"Operation timed out after {seconds} seconds"
    
    def __enter__(self):
        if hasattr(signal, 'SIGALRM'):
            try:
                signal.signal(signal.SIGALRM, self._handle_timeout)
                signal.alarm(self.seconds)
            except:
                pass  # Not supported on all platforms
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(signal, 'SIGALRM'):
            try:
                signal.alarm(0)
            except:
                pass
        return False
    
    def _handle_timeout(self, signum, frame):
        raise TimeoutError(self.error_message)

class CircuitBreaker:
    """Prevents system overload on repeated failures"""
    def __init__(self, name="default", failure_threshold=5, reset_timeout=300):
        self.name = name
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.last_failure_time = 0
        self.is_open = False
        self.lock = Lock()
    
    def record_failure(self):
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.is_open = True
                logger.warning(f"🔴 Circuit breaker '{self.name}' OPEN - {self.failure_count} failures")
    
    def record_success(self):
        with self.lock:
            self.failure_count = 0
            self.is_open = False
    
    def can_execute(self):
        with self.lock:
            if self.is_open:
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.is_open = False
                    self.failure_count = 0
                    logger.info(f"🟢 Circuit breaker '{self.name}' RESET")
                    return True
                return False
            return True

# ============================================
# LOGGING CONFIGURATION
# ============================================
os.makedirs('logs', exist_ok=True)

file_handler = logging.handlers.RotatingFileHandler(
    'logs/server.log',
    maxBytes=20*1024*1024,
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
# FILE PATHS
# ============================================
ACCOUNTS_FILE = 'accounts.json'
REPORTS_FILE = 'reports.json'
REPORT_STATS_FILE = 'report_stats.json'
TEMP_SESSIONS_FILE = 'temp_sessions.json'
SESSION_POOL_FILE = 'session_pool.json'
BLACKLIST_FILE = 'blacklist.json'

# ============================================
# STORAGE
# ============================================
accounts = []
temp_sessions = {}
reports = []
session_pool = {}
blacklist = set()
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

report_queue = Queue()
MAX_QUEUE_SIZE = 1000
QUEUE_PROCESSING_TIMEOUT = 120

report_circuit_breaker = CircuitBreaker("report", failure_threshold=5, reset_timeout=300)
telegram_circuit_breaker = CircuitBreaker("telegram", failure_threshold=3, reset_timeout=180)

# ============================================
# HTML TEMPLATES
# ============================================

LOGIN_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scammer Report System - Login</title>
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
        h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }
        .account-card {
            background: #f8f9fa;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 2px solid #e9ecef;
        }
        .account-card h3 {
            color: #495057;
            margin-bottom: 15px;
        }
        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 10px;
            font-size: 16px;
            cursor: pointer;
            transition: transform 0.2s;
            width: 100%;
            margin-top: 10px;
        }
        .btn:hover { transform: translateY(-2px); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-danger {
            background: linear-gradient(135deg, #f56565 0%, #ed8936 100%);
        }
        .btn-success {
            background: linear-gradient(135deg, #48bb78 0%, #38a169 100%);
        }
        input {
            width: 100%;
            padding: 12px;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            font-size: 16px;
            margin-bottom: 15px;
            transition: border-color 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        .status {
            padding: 10px;
            border-radius: 8px;
            margin: 10px 0;
            display: none;
        }
        .status.success { background: #c6f6d5; color: #22543d; display: block; }
        .status.error { background: #fed7d7; color: #742a2a; display: block; }
        .status.info { background: #bee3f8; color: #2a4365; display: block; }
        .account-list {
            margin-top: 20px;
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
        .account-info {
            flex-grow: 1;
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
        .nav-bar {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .nav-link {
            padding: 10px 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-decoration: none;
            color: #495057;
            transition: all 0.3s;
        }
        .nav-link:hover { background: #e9ecef; }
        .nav-link.active { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
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
            <button class="btn" onclick="addAccount()" id="addBtn">
                Send Verification Code
            </button>
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
            <button class="btn btn-success" onclick="refreshSessions()" style="margin-top:15px;">
                🔄 Refresh All Sessions
            </button>
        </div>
    </div>
    
    <script>
        let currentSessionId = '';
        let needPassword = false;
        
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
                })
                .catch(err => {
                    console.error('Load accounts error:', err);
                });
        }
        
        function addAccount() {
            const phone = document.getElementById('phoneInput').value.trim();
            if (!phone) {
                showStatus('Please enter a phone number', 'error');
                return;
            }
            
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
                    document.getElementById('codeInput').focus();
                } else {
                    showStatus(data.error || 'Failed to send code', 'error');
                }
            })
            .catch(err => {
                btn.disabled = false;
                btn.innerHTML = 'Send Verification Code';
                showStatus('Network error. Please try again.', 'error');
            });
        }
        
        function verifyCode() {
            const code = document.getElementById('codeInput').value.trim();
            const password = document.getElementById('passwordInput').value.trim();
            
            if (!code) {
                showStatus('Please enter the verification code', 'error');
                return;
            }
            
            const btn = document.getElementById('verifyBtn');
            btn.disabled = true;
            btn.innerHTML = '<span class="loading"></span> Verifying...';
            
            fetch('/api/verify-code', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    code: code,
                    session_id: currentSessionId,
                    password: password
                })
            })
            .then(r => r.json())
            .then(data => {
                btn.disabled = false;
                btn.innerHTML = 'Verify & Add Account';
                
                if (data.need_password) {
                    needPassword = true;
                    document.getElementById('passwordInput').style.display = 'block';
                    showStatus('2FA enabled. Please enter your password.', 'info');
                } else if (data.success) {
                    document.getElementById('verificationSection').style.display = 'none';
                    document.getElementById('phoneInput').value = '';
                    document.getElementById('codeInput').value = '';
                    document.getElementById('passwordInput').value = '';
                    document.getElementById('passwordInput').style.display = 'none';
                    showStatus('Account added successfully!', 'success');
                    loadAccounts();
                } else {
                    showStatus(data.error || 'Verification failed', 'error');
                }
            })
            .catch(err => {
                btn.disabled = false;
                btn.innerHTML = 'Verify & Add Account';
                showStatus('Network error. Please try again.', 'error');
            });
        }
        
        function removeAccount(id) {
            if (!confirm('Are you sure you want to remove this account?')) return;
            
            fetch('/api/remove-account', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({accountId: id})
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    showStatus(data.message, 'success');
                    loadAccounts();
                } else {
                    showStatus(data.error, 'error');
                }
            })
            .catch(err => {
                showStatus('Network error', 'error');
            });
        }
        
        function refreshSessions() {
            fetch('/api/refresh-sessions', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    showStatus(data.message, 'success');
                    loadAccounts();
                }
            })
            .catch(err => {
                showStatus('Refresh failed', 'error');
            });
        }
        
        // Load accounts on page load
        loadAccounts();
        
        // Auto-refresh every 30 seconds
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
        h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        input, textarea {
            width: 100%;
            padding: 12px;
            border: 2px solid #e9ecef;
            border-radius: 10px;
            font-size: 16px;
            margin-bottom: 15px;
            transition: border-color 0.3s;
        }
        input:focus, textarea:focus {
            outline: none;
            border-color: #f5576c;
        }
        textarea { resize: vertical; min-height: 100px; }
        .reasons-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
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
        .reason-btn:hover { border-color: #f5576c; transform: translateY(-2px); }
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
            transition: transform 0.2s;
            width: 100%;
            margin-top: 10px;
            font-weight: bold;
        }
        .btn:hover { transform: translateY(-2px); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-emergency {
            background: linear-gradient(135deg, #f56565 0%, #c53030 100%);
            margin-top: 5px;
        }
        .status {
            padding: 15px;
            border-radius: 10px;
            margin: 15px 0;
            display: none;
            font-weight: 500;
        }
        .status.success { background: #c6f6d5; color: #22543d; display: block; }
        .status.error { background: #fed7d7; color: #742a2a; display: block; }
        .status.info { background: #bee3f8; color: #2a4365; display: block; }
        .result-details {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 15px;
            margin-top: 15px;
            display: none;
        }
        .nav-bar {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .nav-link {
            padding: 10px 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-decoration: none;
            color: #495057;
            transition: all 0.3s;
        }
        .nav-link:hover { background: #e9ecef; }
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
        
        <textarea id="messageInput" placeholder="Additional details about the scammer (optional)"></textarea>
        
        <button class="btn" onclick="submitReport()" id="reportBtn">
            🚨 Submit Report
        </button>
        <button class="btn btn-emergency" onclick="emergencyReport()" id="emergencyBtn">
            ⚡ EMERGENCY REPORT (All Reasons)
        </button>
        
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
                        html += `
                            <div class="reason-btn" onclick="toggleReason('${reason.key}', this)" data-reason="${reason.key}">
                                ${reason.icon} ${reason.name}
                            </div>
                        `;
                    });
                    document.getElementById('reasonsGrid').innerHTML = html;
                })
                .catch(err => {
                    console.error('Load reasons error:', err);
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
            const status = document.getElementById('status');
            status.className = 'status ' + type;
            status.textContent = message;
        }
        
        function showProgress(show) {
            document.getElementById('progressBar').style.display = show ? 'block' : 'none';
            if (!show) {
                document.getElementById('progressFill').style.width = '0%';
            }
        }
        
        function updateProgress(percent) {
            document.getElementById('progressFill').style.width = percent + '%';
        }
        
        async function submitReport() {
            const scammer = document.getElementById('scammerInput').value.trim();
            const message = document.getElementById('messageInput').value.trim();
            
            if (!scammer) {
                showStatus('Please enter a username or phone number', 'error');
                return;
            }
            
            if (selectedReasons.size === 0) {
                showStatus('Please select at least one report reason', 'error');
                return;
            }
            
            const reportBtn = document.getElementById('reportBtn');
            reportBtn.disabled = true;
            reportBtn.innerHTML = '<span class="loading"></span> Reporting...';
            showProgress(true);
            updateProgress(30);
            
            try {
                const response = await fetch('/api/report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        scammer: scammer,
                        reasons: Array.from(selectedReasons),
                        message: message,
                        immediate: true
                    })
                });
                
                updateProgress(80);
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`✅ Report submitted successfully! ${data.total_reports_sent || 0} reports sent.`, 'success');
                    
                    if (data.results) {
                        let detailsHtml = '<h4>Report Details:</h4>';
                        data.results.forEach(r => {
                            const status = r.result.success ? '✅' : '❌';
                            detailsHtml += `<p>${status} ${r.account_name}: ${r.result.error || 'Reported successfully'}</p>`;
                        });
                        document.getElementById('resultDetails').innerHTML = detailsHtml;
                        document.getElementById('resultDetails').style.display = 'block';
                    }
                    
                    // Clear form
                    document.getElementById('scammerInput').value = '';
                    document.getElementById('messageInput').value = '';
                    selectedReasons.clear();
                    document.querySelectorAll('.reason-btn').forEach(btn => btn.classList.remove('selected'));
                } else {
                    showStatus(`❌ ${data.error || 'Report failed'}`, 'error');
                }
            } catch (err) {
                showStatus('Network error. Please try again.', 'error');
            } finally {
                reportBtn.disabled = false;
                reportBtn.innerHTML = '🚨 Submit Report';
                updateProgress(100);
                setTimeout(() => showProgress(false), 1000);
            }
        }
        
        async function emergencyReport() {
            const scammer = document.getElementById('scammerInput').value.trim();
            
            if (!scammer) {
                showStatus('Please enter a username or phone number', 'error');
                return;
            }
            
            if (!confirm('⚠️ EMERGENCY REPORT will use ALL report reasons across ALL accounts. Continue?')) {
                return;
            }
            
            const emergencyBtn = document.getElementById('emergencyBtn');
            emergencyBtn.disabled = true;
            emergencyBtn.innerHTML = '<span class="loading"></span> Sending...';
            showProgress(true);
            updateProgress(50);
            
            try {
                const response = await fetch('/api/immediate-report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({scammer: scammer})
                });
                
                updateProgress(90);
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`🚨 EMERGENCY REPORT SENT! ${data.total_reports_sent || 0} reports across ${data.accounts_used || 0} accounts.`, 'success');
                } else {
                    showStatus(`❌ ${data.error || 'Emergency report failed'}`, 'error');
                }
            } catch (err) {
                showStatus('Network error. Please try again.', 'error');
            } finally {
                emergencyBtn.disabled = false;
                emergencyBtn.innerHTML = '⚡ EMERGENCY REPORT (All Reasons)';
                updateProgress(100);
                setTimeout(() => showProgress(false), 1000);
            }
        }
        
        // Load reasons on page load
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
        h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
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
        .stat-value {
            font-size: 36px;
            font-weight: bold;
            margin: 10px 0;
        }
        .stat-label {
            font-size: 14px;
            opacity: 0.9;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .stat-icon {
            font-size: 30px;
        }
        .report-list {
            margin-top: 20px;
        }
        .report-item {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 10px;
            border-left: 4px solid #667eea;
        }
        .report-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 5px;
        }
        .report-scammer {
            font-weight: bold;
            color: #333;
        }
        .report-time {
            color: #666;
            font-size: 12px;
        }
        .report-details {
            color: #666;
            font-size: 14px;
        }
        .nav-bar {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .nav-link {
            padding: 10px 20px;
            background: #f8f9fa;
            border-radius: 8px;
            text-decoration: none;
            color: #495057;
            transition: all 0.3s;
        }
        .nav-link:hover { background: #e9ecef; }
        .nav-link.active { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); color: white; }
        .btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 10px;
            font-size: 16px;
            cursor: pointer;
            transition: transform 0.2s;
            width: 100%;
            margin-top: 10px;
        }
        .btn:hover { transform: translateY(-2px); }
        .circuit-status {
            display: inline-block;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            margin-left: 10px;
        }
        .circuit-status.open { background: #fed7d7; color: #742a2a; }
        .circuit-status.closed { background: #c6f6d5; color: #22543d; }
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
        <p class="subtitle">Track your reporting impact</p>
        
        <div class="stats-grid" id="statsGrid">
            <div class="stat-card">
                <div class="stat-icon">📨</div>
                <div class="stat-value" id="totalReports">0</div>
                <div class="stat-label">Total Reports</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📅</div>
                <div class="stat-value" id="todayReports">0</div>
                <div class="stat-label">Today's Reports</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">🎯</div>
                <div class="stat-value" id="scammersReported">0</div>
                <div class="stat-label">Scammers Reported</div>
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
                <div class="stat-label">Queue Size</div>
            </div>
        </div>
        
        <div style="margin:20px 0;">
            <strong>Circuit Breakers:</strong>
            <span id="reportCB" class="circuit-status">Checking...</span>
            <span id="telegramCB" class="circuit-status">Checking...</span>
            <button class="btn" onclick="resetCircuitBreakers()" style="width:auto;margin-top:10px;">
                🔄 Reset Circuit Breakers
            </button>
        </div>
        
        <h3 style="margin-top:30px;">Recent Reports</h3>
        <div class="report-list" id="reportList">
            <p style="color:#666;">Loading...</p>
        </div>
        
        <button class="btn" onclick="loadStats()">🔄 Refresh Stats</button>
    </div>
    
    <script>
        function loadStats() {
            fetch('/api/report-stats')
                .then(r => r.json())
                .then(data => {
                    const stats = data.stats;
                    document.getElementById('totalReports').textContent = stats.total_reports;
                    document.getElementById('todayReports').textContent = stats.today_reports;
                    document.getElementById('scammersReported').textContent = stats.scammers_reported;
                    document.getElementById('activeAccounts').textContent = `${stats.active_accounts}/${stats.accounts}`;
                    document.getElementById('blacklistCount').textContent = stats.blacklist_count;
                    document.getElementById('queueSize').textContent = stats.queue_size;
                    
                    // Circuit breakers
                    const reportCB = document.getElementById('reportCB');
                    const telegramCB = document.getElementById('telegramCB');
                    
                    const cbStatus = stats.circuit_breakers || {};
                    reportCB.textContent = `Report: ${cbStatus.report || 'UNKNOWN'}`;
                    reportCB.className = 'circuit-status ' + (cbStatus.report === 'OPEN' ? 'open' : 'closed');
                    telegramCB.textContent = `Telegram: ${cbStatus.telegram || 'UNKNOWN'}`;
                    telegramCB.className = 'circuit-status ' + (cbStatus.telegram === 'OPEN' ? 'open' : 'closed');
                    
                    // Recent reports
                    let html = '';
                    const recent = data.recent_reports || [];
                    if (recent.length === 0) {
                        html = '<p style="color:#666;">No reports yet</p>';
                    } else {
                        recent.forEach(report => {
                            const time = new Date(report.timestamp).toLocaleString();
                            html += `
                                <div class="report-item">
                                    <div class="report-header">
                                        <span class="report-scammer">🎯 ${report.scammer}</span>
                                        <span class="report-time">${time}</span>
                                    </div>
                                    <div class="report-details">
                                        Reasons: ${report.reasons.join(', ')} | 
                                        Impact Score: ${report.impact_score || 0} |
                                        Success: ${report.successful_accounts}/${report.accounts_used}
                                    </div>
                                </div>
                            `;
                        });
                    }
                    document.getElementById('reportList').innerHTML = html;
                })
                .catch(err => {
                    console.error('Load stats error:', err);
                });
        }
        
        function resetCircuitBreakers() {
            fetch('/api/reset-circuit-breaker', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        alert('Circuit breakers reset successfully!');
                        loadStats();
                    }
                })
                .catch(err => {
                    alert('Failed to reset circuit breakers');
                });
        }
        
        // Load stats on page load
        loadStats();
        
        // Auto-refresh every 30 seconds
        setInterval(loadStats, 30000);
    </script>
</body>
</html>
'''

# ============================================
# REPORT REASONS
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
# FILE OPERATIONS
# ============================================
def load_json(path, default):
    """Load JSON with backup recovery"""
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    backup_path = f"{path}.backup"
                    try:
                        with open(backup_path, 'w') as backup:
                            json.dump(data, backup, indent=2, default=str)
                    except:
                        pass
                    return data
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON file {path}: {e}")
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

def load_reports():
    global reports
    reports = load_json(REPORTS_FILE, [])

def save_reports():
    save_json(REPORTS_FILE, reports)

def load_report_stats():
    global report_stats
    stats_data = load_json(REPORT_STATS_FILE, {})
    if stats_data:
        report_stats.update(stats_data)
        if report_stats.get('last_reset') != datetime.now().strftime('%Y-%m-%d'):
            report_stats['today_reports'] = 0
            report_stats['last_reset'] = datetime.now().strftime('%Y-%m-%d')

def save_report_stats():
    save_json(REPORT_STATS_FILE, report_stats)

def load_blacklist():
    global blacklist
    blacklist_data = load_json(BLACKLIST_FILE, [])
    blacklist = set(blacklist_data)

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
    for session_id, session_data in sessions_data.items():
        created_at = session_data.get('created_at', 0)
        if current_time - created_at < 7200:
            temp_sessions[session_id] = session_data

# ============================================
# SESSION POOL MANAGEMENT
# ============================================
class SessionPool:
    def __init__(self):
        self.pool = {}
        self.lock = Lock()
        
    def get_client(self, session_string, phone=''):
        key = phone or session_string[:20]
        with self.lock:
            if key in self.pool:
                client, last_used = self.pool[key]
                if time.time() - last_used < 300:
                    self.pool[key] = (client, time.time())
                    return client
                else:
                    del self.pool[key]
            
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
        with self.lock:
            current_time = time.time()
            expired = [
                key for key, (client, last_used) in self.pool.items()
                if current_time - last_used > 600
            ]
            for key in expired:
                try:
                    client = self.pool[key][0]
                    if client.is_connected():
                        loop = get_or_create_eventloop()
                        loop.run_until_complete(client.disconnect())
                except:
                    pass
                del self.pool[key]

session_pool = SessionPool()

# ============================================
# EVENT LOOP MANAGEMENT
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
# TELEGRAM CLIENT HELPER
# ============================================
class SyncTelegramClient:
    @staticmethod
    def run_async(async_func, timeout=120, retries=3):
        for attempt in range(retries + 1):
            try:
                loop = get_or_create_eventloop()
                
                async def timeout_wrapper():
                    try:
                        return await asyncio.wait_for(async_func(), timeout=timeout)
                    except asyncio.TimeoutError:
                        raise TimeoutError(f"Operation timed out after {timeout} seconds")
                
                result = loop.run_until_complete(timeout_wrapper())
                telegram_circuit_breaker.record_success()
                return result
                
            except TimeoutError as e:
                logger.warning(f"Async timeout on attempt {attempt + 1}/{retries + 1}")
                if attempt == retries:
                    telegram_circuit_breaker.record_failure()
                    raise
                time.sleep(2 * (attempt + 1))
                
            except Exception as e:
                logger.error(f"Async execution error (attempt {attempt + 1}): {e}")
                if attempt == retries:
                    telegram_circuit_breaker.record_failure()
                    raise
                time.sleep(2 * (attempt + 1))
    
    @staticmethod
    def get_client(session_string, phone=''):
        return session_pool.get_client(session_string, phone)
    
    @staticmethod
    async def safe_connect(client, max_retries=3, timeout=15):
        for attempt in range(max_retries):
            try:
                await asyncio.wait_for(client.connect(), timeout=timeout)
                return True
            except asyncio.TimeoutError:
                logger.warning(f"Connection attempt {attempt + 1} timed out")
            except Exception as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        return False
    
    @staticmethod
    async def safe_disconnect(client):
        try:
            if client and client.is_connected():
                await asyncio.wait_for(client.disconnect(), timeout=5)
        except:
            pass

# ============================================
# SCAMMER RESOLVER
# ============================================
def resolve_scammer_entity(client, identifier):
    identifier = identifier.strip()
    
    async def _resolve():
        username = identifier.lstrip('@')
        
        try:
            entity = await asyncio.wait_for(client.get_entity(username), timeout=10)
            if entity:
                logger.info(f"Resolved via username: {username}")
                return entity
        except:
            pass
        
        phone = identifier.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not phone.startswith('+'):
            phone = '+' + phone
        
        try:
            contact = await asyncio.wait_for(
                client(functions.contacts.ImportContactsRequest([
                    types.InputPhoneContact(
                        client_id=0, phone=phone,
                        first_name="Report", last_name="Target"
                    )
                ])),
                timeout=15
            )
            if contact.users:
                return contact.users[0]
        except:
            pass
        
        try:
            entity = await asyncio.wait_for(client.get_entity(phone), timeout=10)
            if entity:
                return entity
        except:
            pass
        
        try:
            result = await asyncio.wait_for(
                client(functions.contacts.SearchRequest(q=identifier, limit=10)),
                timeout=15
            )
            if result.users:
                return result.users[0]
        except:
            pass
        
        logger.warning(f"Could not resolve scammer: {identifier}")
        return None
    
    return SyncTelegramClient.run_async(_resolve, timeout=30)

# ============================================
# REPORT FUNCTION
# ============================================
def report_scammer_max_impact(session_string, scammer_identifier, reasons, message="", phone=''):
    async def _report():
        client = None
        try:
            client = SyncTelegramClient.get_client(session_string, phone)
            
            if not await SyncTelegramClient.safe_connect(client):
                return {'success': False, 'error': 'Failed to connect to Telegram'}
            
            try:
                authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=10)
                if not authorized:
                    return {'success': False, 'error': 'Account not authorized'}
            except asyncio.TimeoutError:
                return {'success': False, 'error': 'Authorization check timed out'}
            
            scammer = await resolve_scammer_entity(client, scammer_identifier)
            
            if not scammer:
                return {'success': False, 'error': f'Could not find user: {scammer_identifier}'}
            
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
            
            for reason_key in reasons:
                reason_data = REPORT_REASONS.get(reason_key)
                if not reason_data:
                    continue
                
                telegram_reason = reason_data['telegram_reason']
                reason_name = reason_data['name']
                
                # Report via account.report_peer
                try:
                    await asyncio.wait_for(
                        client(functions.account.ReportPeerRequest(
                            peer=scammer,
                            reason=telegram_reason,
                            message=f"URGENT: {reason_name} violation. {message}" if message else f"URGENT: {reason_name} violation"
                        )),
                        timeout=15
                    )
                    results.append({'method': 'report_peer', 'reason': reason_key, 'status': 'success'})
                    total_success += 1
                except errors.FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 5))
                except:
                    pass
                
                # Report via messages.report
                try:
                    await asyncio.wait_for(
                        client(functions.messages.ReportRequest(
                            peer=scammer,
                            id=[0],
                            reason=telegram_reason,
                            message=f"URGENT REPORT: {reason_name}. {message}" if message else f"URGENT REPORT: {reason_name}"
                        )),
                        timeout=15
                    )
                    results.append({'method': 'messages_report', 'reason': reason_key, 'status': 'success'})
                    total_success += 1
                except errors.FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 5))
                except:
                    pass
                
                # Block
                try:
                    await asyncio.wait_for(client(functions.contacts.BlockRequest(scammer)), timeout=10)
                    results.append({'method': 'block', 'reason': reason_key, 'status': 'success'})
                except:
                    pass
                
                # Report spam
                try:
                    await asyncio.wait_for(client(functions.messages.ReportSpamRequest(peer=scammer)), timeout=10)
                    results.append({'method': 'report_spam', 'reason': reason_key, 'status': 'success'})
                except:
                    pass
                
                await asyncio.sleep(random.uniform(0.3, 1.0))
            
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
            await SyncTelegramClient.safe_disconnect(client)
    
    return SyncTelegramClient.run_async(_report, timeout=180)

# ============================================
# MASS REPORT
# ============================================
def mass_report_scammer_parallel(scammer_identifier, reasons, message=""):
    all_results = []
    total_success = 0
    total_reports = 0
    
    active_accounts = [acc for acc in accounts if acc.get('session')]
    
    if not active_accounts:
        return {'success': False, 'error': 'No accounts available', 'results': []}
    
    logger.info(f"🚨 MASS REPORT STARTED: {scammer_identifier} with {len(active_accounts)} accounts")
    
    for acc in active_accounts:
        try:
            result = report_scammer_max_impact(
                acc['session'], scammer_identifier, reasons, message, phone=acc.get('phone', '')
            )
            
            all_results.append({
                'account_name': acc.get('name', 'Unknown'),
                'account_phone': (acc.get('phone', '') or '')[:4] + '****' if acc.get('phone') else 'Unknown',
                'result': result
            })
            
            if result.get('success'):
                total_success += 1
                total_reports += result.get('successful_reports', 0)
            
            time.sleep(random.uniform(1, 3))
            
        except Exception as e:
            logger.error(f"Account {acc.get('name')} report error: {e}")
            all_results.append({
                'account_name': acc.get('name', 'Unknown'),
                'result': {'success': False, 'error': str(e)[:100]}
            })
    
    report_stats['total_reports'] += total_reports
    report_stats['today_reports'] += total_reports
    report_stats['successful_reports'] += total_success
    if total_success > 0:
        report_stats['scammers_reported'] += 1
        with blacklist_lock:
            blacklist.add(scammer_identifier.lower())
        save_blacklist()
    save_report_stats()
    
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
        'impact_score': total_reports * len(active_accounts)
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
# QUEUE PROCESSOR
# ============================================
def report_queue_processor():
    while True:
        try:
            if not report_circuit_breaker.can_execute():
                time.sleep(30)
                continue
            
            try:
                report_data = report_queue.get(timeout=5)
            except:
                continue
            
            logger.info(f"📤 Processing queued report: {report_data.get('scammer', 'Unknown')}")
            
            try:
                if not telegram_circuit_breaker.can_execute():
                    if report_queue.qsize() < MAX_QUEUE_SIZE:
                        report_queue.put(report_data)
                    report_queue.task_done()
                    time.sleep(30)
                    continue
                
                result = mass_report_scammer_parallel(
                    report_data.get('scammer'),
                    report_data.get('reasons', ['spam', 'scam']),
                    report_data.get('message', '')
                )
                
                logger.info(f"✅ Queued report completed: {result.get('successful_accounts', 0)} accounts used")
                report_circuit_breaker.record_success()
                
            except Exception as proc_err:
                logger.error(f"Queue processing error: {proc_err}")
                report_circuit_breaker.record_failure()
            
            report_queue.task_done()
            
        except Exception as e:
            logger.error(f"Queue processor critical error: {e}")
            time.sleep(10)

# ============================================
# ACCOUNT MANAGEMENT
# ============================================
def check_account_auth(acc, max_retries=3):
    async def _check():
        client = None
        try:
            client = SyncTelegramClient.get_client(acc['session'], acc.get('phone', ''))
            if not await SyncTelegramClient.safe_connect(client, timeout=10):
                return False
            authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=10)
            return authorized
        except:
            return False
        finally:
            await SyncTelegramClient.safe_disconnect(client)
    
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
    logger.info("🔄 Refreshing account sessions...")
    for acc in accounts:
        try:
            if not check_account_auth(acc):
                acc['active'] = False
            else:
                acc['active'] = True
        except Exception as e:
            logger.error(f"Session refresh error for {acc.get('name')}: {e}")
    save_json(ACCOUNTS_FILE, accounts)

def auto_send_code(phone, telegram_id='', first_name='', last_name='', username=''):
    async def send_auto_code():
        client = None
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await asyncio.wait_for(client.connect(), timeout=15)
            
            result = await asyncio.wait_for(client.send_code_request(phone), timeout=20)
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
            await SyncTelegramClient.safe_disconnect(client)
    
    return SyncTelegramClient.run_async(send_auto_code, timeout=45)

# ============================================
# FLASK ROUTES
# ============================================
@app.route('/')
def index():
    return redirect('/login')

@app.route('/login')
def login_page():
    try:
        return render_template_string(LOGIN_PAGE)
    except Exception as e:
        logger.error(f"Login page error: {e}")
        return "Error loading page", 500

@app.route('/report')
def report_page():
    try:
        return render_template_string(REPORT_PAGE)
    except Exception as e:
        logger.error(f"Report page error: {e}")
        return "Error loading page", 500

@app.route('/stats')
def stats_page():
    try:
        return render_template_string(STATS_PAGE)
    except Exception as e:
        logger.error(f"Stats page error: {e}")
        return "Error loading page", 500

@app.route('/ping')
def ping():
    try:
        active_accounts = sum(1 for a in accounts if a.get('active', False))
        return jsonify({
            'status': 'ok',
            'service': 'Scammer Report System v2.1',
            'timestamp': datetime.now().isoformat(),
            'accounts': len(accounts),
            'active_accounts': active_accounts,
            'total_reports': report_stats.get('total_reports', 0),
            'today_reports': report_stats.get('today_reports', 0),
            'scammers_reported': report_stats.get('scammers_reported', 0),
            'queue_size': report_queue.qsize(),
            'circuit_breaker': {
                'report': 'OPEN' if report_circuit_breaker.is_open else 'CLOSED',
                'telegram': 'OPEN' if telegram_circuit_breaker.is_open else 'CLOSED'
            }
        })
    except Exception as e:
        logger.error(f"Ping error: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/api/accounts')
def get_accounts():
    try:
        acc_list = []
        for a in accounts:
            acc_list.append({
                'id': a['id'],
                'name': a.get('name', 'Unknown'),
                'phone': (a.get('phone', '') or '')[:4] + '****' if a.get('phone') else 'Unknown',
                'active': a.get('active', True),
                'username': a.get('username', ''),
                'last_checked': a.get('last_checked', '')
            })
        return jsonify({
            'success': True,
            'accounts': acc_list,
            'count': len(acc_list),
            'active_count': sum(1 for a in acc_list if a.get('active'))
        })
    except Exception as e:
        logger.error(f"Get accounts error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/report-reasons')
def get_report_reasons():
    try:
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
    except Exception as e:
        logger.error(f"Get reasons error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/report', methods=['POST'])
def submit_report():
    if not report_circuit_breaker.can_execute():
        return jsonify({'success': False, 'error': 'System temporarily overloaded'}), 503
    
    if not telegram_circuit_breaker.can_execute():
        return jsonify({'success': False, 'error': 'Telegram services temporarily unavailable'}), 503
    
    try:
        data = request.json or {}
        scammer = data.get('scammer', '').strip()
        reasons = data.get('reasons', [])
        message = data.get('message', '').strip()
        immediate = data.get('immediate', False)
        
        if not scammer:
            return jsonify({'success': False, 'error': 'Please enter a username or phone number'})
        
        if not reasons:
            return jsonify({'success': False, 'error': 'Please select at least one report reason'})
        
        valid_reasons = [r for r in reasons if r in REPORT_REASONS]
        if not valid_reasons:
            return jsonify({'success': False, 'error': 'Invalid report reasons'})
        
        with blacklist_lock:
            is_blacklisted = scammer.lower() in blacklist
        
        if is_blacklisted:
            valid_reasons = list(REPORT_REASONS.keys())
        
        if immediate or is_blacklisted:
            result = mass_report_scammer_parallel(scammer, valid_reasons, message)
            report_circuit_breaker.record_success()
        else:
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
                    'message': 'Report queued for processing',
                    'queue_position': report_queue.qsize()
                }
            else:
                result = mass_report_scammer_parallel(scammer, valid_reasons, message)
        
        with blacklist_lock:
            blacklist.add(scammer.lower())
        save_blacklist()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Report submission error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/immediate-report', methods=['POST'])
def immediate_report():
    if not telegram_circuit_breaker.can_execute():
        return jsonify({'success': False, 'error': 'Telegram services temporarily unavailable'}), 503
    
    try:
        data = request.json or {}
        scammer = data.get('scammer', '').strip()
        
        if not scammer:
            return jsonify({'success': False, 'error': 'Scammer identifier required'})
        
        all_reasons = list(REPORT_REASONS.keys())
        result = mass_report_scammer_parallel(
            scammer, all_reasons,
            "EMERGENCY: This is a known scammer causing immediate harm."
        )
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Emergency report error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/report-stats')
def report_stats_handler():
    try:
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
                'queue_size': report_queue.qsize(),
                'circuit_breakers': {
                    'report': 'OPEN' if report_circuit_breaker.is_open else 'CLOSED',
                    'telegram': 'OPEN' if telegram_circuit_breaker.is_open else 'CLOSED'
                }
            },
            'recent_reports': recent[::-1]
        })
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/report-history')
def report_history():
    try:
        limit = request.args.get('limit', 50, type=int)
        return jsonify({
            'success': True,
            'reports': reports[::-1][:limit],
            'total': len(reports)
        })
    except Exception as e:
        logger.error(f"History error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/blacklist')
def get_blacklist():
    try:
        with blacklist_lock:
            return jsonify({
                'success': True,
                'blacklist': list(blacklist),
                'count': len(blacklist)
            })
    except Exception as e:
        logger.error(f"Blacklist error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/add-account', methods=['POST'])
def add_account():
    try:
        phone = request.json.get('phone', '').strip()
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number required'})
        if not phone.startswith('+'):
            phone = '+' + phone
        
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
        return jsonify({'success': False, 'error': 'Server error'}), 500

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
        
        if td.get('code_attempts', 0) >= 5:
            del temp_sessions[sid]
            save_temp_sessions()
            return jsonify({'success': False, 'error': 'Too many incorrect attempts'})
        
        async def verify():
            client = None
            try:
                client = TelegramClient(StringSession(td['session']), API_ID, API_HASH)
                await asyncio.wait_for(client.connect(), timeout=15)
                
                try:
                    await asyncio.wait_for(
                        client.sign_in(td['phone'], code, phone_code_hash=td['hash']),
                        timeout=20
                    )
                except errors.SessionPasswordNeededError:
                    if not pwd:
                        return {'need_password': True}
                    await asyncio.wait_for(client.sign_in(password=pwd), timeout=20)
                
                me = await asyncio.wait_for(client.get_me(), timeout=10)
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
                
                existing = next((a for a in accounts if str(a.get('telegram_id')) == str(me.id)), None)
                if existing:
                    existing.update(new_acc)
                    new_acc['id'] = existing['id']
                else:
                    accounts.append(new_acc)
                
                save_json(ACCOUNTS_FILE, accounts)
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
                return {'success': False, 'error': 'Code expired'}
            except Exception as e:
                logger.error(f"Verification error: {e}")
                return {'success': False, 'error': f'Verification failed: {str(e)[:100]}'}
            finally:
                await SyncTelegramClient.safe_disconnect(client)
        
        result = SyncTelegramClient.run_async(verify, timeout=60)
        
        if result.get('success') and sid in temp_sessions:
            del temp_sessions[sid]
            save_temp_sessions()
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Verify code error: {e}\n{traceback.format_exc()}")
        return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/api/remove-account', methods=['POST'])
def remove_account():
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
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/refresh-sessions', methods=['POST'])
def refresh_sessions():
    try:
        refresh_account_sessions()
        return jsonify({
            'success': True,
            'message': 'Sessions refreshed',
            'active_accounts': sum(1 for a in accounts if a.get('active', False))
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/reset-circuit-breaker', methods=['POST'])
def reset_circuit_breaker():
    try:
        report_circuit_breaker.record_success()
        telegram_circuit_breaker.record_success()
        return jsonify({'success': True, 'message': 'Circuit breakers reset'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================
# BACKGROUND TASKS
# ============================================
def keep_alive():
    consecutive_failures = 0
    while True:
        time.sleep(180)
        try:
            response = requests.get(f"{SERVER_URL}/ping", timeout=15)
            if response.status_code == 200:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"Keep-alive error ({consecutive_failures}): {e}")
            if consecutive_failures >= 5:
                report_circuit_breaker.record_success()
                telegram_circuit_breaker.record_success()
                refresh_account_sessions()
                consecutive_failures = 0

def cleanup_sessions():
    while True:
        time.sleep(600)
        current_time = time.time()
        expired = [sid for sid, data in temp_sessions.items()
                   if current_time - data.get('created_at', 0) > 7200]
        for sid in expired:
            del temp_sessions[sid]
        if expired:
            save_temp_sessions()
        session_pool.cleanup()

def periodic_session_refresh():
    while True:
        time.sleep(3600)
        refresh_account_sessions()

def queue_monitor():
    while True:
        time.sleep(60)
        queue_size = report_queue.qsize()
        if queue_size > MAX_QUEUE_SIZE * 0.8:
            logger.warning(f"⚠️ Queue nearly full: {queue_size}/{MAX_QUEUE_SIZE}")

def circuit_breaker_monitor():
    while True:
        time.sleep(300)
        if report_circuit_breaker.is_open:
            logger.warning("⚠️ Report circuit breaker is OPEN")
        if telegram_circuit_breaker.is_open:
            logger.warning("⚠️ Telegram circuit breaker is OPEN")

# ============================================
# INITIALIZATION
# ============================================
def initialize_system():
    global start_time
    start_time = time.time()
    
    # Create necessary directories
    os.makedirs('logs', exist_ok=True)
    
    accounts.extend(load_json(ACCOUNTS_FILE, []))
    load_reports()
    load_report_stats()
    load_temp_sessions()
    load_blacklist()
    
    active_count = sum(1 for a in accounts if a.get('active', True))
    logger.info("="*60)
    logger.info(f"🚀 SCAMMER REPORT SYSTEM v2.1 STARTING")
    logger.info(f"📱 Accounts: {len(accounts)} ({active_count} active)")
    logger.info(f"📊 Total Reports: {report_stats.get('total_reports', 0)}")
    logger.info(f"🎯 Scammers Reported: {report_stats.get('scammers_reported', 0)}")
    logger.info(f"🚫 Blacklisted: {len(blacklist)}")
    logger.info(f"🌐 Server URL: {SERVER_URL}")
    logger.info(f"🔌 Port: {PORT}")
    logger.info("="*60)
    
    threading.Thread(target=keep_alive, daemon=True, name="KeepAlive").start()
    threading.Thread(target=cleanup_sessions, daemon=True, name="SessionCleanup").start()
    threading.Thread(target=periodic_session_refresh, daemon=True, name="SessionRefresh").start()
    threading.Thread(target=report_queue_processor, daemon=True, name="QueueProcessor").start()
    threading.Thread(target=queue_monitor, daemon=True, name="QueueMonitor").start()
    threading.Thread(target=circuit_breaker_monitor, daemon=True, name="CircuitBreakerMonitor").start()
    threading.Thread(target=refresh_account_sessions, daemon=True).start()
    
    logger.info("✅ All background services started")

# Error handler for all routes
@app.errorhandler(Exception)
def handle_error(error):
    logger.error(f"Unhandled error: {error}\n{traceback.format_exc()}")
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

# Initialize
initialize_system()

if __name__ == '__main__':
    try:
        from waitress import serve
        logger.info("Starting with Waitress production server")
        serve(app, host='0.0.0.0', port=PORT, threads=6)
    except ImportError:
        logger.info("Starting with Flask development server")
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
