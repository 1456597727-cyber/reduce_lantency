"""
低延迟直播观看服务 - Web 版
卖服务不卖软件：用户访问网站，在线播放，拿不到代码
"""
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
import sqlite3, os, requests, secrets, string, datetime, sys
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# 部署配置 - Render 需要 0.0.0.0
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', 5001))
DB = os.path.join(os.path.dirname(__file__), 'service.db')

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            streams_used INTEGER DEFAULT 0,
            expires_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS act_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            days INTEGER DEFAULT 0,
            used_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    # Upgrade existing tables
    try: conn.execute("ALTER TABLE users ADD COLUMN expires_at TEXT")
    except: pass
    try: conn.execute("ALTER TABLE act_codes ADD COLUMN days INTEGER DEFAULT 0")
    except: pass
    # Admin
    if not conn.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        conn.execute("INSERT INTO users (username,password,code) VALUES ('admin','admin123','ADMIN')")
        conn.execute("INSERT INTO act_codes (code,days) VALUES ('ADMIN',0)")
    conn.commit()
    conn.close()

# === Login required ===
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('username') != 'admin':
            flash('需要管理员权限')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# === Auth ===
@app.route('/')
def login():
    if request.method == 'GET':
        return render_template('login.html')

@app.route('/login', methods=['POST'])
def do_login():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=? AND password=?",
                      (request.form['username'], request.form['password'])).fetchone()
    db.close()
    if user:
        # Check expiry
        if user['expires_at']:
            from datetime import datetime
            if datetime.now() > datetime.fromisoformat(user['expires_at']):
                flash('账号已过期，请联系购买新的激活码')
                return redirect(url_for('login'))
        session['user_id'] = user['id']
        session['username'] = user['username']
        return redirect(url_for('dashboard'))
    flash('用户名或密码错误')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')
    db = get_db()
    code = request.form['code']
    username = request.form['username']
    password = request.form['password']
    # Check activation code
    act = db.execute("SELECT * FROM act_codes WHERE code=? AND used_by IS NULL", (code,)).fetchone()
    if not act:
        db.close()
        flash('激活码无效或已被使用')
        return render_template('register.html')
    # Calculate expiry
    days = act['days'] or 0
    expires_at = None
    if days > 0:
        from datetime import datetime, timedelta
        expires_at = (datetime.now() + timedelta(days=days)).isoformat()
    # Check username
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        db.close()
        flash('用户名已存在')
        return render_template('register.html')
    # Create user
    db.execute("INSERT INTO users (username,password,code,expires_at) VALUES (?,?,?,?)",
               (username, password, code, expires_at))
    db.execute("UPDATE act_codes SET used_by=? WHERE code=?", (username, code))
    db.commit()
    db.close()
    flash('注册成功！请登录')
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# === Dashboard ===
@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    # Calculate remaining days
    days_left = None
    if user['expires_at']:
        from datetime import datetime
        delta = datetime.fromisoformat(user['expires_at']) - datetime.now()
        days_left = max(0, delta.days)
    db.close()
    return render_template('dashboard.html', user=user, days_left=days_left)

# === FLV Stream Proxy ===
# 核心：服务器代理 FLV 流，浏览器通过 /play 访问
@app.route('/play')
@login_required
def play():
    url = request.args.get('url', '')
    if not url:
        return 'Missing URL', 400
    # Record usage
    db = get_db()
    db.execute("UPDATE users SET streams_used = streams_used + 1 WHERE id=?",
               (session['user_id'],))
    db.commit()
    db.close()
    return render_template('player.html', stream_url=url)

# === Stream Proxy (解决浏览器跨域) ===
@app.route('/proxy')
@login_required
def proxy_stream():
    target_url = request.args.get('url', '')
    if not target_url:
        return 'Missing URL', 400

    def generate():
        try:
            resp = requests.get(target_url, stream=True, timeout=30,
                              headers={'User-Agent': 'Mozilla/5.0'})
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        except Exception:
            pass

    return Response(
        generate(),
        mimetype='video/x-flv',
        headers={
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-cache',
        }
    )

# === Admin Panel ===
@app.route('/admin')
@login_required
@admin_required
def admin():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    codes = db.execute("SELECT * FROM act_codes WHERE used_by IS NULL ORDER BY created_at DESC").fetchall()
    used_codes = db.execute("SELECT * FROM act_codes WHERE used_by IS NOT NULL ORDER BY created_at DESC").fetchall()
    db.close()
    return render_template('admin.html', users=users, codes=codes, used_codes=used_codes)

@app.route('/admin/gen_codes', methods=['POST'])
@login_required
@admin_required
def gen_codes():
    count = int(request.form.get('count', 1))
    prefix = request.form.get('prefix', 'VIP')
    days = int(request.form.get('days', 0))
    db = get_db()
    new_codes = []
    for _ in range(count):
        code = f"{prefix}-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))}"
        db.execute("INSERT INTO act_codes (code,days) VALUES (?,?)", (code, days))
        new_codes.append(code)
    db.commit()
    db.close()
    flash(f'已生成 {count} 个激活码（{days}天有效期）' if days > 0 else f'已生成 {count} 个永久激活码')
    return redirect(url_for('admin'))

@app.route('/admin/delete_user/<int:uid>')
@login_required
@admin_required
def delete_user(uid):
    db = get_db()
    db.execute("DELETE FROM users WHERE id=? AND username!='admin'", (uid,))
    db.commit()
    db.close()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    init_db()
    print(f'=================================')
    print(f'直播观看服务已启动')
    print(f'http://{HOST}:{PORT}')
    print(f'管理后台: /admin (admin/admin123)')
    print(f'=================================')

    # 生产模式用 waitress，开发模式用 Flask 自带
    try:
        from waitress import serve
        print('使用 Waitress 生产服务器')
        serve(app, host=HOST, port=PORT, threads=8)
    except ImportError:
        print('使用 Flask 开发服务器（仅本地测试）')
        app.run(host=HOST, port=PORT, debug=True)
