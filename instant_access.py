import os
import json
from flask import Flask, request, render_template_string, redirect, url_for, flash, session
from werkzeug.utils import secure_filename
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import time
from datetime import datetime
import stripe
import os as _os_env
import urllib.parse

# Email providers (SendGrid)
try:
    import sendgrid
    from sendgrid.helpers.mail import Mail
except Exception:
    sendgrid = None
    Mail = None

app = Flask(__name__)
app.secret_key = 'instant-access-secret-key-change-me'
_last_error = {'message': None}

# Stripe setup via environment (set STRIPE_SECRET_KEY and STRIPE_PRICE_ID)
stripe.api_key = _os_env.getenv('STRIPE_SECRET_KEY', '')
STRIPE_PRICE_ID = _os_env.getenv('STRIPE_PRICE_ID', '')  # Create in Stripe dashboard

CONFIG_FILE = 'instantaccess_config.json'
GRANTS_FILE = 'grants_log.json'
ROLE_QUEUE_FILE = 'role_queue.json'

# Discord OAuth environment configuration
DISCORD_CLIENT_ID = _os_env.getenv('DISCORD_CLIENT_ID')
DISCORD_CLIENT_SECRET = _os_env.getenv('DISCORD_CLIENT_SECRET')
DISCORD_REDIRECT_URI = _os_env.getenv('DISCORD_REDIRECT_URI', 'http://localhost:5000/discord-callback')
CRON_SECRET = _os_env.getenv('CRON_SECRET')

# Simple data store for grants
def load_grants():
    if os.path.exists(GRANTS_FILE):
        with open(GRANTS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_grant(data):
    grants = load_grants()
    grants.append(data)
    with open(GRANTS_FILE, 'w') as f:
        json.dump(grants, f)

# Config management
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

# Role-queue persistence
def load_role_queue():
    if os.path.exists(ROLE_QUEUE_FILE):
        with open(ROLE_QUEUE_FILE, 'r') as f:
            try:
                return json.load(f)
            except Exception:
                return []
    return []

def save_role_queue(queue):
    with open(ROLE_QUEUE_FILE, 'w') as f:
        json.dump(queue, f)

def upsert_queue_entry(email=None, user_id=None):
    """Upsert a queue entry keyed by email if provided, otherwise by user_id."""
    queue = load_role_queue()
    updated = False
    if email:
        for entry in queue:
            if entry.get('email') == email:
                if user_id:
                    entry['user_id'] = user_id
                entry['timestamp'] = datetime.now().isoformat()
                updated = True
                break
    if not updated:
        # If matching by user_id only
        if user_id and not email:
            for entry in queue:
                if entry.get('user_id') == user_id:
                    entry['timestamp'] = datetime.now().isoformat()
                    updated = True
                    break
    if not updated:
        queue.append({
            'email': email,
            'user_id': user_id,
            'timestamp': datetime.now().isoformat()
        })
    save_role_queue(queue)
    return queue

# HTML Template - Clean, modern UI
SETUP_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>InstantAccess Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { 
            max-width: 600px; 
            margin: 0 auto; 
            background: white; 
            border-radius: 12px; 
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            overflow: hidden;
        }
        .header { 
            background: #4f46e5; 
            color: white; 
            padding: 30px; 
            text-align: center; 
        }
        .header h1 { font-size: 2em; margin-bottom: 10px; }
        .content { padding: 30px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: 500; color: #374151; }
        input, select { 
            width: 100%; padding: 12px; border: 2px solid #e5e7eb; 
            border-radius: 8px; font-size: 16px; transition: border-color 0.2s;
        }
        input:focus, select:focus { outline: none; border-color: #4f46e5; }
        .btn { 
            background: #10b981; color: white; padding: 12px 24px; 
            border: none; border-radius: 8px; cursor: pointer; 
            font-size: 16px; font-weight: 600; width: 100%; 
            transition: background 0.2s;
        }
        .btn:hover { background: #059669; }
        .btn-danger { background: #ef4444; }
        .btn-danger:hover { background: #dc2626; }
        .status { padding: 12px; border-radius: 8px; margin: 20px 0; text-align: center; }
        .status.live { background: #d1fae5; color: #065f46; }
        .status.error { background: #fee2e2; color: #991b1b; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 20px; margin: 20px 0; }
        .stat-card { background: #f9fafb; padding: 20px; border-radius: 8px; text-align: center; }
        .stat-number { font-size: 2em; font-weight: bold; color: #10b981; }
        .upgrade-cta { background: linear-gradient(135deg, #f59e0b, #d97706); color: white; text-align: center; padding: 20px; }
        .grants-table { margin-top: 30px; }
        .grants-table table { width: 100%; border-collapse: collapse; }
        .grants-table th, .grants-table td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
        .grants-table th { background: #f3f4f6; font-weight: 600; }
        .grants-table tr:hover { background: #f9fafb; }
        @media (max-width: 600px) { .grants-table th, .grants-table td { font-size: 14px; padding: 8px; } }
    </style>
    </head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚡ InstantAccess Bot</h1>
            <p>Automate access granting in seconds</p>
        </div>
        <div class="content">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="status {{ 'live' if 'success' in category else 'error' }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            {% if config.bot_status == 'active' %}
                <div class="stats">
                    <div class="stat-card">
                        <div class="stat-number">{{ config.grants_today }}</div>
                        <div>Grants Today</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{{ config.total_grants }}</div>
                        <div>Total Grants</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{{ config.pending_roles }}</div>
                        <div>Pending Roles</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-number">{{ '🟢 Live' if config.listening else '🔴 Stopped' }}</div>
                        <div>Status</div>
                    </div>
                </div>
                
                <div style="text-align: center; margin: 30px 0;">
                    <p><strong>Webhook URL:</strong> <code style="background: #f3f4f6; padding: 4px 8px; border-radius: 4px;">{{ webhook_url }}</code></p>
                    <p><strong>Customer Connect URL:</strong> <code style="background: #f3f4f6; padding: 4px 8px; border-radius: 4px;">{{ connect_url }}</code></p>
                    <button class="btn" onclick="testWebhook()">🧪 Test with Fake Sale</button>
                    <button class="btn btn-danger" onclick="stopBot()">🛑 Stop Bot</button>
                </div>

                <div class="grants-table">
                    <h3>Recent Grants</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Email</th>
                                <th>Product</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for grant in grants|sort(attribute='timestamp', reverse=True)|slice(0, 10) %}
                                <tr>
                                    <td>{{ grant.timestamp[:19] }}</td>
                                    <td>{{ grant.email }}</td>
                                    <td>{{ grant.product }}</td>
                                    <td>{{ '✅ Success' if grant.success else '❌ Failed' }}</td>
                                </tr>
                            {% else %}
                                <tr>
                                    <td colspan="4" style="color:#6b7280;">No grants yet. Run a test above.</td>
                                </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            {% else %}
                <form method="POST">
                    <div class="form-group">
                        <label>Platform Secret Key <span style="color:#6b7280; font-weight:normal;">(optional)</span></label>
                        <input type="password" name="webhook_secret" placeholder="Shopify/Whop webhook secret (optional)">
                        <small>Optional for MVP. Add later to verify incoming webhooks.</small>
                    </div>
                    <div class="form-group">
                        <label>Action Type</label>
                        <select name="action_type" required>
                            <option value="">Choose what to automate</option>
                            <option value="email">📧 Send Welcome Email</option>
                            <option value="discord">💬 Add Discord Role</option>
                        </select>
                    </div>
                    <div class="form-group" id="email-fields" style="display: none;">
                        <div class="form-group">
                            <label style="display:block; margin-bottom:8px;">Email Provider</label>
                            <label style="display:block; margin-bottom:6px;">
                                <input type="radio" name="email_provider" value="instantaccess" checked> 📧 Send from InstantAccess (Recommended)
                            </label>
                            <small style="color:#6b7280;">We handle delivery. Add your Reply-To. Free up to 500/month.</small>
                            <label style="display:block; margin-top:12px;">
                                <input type="radio" name="email_provider" value="custom"> 🔌 Use my SendGrid API key
                            </label>
                        </div>
                        <div id="custom-email-fields" style="display:none;">
                            <label>SendGrid API Key</label>
                            <input type="password" name="sendgrid_api_key" placeholder="SG.XXXX...">
                        </div>
                        <label>Reply-To Email</label>
                        <input type="email" name="reply_to_email" placeholder="hello@yourbrand.com">
                        <label>Your Business Name</label>
                        <input type="text" name="business_name" placeholder="Your Brand">
                        <label>Custom Welcome Message</label>
                        <textarea name="custom_message" rows="3" placeholder="Check your Discord server for VIP access!"></textarea>
                        <div style="margin-top:12px; padding:8px; background:#f9fafb; border-radius:8px;">
                            <div style="font-size:12px; color:#6b7280;">Legacy Gmail SMTP (optional)</div>
                            <label>Email Address</label>
                            <input type="email" name="email_user" placeholder="your@gmail.com">
                            <label>Email Password</label>
                            <input type="password" name="email_pass" placeholder="App password">
                        </div>
                    </div>
                    <div class="form-group" id="discord-fields" style="display: none;">
                        <label>Discord Bot Token</label>
                        <input type="password" name="discord_token" placeholder="Bot token from Discord Developer Portal">
                        <label>Server ID</label>
                        <input type="text" name="guild_id" placeholder="123456789012345678">
                        <label>Role ID</label>
                        <input type="text" name="role_id" placeholder="234567890123456789">
                        <div style="display:flex; gap:8px; align-items:center; margin:8px 0;">
                            <button type="button" class="btn" id="fetch-roles-btn">Fetch Roles</button>
                            <select id="roles-dropdown" style="flex:1; display:none; padding:10px; border:2px solid #e5e7eb; border-radius:8px;"></select>
                        </div>
                        <label>Test User ID</label>
                        <input type="text" name="test_user_id" placeholder="User ID for test assignment">
                        <small>Ensure bot is in the server and its role is above the target role.</small>
                        <div style="display:flex; gap:8px; align-items:center; margin-top:8px;">
                            <button type="button" class="btn" id="check-member-btn">Check Member</button>
                            <span id="check-member-status" style="font-size:14px; color:#374151;"></span>
                        </div>
                        <div class="form-group" style="margin-top:12px;">
                            <label>Search by Username/Display Name</label>
                            <div style="display:flex; gap:8px; align-items:center;">
                                <input type="text" id="search-query" placeholder="e.g., bob, @bob" style="flex:1;">
                                <button type="button" class="btn" id="search-users-btn">Search Users</button>
                            </div>
                            <select id="users-dropdown" style="margin-top:8px; width:100%; display:none; padding:10px; border:2px solid #e5e7eb; border-radius:8px;"></select>
                            <small>Requires Server Members Intent enabled on your bot.</small>
                        </div>
                    </div>
                    <button type="submit" class="btn">🚀 Start Bot</button>
                </form>
                <div style="text-align: center; margin: 16px 0;">
                    <button class="btn" type="button" onclick="testWebhook()">🧪 Test with Fake Sale</button>
                </div>
            {% endif %}
            
            {% if config.bot_status != 'active' %}
                <div class="upgrade-cta" style="margin-top: 30px;">
                    <h3>Want Unlimited + Discord?</h3>
                    <p>Upgrade for $9/month - Discord roles, unlimited grants, priority support</p>
                    <button class="btn" onclick="upgrade()">Upgrade Now</button>
                </div>
            {% endif %}
        </div>
    </div>
    
    <script>
        function toggleFields() {
            const action = document.querySelector('[name="action_type"]').value;
            document.getElementById('email-fields').style.display = action === 'email' ? 'block' : 'none';
            document.getElementById('discord-fields').style.display = action === 'discord' ? 'block' : 'none';
            const provider = document.querySelector('input[name="email_provider"]:checked')?.value;
            const customFields = document.getElementById('custom-email-fields');
            if (customFields) customFields.style.display = (action === 'email' && provider === 'custom') ? 'block' : 'none';
        }
        const actionSelect = document.querySelector('[name="action_type"]');
        if (actionSelect) {
            actionSelect.addEventListener('change', toggleFields);
            // Initialize visibility on load
            toggleFields();
        }
        const providerRadios = document.querySelectorAll('input[name="email_provider"]');
        providerRadios.forEach(r => r.addEventListener('change', toggleFields));
        
        // Fetch Roles helper
        const fetchRolesBtn = document.getElementById('fetch-roles-btn');
        if (fetchRolesBtn) {
            fetchRolesBtn.addEventListener('click', async () => {
                const token = document.querySelector('[name="discord_token"]').value.trim();
                const guildId = document.querySelector('[name="guild_id"]').value.trim();
                if (!token || !guildId) {
                    alert('Enter Bot Token and Server ID first');
                    return;
                }
                const resp = await fetch('/discord/roles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token, guild_id: guildId })
                });
                const data = await resp.json();
                if (!data.success) {
                    alert('Failed to fetch roles: ' + (data.error || 'Unknown error'));
                    return;
                }
                const dropdown = document.getElementById('roles-dropdown');
                dropdown.innerHTML = '';
                data.roles.forEach(r => {
                    const opt = document.createElement('option');
                    opt.value = r.id;
                    opt.textContent = r.name + (r.managed ? ' (managed)' : '');
                    dropdown.appendChild(opt);
                });
                dropdown.style.display = 'block';
                dropdown.addEventListener('change', () => {
                    document.querySelector('[name="role_id"]').value = dropdown.value;
                });
            });
        }

        // Check Member helper
        const checkMemberBtn = document.getElementById('check-member-btn');
        const checkMemberStatus = document.getElementById('check-member-status');
        if (checkMemberBtn) {
            checkMemberBtn.addEventListener('click', async () => {
                const token = document.querySelector('[name="discord_token"]').value.trim();
                const guildId = document.querySelector('[name="guild_id"]').value.trim();
                let userId = document.querySelector('[name="test_user_id"]').value.trim();
                // Extract numeric ID from mention forms like <@123>, <@!123>, or @username (no conversion for latter)
                const mentionMatch = userId.match(/<@!?([0-9]{16,20})>/);
                if (mentionMatch) userId = mentionMatch[1];
                if (!token || !guildId || !userId) {
                    alert('Enter Bot Token, Server ID, and Test User ID first');
                    return;
                }
                checkMemberStatus.textContent = 'Checking...';
                const resp = await fetch('/discord/check-member', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token, guild_id: guildId, user_id: userId })
                });
                const data = await resp.json();
                if (data.success) {
                    checkMemberStatus.textContent = '✅ Member found';
                    checkMemberStatus.style.color = '#065f46';
                } else {
                    checkMemberStatus.textContent = '❌ ' + (data.error || 'Not found');
                    checkMemberStatus.style.color = '#991b1b';
                }
            });
        }

        // Search users helper (by username/display name)
        const searchUsersBtn = document.getElementById('search-users-btn');
        const usersDropdown = document.getElementById('users-dropdown');
        if (searchUsersBtn) {
            searchUsersBtn.addEventListener('click', async () => {
                const token = document.querySelector('[name="discord_token"]').value.trim();
                const guildId = document.querySelector('[name="guild_id"]').value.trim();
                const query = document.getElementById('search-query').value.trim();
                if (!token || !guildId) {
                    alert('Enter Bot Token and Server ID');
                    return;
                }
                usersDropdown.style.display = 'none';
                usersDropdown.innerHTML = '';
                const resp = await fetch('/discord/search-member', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token, guild_id: guildId, query })
                });
                const data = await resp.json();
                if (!data.success) {
                    alert('Search failed: ' + (data.error || 'Unknown error')); 
                    return; 
                }
                if (!data.members || data.members.length === 0) {
                    alert('No users found for that query');
                    return;
                }
                data.members.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.id;
                    opt.textContent = `${m.username}${m.discriminator ? '#' + m.discriminator : ''}` + (m.global_name ? ` (${m.global_name})` : '');
                    usersDropdown.appendChild(opt);
                });
                usersDropdown.style.display = 'block';
                usersDropdown.addEventListener('change', () => {
                    document.querySelector('[name="test_user_id"]').value = usersDropdown.value;
                });
                // Preselect first result
                document.querySelector('[name="test_user_id"]').value = usersDropdown.options[0].value;
            });
        }
        
        function testWebhook() {
            const action = document.querySelector('[name="action_type"]')?.value || '';
            const emailUser = document.querySelector('[name="email_user"]')?.value || '';
            const emailPass = document.querySelector('[name="email_pass"]')?.value || '';
            const discordToken = document.querySelector('[name="discord_token"]')?.value || '';
            const guildId = document.querySelector('[name="guild_id"]')?.value || '';
            const roleId = document.querySelector('[name="role_id"]')?.value || '';
            let testUserId = document.querySelector('[name="test_user_id"]')?.value || '';
            const m = testUserId.match(/<@!?([0-9]{16,20})>/);
            if (m) testUserId = m[1];
            const emailProvider = document.querySelector('input[name="email_provider"]:checked')?.value || 'instantaccess';
            const sendgridKey = document.querySelector('[name="sendgrid_api_key"]')?.value || '';
            const replyTo = document.querySelector('[name="reply_to_email"]')?.value || '';
            const businessName = document.querySelector('[name="business_name"]')?.value || '';
            const customMessage = document.querySelector('[name="custom_message"]')?.value || '';
            fetch('/test-webhook', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    overrides: {
                        action_type: action,
                        email_provider: emailProvider,
                        sendgrid_api_key: sendgridKey,
                        reply_to_email: replyTo,
                        business_name: businessName,
                        custom_message: customMessage,
                        email_user: emailUser,
                        email_pass: emailPass,
                        discord_token: discordToken,
                        guild_id: guildId,
                        role_id: roleId,
                        test_user_id: testUserId
                    }
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    alert('✅ Success! Access would be granted instantly.');
                } else {
                    alert('❌ Test failed: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(() => alert('❌ Test failed: Network error'));
        }
        
        function stopBot() {
            if (confirm('Stop the bot?')) {
                fetch('/stop', {method: 'POST'}).then(() => location.reload());
            }
        }
        
        function upgrade() {
            fetch('/create-checkout', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    if (data.url) window.location.href = data.url;
                });
        }
    </script>
</body>
</html>
'''

@app.route('/app', methods=['GET', 'POST'])
def dashboard():
    config = load_config()
    
    if request.method == 'POST':
        # Save new config
        new_config = {
            'webhook_secret': request.form['webhook_secret'],
            'action_type': request.form['action_type'],
            'email_provider': request.form.get('email_provider') or 'instantaccess',
            'sendgrid_api_key': request.form.get('sendgrid_api_key'),
            'reply_to_email': request.form.get('reply_to_email'),
            'business_name': request.form.get('business_name'),
            'custom_message': request.form.get('custom_message'),
            'email_user': request.form.get('email_user'),
            'email_pass': request.form.get('email_pass'),
            'discord_token': request.form.get('discord_token'),
            'guild_id': request.form.get('guild_id'),
            'role_id': request.form.get('role_id'),
            'test_user_id': request.form.get('test_user_id'),
            'bot_status': 'active',
            'listening': True,
            'grants_today': 0,
            'total_grants': len(load_grants())
        }
        save_config(new_config)
        flash('✅ Bot started! Point your webhook to the URL above.', 'success')
        return redirect(url_for('dashboard'))
    
    webhook_url = request.host_url + 'webhook'
    stats = {
        'bot_status': config.get('bot_status', 'inactive'),
        'listening': config.get('listening', False),
        'grants_today': config.get('grants_today', 0),
        'total_grants': len(load_grants()),
        'webhook_url': webhook_url,
        'pending_roles': len(load_role_queue())
    }
    
    connect_url = request.host_url + 'connect-discord'
    grants = load_grants()
    return render_template_string(SETUP_TEMPLATE, config=stats, webhook_url=webhook_url, connect_url=connect_url, grants=grants)

@app.route('/')
def root_index():
    return redirect('/index.html')


# Customer OAuth connect (buyer)
@app.route('/connect-discord')
def connect_discord():
    if not DISCORD_CLIENT_ID or not DISCORD_REDIRECT_URI:
        flash('Discord OAuth is not configured on the server.', 'error')
        return redirect(url_for('dashboard'))
    state = request.args.get('state')  # optional email
    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'identify',
    }
    if state:
        params['state'] = state
    qs = urllib.parse.urlencode(params)
    return redirect(f'https://discord.com/api/oauth2/authorize?{qs}')

def get_discord_user(access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get('https://discord.com/api/users/@me', headers=headers)
    if resp.status_code == 200:
        return resp.json()
    return None

def check_server_membership(discord_user_id, config):
    token = config.get('discord_token')
    guild_id = config.get('guild_id')
    if not token or not guild_id or not discord_user_id:
        return False
    url = f'https://discord.com/api/v10/guilds/{guild_id}/members/{discord_user_id}'
    headers = { 'Authorization': f'Bot {token}' }
    resp = requests.get(url, headers=headers)
    return resp.status_code == 200

@app.route('/discord-callback')
def discord_callback():
    code = request.args.get('code')
    email_state = request.args.get('state')  # may be buyer email
    if not code:
        flash('❌ Discord authorization failed.', 'error')
        return redirect(url_for('dashboard'))
    data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'scope': 'identify'
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    token_resp = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    token_data = token_resp.json() if token_resp is not None else {}
    access_token = token_data.get('access_token')
    if not access_token:
        flash('❌ Could not authenticate with Discord.', 'error')
        return redirect(url_for('dashboard'))
    user = get_discord_user(access_token)
    if not user:
        flash('❌ Failed to fetch Discord user.', 'error')
        return redirect(url_for('dashboard'))
    # Link email->user_id in queue
    upsert_queue_entry(email=email_state, user_id=user.get('id'))
    config = load_config()
    # If already in server, try to grant immediately
    if config.get('action_type') == 'discord' and check_server_membership(user.get('id'), config):
        success = grant_discord_role(user.get('id'), config)
        if success:
            # Remove any fulfilled entries with this user/email
            queue = load_role_queue()
            queue = [e for e in queue if not ((email_state and e.get('email') == email_state) or e.get('user_id') == user.get('id'))]
            save_role_queue(queue)
    flash(f'✅ Connected Discord user: {user.get("username")}', 'success')
    return redirect(url_for('dashboard'))

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    config = load_config()
    if not config.get('listening'):
        return '', 503
    
    # Enforce HMAC if secret configured (Shopify: X-Shopify-Hmac-Sha256; Whop: X-Whop-Signature)
    if config.get('webhook_secret'):
        signature = request.headers.get('X-Shopify-Hmac-Sha256') or request.headers.get('X-Whop-Signature')
        if not signature:
            return '', 401
        expected = hmac.new(config['webhook_secret'].encode(), request.get_data(), hashlib.sha256).hexdigest()
        valid = (signature == f'sha256={expected}') or (signature == expected)
        if not valid:
            return '', 401

    data = request.json or request.form.to_dict()
    
    # Extract customer info (works for Shopify/Whop)
    customer_email = (data.get('customer', {}).get('email') or 
                     data.get('email') or 
                     data.get('user', {}).get('email'))
    
    product_name = (data.get('line_items', [{}])[0].get('title') or
                   data.get('product', {}).get('name') or
                   'Product')
    
    if not customer_email:
        return '', 400
    
    # Process action
    success = False
    if config['action_type'] == 'email':
        success = send_welcome_email(customer_email, product_name, config)
    elif config['action_type'] == 'discord' and config.get('discord_token'):
        # Try to find a queued OAuth link for this email
        queued = load_role_queue()
        matched = None
        for entry in reversed(queued):  # prefer newest
            if entry.get('email') == customer_email and entry.get('user_id'):
                matched = entry
                break
        if matched and check_server_membership(matched.get('user_id'), config):
            success = grant_discord_role(matched.get('user_id'), config)
            if success:
                queued.remove(matched)
                save_role_queue(queued)
        else:
            # Queue this email until OAuth is completed
            upsert_queue_entry(email=customer_email, user_id=None)
            success = True  # queued counts as success
    
    # Log grant
    grant_data = {
        'timestamp': datetime.now().isoformat(),
        'email': customer_email,
        'product': product_name,
        'success': success,
        'action': config['action_type']
    }
    save_grant(grant_data)
    
    # Update stats
    config['total_grants'] = config.get('total_grants', 0) + 1
    config['grants_today'] = config.get('grants_today', 0) + 1
    save_config(config)
    
    print(f"Granted access to {customer_email}: {'✅' if success else '❌'}")
    return '', 200

INSTANTACCESS_SENDGRID_KEY = _os_env.getenv('INSTANTACCESS_SENDGRID_KEY')
INSTANTACCESS_FROM_EMAIL = _os_env.getenv('INSTANTACCESS_FROM_EMAIL', 'noreply@instantaccessbot.com')

def send_welcome_email(to_email, product_name, config):
    try:
        provider = (config.get('email_provider') or 'instantaccess').lower()
        reply_to = config.get('reply_to_email') or 'support@instantaccessbot.com'
        business_name = config.get('business_name') or 'Your Team'
        custom_message = config.get('custom_message') or 'Check your Discord server or inbox for details.'

        if provider == 'instantaccess':
            if not sendgrid or not Mail or not INSTANTACCESS_SENDGRID_KEY:
                raise RuntimeError('InstantAccess email not available (missing server API key)')
            sg = sendgrid.SendGridAPIClient(api_key=INSTANTACCESS_SENDGRID_KEY)
            message = Mail(
                from_email=INSTANTACCESS_FROM_EMAIL,
                to_emails=to_email,
                subject=f'🎉 Welcome to {product_name}! Access Granted',
                html_content=f"""
                <h1>Hi there! 👋</h1>
                <p>Thanks for purchasing <strong>{product_name}</strong>!</p>
                <p>Your access has been <strong>automatically granted</strong>.</p>
                <p>{custom_message}</p>
                <p>Best,<br>{business_name}</p>
                <hr>
                <small>💌 Sent via InstantAccess Bot</small>
                """
            )
            # SendGrid helpers for reply-to
            message.reply_to = reply_to
            response = sg.send(message)
            _last_error['message'] = None
            return response.status_code == 202

        if provider == 'custom':
            api_key = config.get('sendgrid_api_key')
            if not api_key or not sendgrid or not Mail:
                raise RuntimeError('Custom provider requires SendGrid API key')
            sg = sendgrid.SendGridAPIClient(api_key=api_key)
            message = Mail(
                from_email=INSTANTACCESS_FROM_EMAIL,
                to_emails=to_email,
                subject=f'🎉 Welcome to {product_name}! Access Granted',
                html_content=f"""
                <h1>Hi there! 👋</h1>
                <p>Thanks for purchasing <strong>{product_name}</strong>!</p>
                <p>Your access has been <strong>automatically granted</strong>.</p>
                <p>{custom_message}</p>
                <p>Best,<br>{business_name}</p>
                """
            )
            message.reply_to = reply_to
            response = sg.send(message)
            _last_error['message'] = None
            return response.status_code == 202

        # Fallback to Gmail SMTP if selected (legacy)
        msg = MIMEMultipart()
        msg['From'] = config['email_user']
        msg['To'] = to_email
        msg['Subject'] = f'🎉 Welcome to {product_name}!'
        body = f"""
        Hi there! 👋

        Thanks for purchasing {product_name}! Your access has been automatically granted.

        {custom_message}

        Best,
        {business_name}
        """
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(config['email_user'], config['email_pass'])
        text = msg.as_string()
        server.sendmail(config['email_user'], to_email, text)
        server.quit()
        _last_error['message'] = None
        return True
    except Exception as e:
        _last_error['message'] = str(e)
        print(f"Email failed: {e}")
        return False

def grant_discord_role(discord_user_id, config):
    """Assign role to a Discord user ID in a guild."""
    token = config.get('discord_token')
    guild_id = config.get('guild_id')
    role_id = config.get('role_id')
    if not token or not guild_id or not role_id or not discord_user_id:
        _last_error['message'] = 'Missing Discord token/guild_id/role_id/user_id'
        return False
    url = f'https://discord.com/api/v10/guilds/{guild_id}/members/{discord_user_id}/roles/{role_id}'
    headers = {
        'Authorization': f'Bot {token}',
        'Content-Type': 'application/json'
    }
    resp = requests.put(url, headers=headers)
    if resp.status_code == 204:
        _last_error['message'] = None
        return True
    _last_error['message'] = f'Discord API error {resp.status_code}: {resp.text}'
    return False

def process_role_queue_once():
    """Process the role queue once: try to grant roles for any entries with user_id present and membership confirmed."""
    config = load_config()
    if config.get('action_type') != 'discord' or not config.get('discord_token'):
        return {'processed': 0, 'granted': 0, 'remaining': len(load_role_queue())}
    queue = load_role_queue()
    processed = 0
    granted = 0
    changed = False
    for entry in list(queue):
        processed += 1
        user_id = entry.get('user_id')
        if not user_id:
            continue
        if check_server_membership(user_id, config):
            if grant_discord_role(user_id, config):
                queue.remove(entry)
                granted += 1
                changed = True
    if changed:
        save_role_queue(queue)
    return {'processed': processed, 'granted': granted, 'remaining': len(queue)}

@app.route('/cron/process-queue', methods=['GET', 'POST'])
def cron_process_queue():
    # Simple protection: if CRON_SECRET is set, require ?key=CRON_SECRET
    if CRON_SECRET:
        provided = request.args.get('key') or request.headers.get('X-Cron-Key')
        if provided != CRON_SECRET:
            return {'success': False, 'error': 'Forbidden'}, 403
    result = process_role_queue_once()
    return {'success': True, **result}

@app.route('/test-webhook', methods=['POST'])
def test_webhook():
    # Simulate a sale
    test_data = {
        'customer': {'email': 'test@example.com'},
        'line_items': [{'title': 'Test Product'}]
    }
    config = load_config()
    body = request.get_json(silent=True) or {}
    overrides = (body.get('overrides') or {}) if isinstance(body, dict) else {}
    # Build an effective config (overrides take precedence without persisting)
    effective_config = {**config}
    for key in ['action_type', 'email_provider', 'sendgrid_api_key', 'reply_to_email', 'business_name', 'custom_message', 'email_user', 'email_pass', 'discord_token', 'guild_id', 'role_id', 'test_user_id']:
        if overrides.get(key):
            effective_config[key] = overrides[key]
    if not effective_config.get('action_type'):
        return {'success': False, 'error': 'Configure action type first'}, 400
    success = process_webhook_data(test_data, effective_config)
    return {'success': success, 'error': None if success else (_last_error['message'] or 'Action failed')}

def process_webhook_data(data, config):
    # Same logic as webhook
    customer_email = data.get('customer', {}).get('email')
    if config['action_type'] == 'email':
        provider = (config.get('email_provider') or 'instantaccess').lower()
        if provider == 'instantaccess' or provider == 'custom':
            return send_welcome_email(customer_email, 'Test Product', config)
        # legacy SMTP requires creds
        if not config.get('email_user') or not config.get('email_pass'):
            return False
        return send_welcome_email(customer_email, 'Test Product', config)
    # Discord: use provided test user id for test flow
    if config['action_type'] == 'discord':
        discord_user_id = config.get('test_user_id')
        return grant_discord_role(discord_user_id, config)
    return True

@app.route('/create-checkout', methods=['POST'])
def create_checkout():
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': STRIPE_PRICE_ID,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=request.host_url + 'success',
            cancel_url=request.host_url + 'cancel',
        )
        return {'url': session.url}
    except Exception as e:
        return {'error': str(e)}

@app.route('/discord/roles', methods=['POST'])
def discord_roles():
    body = request.get_json(silent=True) or {}
    token = body.get('token') or ''
    guild_id = body.get('guild_id') or ''
    if not token or not guild_id:
        return {'success': False, 'error': 'Missing token or guild_id'}, 400
    url = f'https://discord.com/api/v10/guilds/{guild_id}/roles'
    headers = { 'Authorization': f'Bot {token}' }
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return {'success': False, 'error': f'Discord API {resp.status_code}: {resp.text}'}, resp.status_code
    roles = resp.json()
    # Map to minimal fields
    minimal = [{ 'id': r.get('id'), 'name': r.get('name'), 'managed': r.get('managed', False) } for r in roles]
    return {'success': True, 'roles': minimal}

@app.route('/discord/check-member', methods=['POST'])
def discord_check_member():
    body = request.get_json(silent=True) or {}
    token = body.get('token') or ''
    guild_id = body.get('guild_id') or ''
    user_id = body.get('user_id') or ''
    if not token or not guild_id or not user_id:
        return {'success': False, 'error': 'Missing token, guild_id or user_id'}, 400
    url = f'https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}'
    headers = { 'Authorization': f'Bot {token}' }
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        return {'success': True}
    return {'success': False, 'error': f'Discord API {resp.status_code}: {resp.text}'}, resp.status_code

@app.route('/discord/search-member', methods=['POST'])
def discord_search_member():
    body = request.get_json(silent=True) or {}
    token = body.get('token') or ''
    guild_id = body.get('guild_id') or ''
    query = (body.get('query') or '').strip()
    if not token or not guild_id:
        return {'success': False, 'error': 'Missing token or guild_id'}, 400
    # Discord member fetch/search APIs (require Server Members Intent)
    headers = { 'Authorization': f'Bot {token}' }
    if query:
        url = f'https://discord.com/api/v10/guilds/{guild_id}/members/search'
        params = { 'query': query, 'limit': 25 }
        resp = requests.get(url, headers=headers, params=params)
    else:
        # No query: list first 100 members
        url = f'https://discord.com/api/v10/guilds/{guild_id}/members'
        params = { 'limit': 100 }
        resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return {'success': False, 'error': f'Discord API {resp.status_code}: {resp.text}'}, resp.status_code
    members = resp.json()
    simplified = []
    for m in members:
        user = m.get('user', {})
        simplified.append({
            'id': user.get('id'),
            'username': user.get('username'),
            'discriminator': user.get('discriminator'),
            'global_name': user.get('global_name') or user.get('display_name')
        })
    return {'success': True, 'members': simplified}

@app.route('/success')
def success():
    flash('🎉 Upgrade successful! Discord support unlocked.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/cancel')
def cancel():
    flash('Checkout canceled.', 'error')
    return redirect(url_for('dashboard'))

@app.route('/stop', methods=['POST'])
def stop():
    config = load_config()
    if config:
        config['listening'] = False
        config['bot_status'] = 'inactive'
        save_config(config)
    return ('', 204)

if __name__ == '__main__':
    print("🚀 Starting InstantAccess Bot...")
    print("📱 Open http://localhost:5000 in your browser")
    # Background processor: periodically try to grant roles for queued entries
    def process_role_queue():
        while True:
            try:
                config = load_config()
                if config.get('action_type') != 'discord' or not config.get('discord_token'):
                    time.sleep(60)
                    continue
                queue = load_role_queue()
                changed = False
                for entry in list(queue):
                    user_id = entry.get('user_id')
                    if not user_id:
                        continue
                    if check_server_membership(user_id, config):
                        if grant_discord_role(user_id, config):
                            queue.remove(entry)
                            changed = True
                if changed:
                    save_role_queue(queue)
            except Exception:
                # swallow and continue to avoid crashing loop
                pass
            time.sleep(300)

    threading.Thread(target=process_role_queue, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)


