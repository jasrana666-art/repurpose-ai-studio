"""
RepurposeAI Studio - AI Content Repurposing Platform
Turn long-form content into 10+ platform-native formats.
"""
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from functools import wraps
from datetime import datetime, timedelta
import os
import json
import hashlib
import requests
import jwt
from urllib.parse import quote_plus, urlparse, urlunparse

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-repurpose-key-2026')

# Database: Auto-detect PostgreSQL from Railway/Supabase
_database_url = os.environ.get('SUPABASE_URL') or os.environ.get('DATABASE_URL')
_pg_host = os.environ.get('PGHOST')

if _database_url:
    if _database_url.startswith('postgres://'):
        _database_url = 'postgresql://' + _database_url[len('postgres://'):]
    try:
        from urllib.parse import quote_plus, urlparse, urlunparse
        _parsed = urlparse(_database_url)
        if _parsed.password:
            _safe_pw = quote_plus(_parsed.password)
            _netloc = f"{_parsed.username}:{_safe_pw}@{_parsed.hostname}"
            if _parsed.port:
                _netloc += f":{_parsed.port}"
            _database_url = urlunparse((_parsed.scheme, _netloc, _parsed.path, _parsed.params, _parsed.query, _parsed.fragment))
        if 'sslmode' not in _database_url:
            _database_url += '?sslmode=require' if '?' not in _database_url else '&sslmode=require'
    except Exception as e:
        print(f"WARNING: Failed to parse DATABASE_URL: {e}")
elif _pg_host:
    _pg_port = os.environ.get('PGPORT', '5432')
    _pg_user = os.environ.get('PGUSER', 'postgres')
    _pg_password = os.environ.get('PGPASSWORD', '')
    _pg_database = os.environ.get('PGDATABASE', 'railway')
    from urllib.parse import quote_plus
    _database_url = f"postgresql://{_pg_user}:{quote_plus(_pg_password)}@{_pg_host}:{_pg_port}/{_pg_database}?sslmode=require"

if _database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = _database_url
    print(f"Using database: PostgreSQL")
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///repurposeai.db'
    print("Using SQLite (local dev mode)")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE MODELS
# ══════════════════════════════════════════════════════════════════════════════

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(100))
    plan = db.Column(db.String(20), default='free')
    credits = db.Column(db.Integer, default=50)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active_user = db.Column(db.Boolean, default=True)

    @property
    def is_active(self):
        return self.is_active_user

    def set_password(self, password):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()

    def check_password(self, password):
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()


class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(255))
    content_type = db.Column(db.String(50))  # blog, video, podcast, interview
    original_content = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class RepurposedContent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    platform = db.Column(db.String(50))  # twitter, linkedin, instagram, tiktok, newsletter, etc.
    content_text = db.Column(db.Text)
    content_html = db.Column(db.Text)
    status = db.Column(db.String(20), default='draft')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════════════════════════════════════
# AI ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def generate_repurposed_content(original_content, platform, content_type="blog"):
    """Generate platform-specific content using AI."""
    
    platform_specs = {
        'twitter': {
            'max_chars': 280,
            'style': 'concise, engaging, 1-3 hashtags',
            'format': 'Single tweet or short thread'
        },
        'linkedin': {
            'max_chars': 3000,
            'style': 'professional, thought leadership, line breaks',
            'format': 'Long-form post with hook'
        },
        'instagram': {
            'max_chars': 2200,
            'style': 'visual-first, emoji-rich, 5-10 hashtags',
            'format': 'Caption with CTA'
        },
        'tiktok': {
            'max_chars': 300,
            'style': 'casual, trend-focused, hook in first 3 seconds',
            'format': 'Short script or caption'
        },
        'newsletter': {
            'max_chars': 5000,
            'style': 'personal, story-driven, actionable',
            'format': 'Email with subject line'
        },
        'youtube_shorts': {
            'max_chars': 500,
            'style': 'fast-paced, visual, hook-heavy',
            'format': '60-second script'
        },
        'pinterest': {
            'max_chars': 500,
            'style': 'keyword-rich, descriptive, actionable',
            'format': 'Pin description'
        },
        'reddit': {
            'max_chars': 10000,
            'style': 'authentic, community-focused, no self-promo',
            'format': 'Discussion post or comment'
        },
        'threads': {
            'max_chars': 500,
            'style': 'casual, authentic, multi-post capable',
            'format': 'Single thread post'
        },
        'medium': {
            'max_chars': 25000,
            'style': 'long-form, narrative, well-structured',
            'format': 'Article excerpt'
        }
    }
    
    spec = platform_specs.get(platform, platform_specs['twitter'])
    
    prompt = f"""You are an expert content repurposing specialist. Transform this {content_type} into a {platform}-optimized piece.

Original Content:
{original_content[:3000]}

Platform: {platform}
Max Characters: {spec['max_chars']}
Style: {spec['style']}
Format: {spec['format']}

Rules:
1. Stay within {spec['max_chars']} characters
2. Match {platform}'s native tone and format
3. Include a strong hook in the first line
4. End with a clear call-to-action
5. Use appropriate hashtags if relevant
6. Make it feel native to {platform}, NOT cross-posted

Output ONLY the final {platform} content, no explanations."""
    
    # Try multiple AI providers
    ai_result = _call_ai(prompt)
    return ai_result


def _call_ai(prompt):
    """Call AI with fallback providers."""
    
    # Try OpenRouter first
    openrouter_key = os.environ.get('OPENROUTER_API_KEY')
    if openrouter_key:
        try:
            resp = requests.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={'Authorization': f'Bearer {openrouter_key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'meta-llama/llama-3.3-70b-instruct',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 2000
                },
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content']
        except Exception as e:
            print(f"OpenRouter failed: {e}")
    
    # Try Groq
    groq_key = os.environ.get('GROQ_API_KEY')
    if groq_key:
        try:
            resp = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {groq_key}', 'Content-Type': 'application/json'},
                json={
                    'model': 'llama-3.3-70b-versatile',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 2000
                },
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content']
        except Exception as e:
            print(f"Groq failed: {e}")
    
    # Try Anthropic
    anthropic_key = os.environ.get('ANTHROPIC_API_KEY')
    if anthropic_key:
        try:
            resp = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={'x-api-key': anthropic_key, 'Content-Type': 'application/json', 'anthropic-version': '2023-06-01'},
                json={
                    'model': 'claude-sonnet-4-20250514',
                    'max_tokens': 2000,
                    'messages': [{'role': 'user', 'content': prompt}]
                },
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json()['content'][0]['text']
        except Exception as e:
            print(f"Anthropic failed: {e}")
    
    # Fallback: return a template
    return f"[AI content generation requires API key setup]\n\nTemplate for your content:\n\nHook: [First line grabber]\nBody: [Key points from original]\nCTA: [What should reader do next?]"


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'RepurposeAI Studio'})


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid email or password')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        name = request.form.get('name', '').strip()
        if User.query.filter_by(email=email).first():
            return render_template('register.html', error='Email already exists')
        user = User(email=email, name=name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/dashboard')
@login_required
def dashboard():
    projects = Project.query.filter_by(user_id=current_user.id).order_by(Project.created_at.desc()).limit(20).all()
    return render_template('dashboard.html', projects=projects, user=current_user)


@app.route('/projects/new', methods=['GET', 'POST'])
@login_required
def new_project():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content_type = request.form.get('content_type', 'blog')
        original_content = request.form.get('original_content', '').strip()
        
        if not original_content:
            return render_template('new_project.html', error='Content is required')
        
        project = Project(
            user_id=current_user.id,
            title=title or f"Project {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            content_type=content_type,
            original_content=original_content,
            status='processing'
        )
        db.session.add(project)
        db.session.commit()
        
        # Generate repurposed content for all platforms
        platforms = ['twitter', 'linkedin', 'instagram', 'tiktok', 'newsletter', 'youtube_shorts', 'pinterest', 'reddit', 'threads', 'medium']
        
        for platform in platforms:
            try:
                result = generate_repurposed_content(original_content, platform, content_type)
                rc = RepurposedContent(
                    project_id=project.id,
                    platform=platform,
                    content_text=result,
                    status='completed'
                )
                db.session.add(rc)
            except Exception as e:
                print(f"Failed to generate {platform}: {e}")
        
        project.status = 'completed'
        db.session.commit()
        
        return redirect(url_for('project', project_id=project.id))
    
    return render_template('new_project.html')


@app.route('/projects/<int:project_id>')
@login_required
def project(project_id):
    project = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    contents = RepurposedContent.query.filter_by(project_id=project_id).all()
    return render_template('project.html', project=project, contents=contents)


@app.route('/api/generate', methods=['POST'])
@login_required
def api_generate():
    data = request.get_json()
    content = data.get('content', '')
    platform = data.get('platform', 'twitter')
    content_type = data.get('content_type', 'blog')
    
    if not content:
        return jsonify({'error': 'Content is required'}), 400
    
    result = generate_repurposed_content(content, platform, content_type)
    return jsonify({'content': result, 'platform': platform})


@app.route('/pricing')
def pricing():
    return render_template('pricing.html')


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES (inline for single-file deploy)
# ══════════════════════════════════════════════════════════════════════════════

import os as _os

# Create templates directory
_templates_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'templates')
if not _os.path.exists(_templates_dir):
    _os.makedirs(_templates_dir)

# Write template files
_templates = {
    'index.html': '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RepurposeAI Studio - Turn Content Into 10+ Formats</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
:root{--bg:#06060a;--surface:#0e0e14;--card:#12121a;--text:#f4f4f8;--muted:#8e8e9a;--accent:#c4a84d;--accent-glow:rgba(196,168,77,0.2);--green:#22c55e;--green-bg:rgba(34,197,94,0.1);--blue:#3b82f6;--blue-bg:rgba(59,130,246,0.1);--border:rgba(255,255,255,0.06);--border-hover:rgba(196,168,77,0.3);}
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.55;overflow-x:hidden;}
body::before{content:'';position:fixed;inset:0;background:linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);background-size:60px 60px;pointer-events:none;z-index:0;}
header{position:fixed;top:0;left:0;right:0;z-index:100;background:rgba(6,6,10,0.85);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);}
.header-inner{max-width:1400px;margin:0 auto;height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;text-decoration:none;color:var(--text);}
.logo-icon{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#a68b3a);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900;color:#0a0a0e;}
nav{display:flex;gap:28px;font-size:14px;font-weight:500;}
nav a{color:var(--muted);text-decoration:none;transition:color 0.2s;}
nav a:hover{color:var(--text);}
main{position:relative;z-index:1;}
.hero{padding:140px 24px 80px;text-align:center;max-width:900px;margin:0 auto;}
.hero h1{font-size:clamp(40px,6vw,64px);font-weight:900;line-height:1.05;letter-spacing:-0.03em;margin-bottom:20px;}
.hero h1 .accent{background:linear-gradient(135deg,var(--accent),#d4b85c);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.hero p{font-size:19px;color:var(--muted);max-width:600px;margin:0 auto 36px;}
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 22px;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;cursor:pointer;border:none;transition:all 0.2s;font-family:inherit;}
.btn-primary{background:linear-gradient(135deg,var(--accent),#a68b3a);color:#0a0a0e;font-weight:700;}
.btn-primary:hover{transform:translateY(-1px);box-shadow:0 4px 20px var(--accent-glow);}
.btn-outline{background:transparent;color:var(--text);border:1px solid var(--border);}
.btn-outline:hover{border-color:var(--border-hover);}
.btn-lg{padding:14px 32px;font-size:16px;}
.section{padding:80px 24px;max-width:1200px;margin:0 auto;}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:24px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px;transition:all 0.25s;}
.card:hover{border-color:var(--border-hover);transform:translateY(-2px);}
footer{border-top:1px solid var(--border);padding:48px 24px;text-align:center;color:var(--muted);font-size:13px;}
@media(max-width:768px){nav{display:none;}.grid{grid-template-columns:1fr;}}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <a href="/" class="logo"><div class="logo-icon">R</div> RepurposeAI Studio</a>
    <nav>
      <a href="#features">Features</a>
      <a href="/pricing">Pricing</a>
      <a href="/login">Login</a>
      <a href="/register" class="btn btn-primary">Get Started</a>
    </nav>
  </div>
</header>
<main>
  <section class="hero">
    <h1>Turn <span class="accent">One Piece</span> of Content Into 10+ Formats</h1>
    <p>AI-powered content repurposing for creators, marketers, and businesses. Go from blog post to tweets, shorts, newsletters, and more — in seconds.</p>
    <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">
      <a href="/register" class="btn btn-primary btn-lg">Start Free Trial</a>
      <a href="#features" class="btn btn-outline btn-lg">See How It Works</a>
    </div>
  </section>
  <section class="section" id="features">
    <div style="text-align:center;margin-bottom:56px;">
      <h2 style="font-size:36px;font-weight:800;margin-bottom:12px;">Everything You Need</h2>
      <p style="color:var(--muted);font-size:17px;">From one piece of content, generate platform-native posts for every major platform.</p>
    </div>
    <div class="grid">
      <div class="card"><div style="font-size:28px;margin-bottom:12px;">🐦</div><h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">Twitter/X Threads</h3><p style="color:var(--muted);font-size:14px;">Concise, engaging tweets with optimal hashtags and thread structure.</p></div>
      <div class="card"><div style="font-size:28px;margin-bottom:12px;">💼</div><h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">LinkedIn Posts</h3><p style="color:var(--muted);font-size:14px;">Professional thought leadership posts that build your brand.</p></div>
      <div class="card"><div style="font-size:28px;margin-bottom:12px;">📸</div><h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">Instagram Captions</h3><p style="color:var(--muted);font-size:14px;">Visual-first captions with strategic hashtags and CTAs.</p></div>
      <div class="card"><div style="font-size:28px;margin-bottom:12px;">🎵</div><h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">TikTok Scripts</h3><p style="color:var(--muted);font-size:14px;">Short-form video scripts optimized for viral potential.</p></div>
      <div class="card"><div style="font-size:28px;margin-bottom:12px;">📧</div><h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">Email Newsletters</h3><p style="color:var(--muted);font-size:14px;">Personal, story-driven emails that subscribers love to open.</p></div>
      <div class="card"><div style="font-size:28px;margin-bottom:12px;">📌</div><h3 style="font-size:18px;font-weight:700;margin-bottom:8px;">Pinterest Pins</h3><p style="color:var(--muted);font-size:14px;">Keyword-rich descriptions that drive traffic to your content.</p></div>
    </div>
  </section>
</main>
<footer>© 2026 RepurposeAI Studio — Built by NIMBDU. Maximum-effort, zero-waste.</footer>
</body>
</html>''',
    
    'login.html': '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login - RepurposeAI Studio</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
:root{--bg:#06060a;--surface:#0e0e14;--card:#12121a;--text:#f4f4f8;--muted:#8e8e9a;--accent:#c4a84d;--accent-glow:rgba(196,168,77,0.2);--border:rgba(255,255,255,0.06);--border-hover:rgba(196,168,77,0.3);}
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}
.form-container{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:40px;max-width:400px;width:100%;}
h1{font-size:24px;font-weight:800;margin-bottom:8px;text-align:center;}
.sub{color:var(--muted);text-align:center;margin-bottom:32px;font-size:14px;}
input{width:100%;padding:12px 16px;border-radius:10px;font-size:14px;background:var(--surface);border:1px solid var(--border);color:var(--text);margin-bottom:16px;font-family:inherit;}
input:focus{outline:none;border-color:var(--accent);}
input::placeholder{color:var(--muted);}
button{width:100%;padding:12px;border-radius:10px;font-size:14px;font-weight:600;background:linear-gradient(135deg,var(--accent),#a68b3a);color:#0a0a0e;border:none;cursor:pointer;font-family:inherit;}
button:hover{box-shadow:0 4px 16px var(--accent-glow);}
.error{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.2);color:#f87171;padding:12px;border-radius:10px;font-size:14px;margin-bottom:16px;}
.switch{text-align:center;margin-top:20px;font-size:14px;color:var(--muted);}
.switch a{color:var(--accent);text-decoration:none;font-weight:600;}
</style>
</head>
<body>
<div class="form-container">
  <h1>Welcome Back</h1>
  <p class="sub">Login to your RepurposeAI Studio account</p>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <input type="email" name="email" placeholder="Email address" required>
    <input type="password" name="password" placeholder="Password" required>
    <button type="submit">Login</button>
  </form>
  <p class="switch">Don't have an account? <a href="/register">Sign up</a></p>
</div>
</body>
</html>''',
    
    'register.html': '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sign Up - RepurposeAI Studio</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
:root{--bg:#06060a;--surface:#0e0e14;--card:#12121a;--text:#f4f4f8;--muted:#8e8e9a;--accent:#c4a84d;--accent-glow:rgba(196,168,77,0.2);--border:rgba(255,255,255,0.06);--border-hover:rgba(196,168,77,0.3);}
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;}
.form-container{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:40px;max-width:400px;width:100%;}
h1{font-size:24px;font-weight:800;margin-bottom:8px;text-align:center;}
.sub{color:var(--muted);text-align:center;margin-bottom:32px;font-size:14px;}
input{width:100%;padding:12px 16px;border-radius:10px;font-size:14px;background:var(--surface);border:1px solid var(--border);color:var(--text);margin-bottom:16px;font-family:inherit;}
input:focus{outline:none;border-color:var(--accent);}
input::placeholder{color:var(--muted);}
button{width:100%;padding:12px;border-radius:10px;font-size:14px;font-weight:600;background:linear-gradient(135deg,var(--accent),#a68b3a);color:#0a0a0e;border:none;cursor:pointer;font-family:inherit;}
button:hover{box-shadow:0 4px 16px var(--accent-glow);}
.error{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.2);color:#f87171;padding:12px;border-radius:10px;font-size:14px;margin-bottom:16px;}
.switch{text-align:center;margin-top:20px;font-size:14px;color:var(--muted);}
.switch a{color:var(--accent);text-decoration:none;font-weight:600;}
</style>
</head>
<body>
<div class="form-container">
  <h1>Create Account</h1>
  <p class="sub">Start repurposing content for free</p>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <input type="text" name="name" placeholder="Full name" required>
    <input type="email" name="email" placeholder="Email address" required>
    <input type="password" name="password" placeholder="Password" required>
    <button type="submit">Create Account</button>
  </form>
  <p class="switch">Already have an account? <a href="/login">Login</a></p>
</div>
</body>
</html>''',
    
    'dashboard.html': '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard - RepurposeAI Studio</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
:root{--bg:#06060a;--surface:#0e0e14;--card:#12121a;--text:#f4f4f8;--muted:#8e8e9a;--accent:#c4a84d;--accent-glow:rgba(196,168,77,0.2);--green:#22c55e;--green-bg:rgba(34,197,94,0.1);--border:rgba(255,255,255,0.06);--border-hover:rgba(196,168,77,0.3);}
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.55;}
.header{display:flex;align-items:center;justify-content:space-between;padding:16px 24px;border-bottom:1px solid var(--border);}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;text-decoration:none;color:var(--text);}
.logo-icon{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#a68b3a);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900;color:#0a0a0e;}
.container{max-width:1200px;margin:0 auto;padding:32px 24px;}
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 22px;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;cursor:pointer;border:none;transition:all 0.2s;font-family:inherit;}
.btn-primary{background:linear-gradient(135deg,var(--accent),#a68b3a);color:#0a0a0e;font-weight:700;}
.btn-primary:hover{transform:translateY(-1px);box-shadow:0 4px 20px var(--accent-glow);}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:24px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px;transition:all 0.25s;}
.card:hover{border-color:var(--border-hover);transform:translateY(-2px);}
.card h3{font-size:18px;font-weight:700;margin-bottom:8px;}
.card p{color:var(--muted);font-size:14px;margin-bottom:16px;}
.status{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:600;text-transform:uppercase;}
.status-completed{background:var(--green-bg);color:var(--green);}
.status-processing{background:rgba(234,179,8,0.1);color:#eab308;}
.status-pending{background:rgba(168,85,247,0.1);color:#a855f7;}
.empty{text-align:center;padding:60px 20px;color:var(--muted);}
.empty h2{font-size:24px;font-weight:700;color:var(--text);margin-bottom:12px;}
</style>
</head>
<body>
<div class="header">
  <a href="/" class="logo"><div class="logo-icon">R</div> RepurposeAI Studio</a>
  <div style="display:flex;gap:12px;align-items:center;">
    <span style="color:var(--muted);font-size:14px;">Credits: <strong>{{ user.credits }}</strong></span>
    <a href="/projects/new" class="btn btn-primary">+ New Project</a>
    <a href="/logout" class="btn btn-outline" style="background:transparent;color:var(--text);border:1px solid var(--border);">Logout</a>
  </div>
</div>
<div class="container">
  {% if projects %}
  <div class="grid">
    {% for project in projects %}
    <a href="/projects/{{ project.id }}" class="card" style="text-decoration:none;color:var(--text);">
      <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:12px;">
        <h3>{{ project.title }}</h3>
        <span class="status status-{{ project.status }}">{{ project.status }}</span>
      </div>
      <p>{{ project.content_type }} • {{ project.created_at.strftime('%b %d, %Y') }}</p>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">
    <h2>No projects yet</h2>
    <p style="margin-bottom:24px;">Create your first project to start repurposing content.</p>
    <a href="/projects/new" class="btn btn-primary btn-lg">Create First Project</a>
  </div>
  {% endif %}
</div>
</body>
</html>''',
    
    'new_project.html': '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>New Project - RepurposeAI Studio</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
:root{--bg:#06060a;--surface:#0e0e14;--card:#12121a;--text:#f4f4f8;--muted:#8e8e9a;--accent:#c4a84d;--accent-glow:rgba(196,168,77,0.2);--border:rgba(255,255,255,0.06);--border-hover:rgba(196,168,77,0.3);}
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.55;}
.header{display:flex;align-items:center;justify-content:space-between;padding:16px 24px;border-bottom:1px solid var(--border);}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;text-decoration:none;color:var(--text);}
.logo-icon{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#a68b3a);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900;color:#0a0a0e;}
.container{max-width:800px;margin:0 auto;padding:32px 24px;}
h1{font-size:28px;font-weight:800;margin-bottom:8px;}
.sub{color:var(--muted);margin-bottom:32px;}
.form-group{margin-bottom:24px;}
label{display:block;font-size:14px;font-weight:600;margin-bottom:8px;}
input,select,textarea{width:100%;padding:12px 16px;border-radius:10px;font-size:14px;background:var(--surface);border:1px solid var(--border);color:var(--text);font-family:inherit;}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent);}
textarea{min-height:200px;resize:vertical;}
select{appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%238e8e9a' d='M6 8L1 3h10z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 16px center;}
.btn{display:inline-flex;align-items:center;gap:8px;padding:12px 28px;border-radius:10px;font-size:15px;font-weight:600;text-decoration:none;cursor:pointer;border:none;transition:all 0.2s;font-family:inherit;}
.btn-primary{background:linear-gradient(135deg,var(--accent),#a68b3a);color:#0a0a0e;font-weight:700;}
.btn-primary:hover{transform:translateY(-1px);box-shadow:0 4px 20px var(--accent-glow);}
.error{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.2);color:#f87171;padding:12px;border-radius:10px;font-size:14px;margin-bottom:16px;}
</style>
</head>
<body>
<div class="header">
  <a href="/" class="logo"><div class="logo-icon">R</div> RepurposeAI Studio</a>
  <a href="/dashboard" style="color:var(--muted);text-decoration:none;font-size:14px;">← Back to Dashboard</a>
</div>
<div class="container">
  <h1>New Project</h1>
  <p class="sub">Paste your content below and AI will repurpose it for 10+ platforms.</p>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST">
    <div class="form-group">
      <label>Project Title (optional)</label>
      <input type="text" name="title" placeholder="e.g., Blog Post Repurposing">
    </div>
    <div class="form-group">
      <label>Content Type</label>
      <select name="content_type">
        <option value="blog">Blog Post / Article</option>
        <option value="video">Video / YouTube</option>
        <option value="podcast">Podcast / Audio</option>
        <option value="interview">Interview</option>
        <option value="other">Other</option>
      </select>
    </div>
    <div class="form-group">
      <label>Your Content</label>
      <textarea name="original_content" placeholder="Paste your blog post, video transcript, podcast notes, or any content here..." required></textarea>
    </div>
    <button type="submit" class="btn btn-primary">Generate 10+ Formats →</button>
  </form>
</div>
</body>
</html>''',
    
    'project.html': '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ project.title }} - RepurposeAI Studio</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
:root{--bg:#06060a;--surface:#0e0e14;--card:#12121a;--text:#f4f4f8;--muted:#8e8e9a;--accent:#c4a84d;--accent-glow:rgba(196,168,77,0.2);--green:#22c55e;--green-bg:rgba(34,197,94,0.1);--blue:#3b82f6;--blue-bg:rgba(59,130,246,0.1);--border:rgba(255,255,255,0.06);--border-hover:rgba(196,168,77,0.3);}
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.55;}
.header{display:flex;align-items:center;justify-content:space-between;padding:16px 24px;border-bottom:1px solid var(--border);}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;text-decoration:none;color:var(--text);}
.logo-icon{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#a68b3a);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900;color:#0a0a0e;}
.container{max-width:1200px;margin:0 auto;padding:32px 24px;}
.back{color:var(--muted);text-decoration:none;font-size:14px;display:inline-block;margin-bottom:24px;}
.back:hover{color:var(--text);}
.platform-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(350px,1fr));gap:24px;}
.platform-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px;}
.platform-header{display:flex;align-items:center;gap:12px;margin-bottom:16px;}
.platform-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700;}
.platform-name{font-size:16px;font-weight:700;}
.platform-content{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;font-size:14px;line-height:1.6;white-space:pre-wrap;word-break:break-word;}
.copy-btn{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:6px;font-size:12px;font-weight:500;background:rgba(255,255,255,0.05);border:1px solid var(--border);color:var(--muted);cursor:pointer;margin-top:12px;}
.copy-btn:hover{border-color:var(--accent);color:var(--accent);}
</style>
</head>
<body>
<div class="header">
  <a href="/" class="logo"><div class="logo-icon">R</div> RepurposeAI Studio</a>
  <a href="/dashboard" style="color:var(--muted);text-decoration:none;font-size:14px;">← Dashboard</a>
</div>
<div class="container">
  <a href="/dashboard" class="back">← Back to Dashboard</a>
  <h1 style="font-size:28px;font-weight:800;margin-bottom:8px;">{{ project.title }}</h1>
  <p style="color:var(--muted);margin-bottom:32px;">{{ project.content_type }} • {{ project.created_at.strftime('%b %d, %Y at %H:%M') }}</p>
  <div class="platform-grid">
    {% for content in contents %}
    <div class="platform-card">
      <div class="platform-header">
        <div class="platform-icon" style="background:{% if content.platform=='twitter' %}rgba(29,155,240,0.15);color:#1d9bf0{% elif content.platform=='linkedin' %}rgba(0,119,181,0.15);color:#0077b5{% elif content.platform=='instagram' %}rgba(225,48,108,0.15);color:#e1306c{% elif content.platform=='tiktok' %}rgba(0,0,0,0.3);color:#fff{% elif content.platform=='newsletter' %}rgba(196,168,77,0.15);color:var(--accent){% else %}rgba(168,85,247,0.15);color:#a855f7{% endif %};">
          {% if content.platform=='twitter' %}🐦{% elif content.platform=='linkedin' %}💼{% elif content.platform=='instagram' %}📸{% elif content.platform=='tiktok' %}🎵{% elif content.platform=='newsletter' %}📧{% elif content.platform=='youtube_shorts' %}📺{% elif content.platform=='pinterest' %}📌{% elif content.platform=='reddit' %}🤖{% elif content.platform=='threads' %}🧵{% elif content.platform=='medium' %}📝{% else %}📄{% endif %}
        </div>
        <div class="platform-name">{{ content.platform.replace('_', ' ').title() }}</div>
      </div>
      <div class="platform-content">{{ content.content_text }}</div>
      <button class="copy-btn" onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent)">📋 Copy</button>
    </div>
    {% endfor %}
  </div>
</div>
</body>
</html>''',
    
    'pricing.html': '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pricing - RepurposeAI Studio</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
:root{--bg:#06060a;--surface:#0e0e14;--card:#12121a;--text:#f4f4f8;--muted:#8e8e9a;--accent:#c4a84d;--accent-glow:rgba(196,168,77,0.2);--green:#22c55e;--green-bg:rgba(34,197,94,0.1);--border:rgba(255,255,255,0.06);--border-hover:rgba(196,168,77,0.3);}
*{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.55;overflow-x:hidden;}
body::before{content:'';position:fixed;inset:0;background:linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);background-size:60px 60px;pointer-events:none;z-index:0;}
header{position:fixed;top:0;left:0;right:0;z-index:100;background:rgba(6,6,10,0.85);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);}
.header-inner{max-width:1400px;margin:0 auto;height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px;text-decoration:none;color:var(--text);}
.logo-icon{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#a68b3a);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:900;color:#0a0a0e;}
nav{display:flex;gap:28px;font-size:14px;font-weight:500;}
nav a{color:var(--muted);text-decoration:none;transition:color 0.2s;}
nav a:hover{color:var(--text);}
main{position:relative;z-index:1;}
.hero{padding:140px 24px 80px;text-align:center;max-width:900px;margin:0 auto;}
.hero h1{font-size:clamp(40px,6vw,64px);font-weight:900;line-height:1.05;letter-spacing:-0.03em;margin-bottom:20px;}
.hero h1 .accent{background:linear-gradient(135deg,var(--accent),#d4b85c);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.pricing-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:24px;max-width:900px;margin:0 auto;padding:0 24px 80px;}
.pricing-card{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:32px;text-align:center;position:relative;transition:all 0.25s;}
.pricing-card.featured{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 20px 60px rgba(0,0,0,0.3);}
.pricing-card.featured::before{content:'POPULAR';position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:var(--accent);color:#0a0a0e;font-size:10px;font-weight:800;letter-spacing:0.1em;padding:4px 12px;border-radius:999px;}
.pricing-name{font-size:14px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:12px;}
.pricing-price{font-size:48px;font-weight:900;color:var(--text);margin-bottom:4px;}
.pricing-price span{font-size:16px;font-weight:500;color:var(--muted);}
.pricing-desc{font-size:14px;color:var(--muted);margin-bottom:24px;}
.pricing-features{list-style:none;margin-bottom:28px;text-align:left;}
.pricing-features li{padding:8px 0;font-size:14px;color:var(--text);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;}
.pricing-features li:last-child{border-bottom:none;}
.pricing-features li .check{color:var(--green);font-weight:700;}
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 22px;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;cursor:pointer;border:none;transition:all 0.2s;font-family:inherit;}
.btn-primary{background:linear-gradient(135deg,var(--accent),#a68b3a);color:#0a0a0e;font-weight:700;}
.btn-primary:hover{transform:translateY(-1px);box-shadow:0 4px 20px var(--accent-glow);}
.btn-outline{background:transparent;color:var(--text);border:1px solid var(--border);}
.btn-outline:hover{border-color:var(--border-hover);}
.btn-block{width:100%;justify-content:center;}
footer{border-top:1px solid var(--border);padding:48px 24px;text-align:center;color:var(--muted);font-size:13px;}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <a href="/" class="logo"><div class="logo-icon">R</div> RepurposeAI Studio</a>
    <nav>
      <a href="/#features">Features</a>
      <a href="/pricing">Pricing</a>
      <a href="/login">Login</a>
      <a href="/register" class="btn btn-primary">Get Started</a>
    </nav>
  </div>
</header>
<main>
  <section class="hero">
    <h1>Simple, <span class="accent">Transparent</span> Pricing</h1>
    <p style="color:var(--muted);font-size:19px;">Start free. Scale when you're ready.</p>
  </section>
  <div class="pricing-grid">
    <div class="pricing-card">
      <div class="pricing-name">Free</div>
      <div class="pricing-price">$0<span>/mo</span></div>
      <div class="pricing-desc">For trying things out</div>
      <ul class="pricing-features">
        <li><span class="check">✓</span> 5 projects/month</li>
        <li><span class="check">✓</span> 50 AI generations</li>
        <li><span class="check">✓</span> 5 platforms per project</li>
        <li><span class="check">✓</span> Community support</li>
      </ul>
      <a href="/register" class="btn btn-outline btn-block">Get Started</a>
    </div>
    <div class="pricing-card featured">
      <div class="pricing-name">Pro</div>
      <div class="pricing-price">$29<span>/mo</span></div>
      <div class="pricing-desc">For serious creators</div>
      <ul class="pricing-features">
        <li><span class="check">✓</span> Unlimited projects</li>
        <li><span class="check">✓</span> 500 AI generations/month</li>
        <li><span class="check">✓</span> All 10+ platforms</li>
        <li><span class="check">✓</span> Priority support</li>
        <li><span class="check">✓</span> Custom brand voice</li>
        <li><span class="check">✓</span> API access</li>
      </ul>
      <a href="/register" class="btn btn-primary btn-block">Start Free Trial</a>
    </div>
    <div class="pricing-card">
      <div class="pricing-name">Business</div>
      <div class="pricing-price">$79<span>/mo</span></div>
      <div class="pricing-desc">For teams & agencies</div>
      <ul class="pricing-features">
        <li><span class="check">✓</span> Everything in Pro</li>
        <li><span class="check">✓</span> 2000 generations/month</li>
        <li><span class="check">✓</span> Team collaboration</li>
        <li><span class="check">✓</span> White-label option</li>
        <li><span class="check">✓</span> Dedicated support</li>
        <li><span class="check">✓</span> Custom integrations</li>
      </ul>
      <a href="/register" class="btn btn-outline btn-block">Contact Sales</a>
    </div>
  </div>
</main>
<footer>© 2026 RepurposeAI Studio — Built by NIMBDU. Maximum-effort, zero-waste.</footer>
</body>
</html>'''
}

# Write templates
for filename, content in _templates.items():
    filepath = os.path.join(_templates_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Created template: {filename}")


# ══════════════════════════════════════════════════════════════════════════════
# INITIALIZATION
# ══════════════════════════════════════════════════════════════════════════════

with app.app_context():
    try:
        db.create_all()
        print("Database tables created/verified")
    except Exception as e:
        print(f"WARNING: Could not create tables: {e}")


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_ENV') != 'production')
