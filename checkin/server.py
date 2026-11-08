"""CareFlow 居服員打卡網頁（Flask）。

    python -m checkin.server                 # http://本機:8600
    python -m checkin.server --https         # 自簽憑證（同一個 Wi-Fi 測試 GPS 用）

手機要拿 GPS，網址必須是 HTTPS（或 localhost）。外勤使用請搭配 Cloudflare Tunnel 等，見 checkin/README.md。
打卡時間一律用主機時間，不信任手機時間。
"""
from __future__ import annotations

import argparse
import os
import secrets
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

from checkin import store


def create_app(db_path=None, secure_cookie=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=store.secret_key(db_path),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=bool(secure_cookie if secure_cookie is not None else os.getenv('CAREFLOW_CHECKIN_SECURE')),
        PERMANENT_SESSION_LIFETIME=timedelta(days=365),  # 手機綁定後一年內打開就能打卡；督導解除綁定會立刻失效
        MAX_CONTENT_LENGTH=16 * 1024,
        CHECKIN_DB=db_path,
    )

    # 架在 Caddy／Cloudflare 後面時，依轉送標頭產生正確的 https 網址與子路徑
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    def db():
        return app.config['CHECKIN_DB']

    DEVICE_COOKIE = 'cf_device'

    def current_device():
        return store.device_for_token(request.cookies.get(DEVICE_COOKIE), db())

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            device = current_device()
            # 每次都核對這支手機的綁定狀態：督導解除綁定或核准了別支手機，這支就要重新登入
            if not session.get('cg') or not device or device['status'] != store.DEVICE_ACTIVE \
                    or device['caregiver_id'] != session.get('cg'):
                session.pop('cg', None)
                if request.path.endswith('/punch'):
                    return jsonify(ok=False, message='這支手機已不能打卡，請重新登入。'), 401
                return redirect(url_for('login'))
            return view(*args, **kwargs)
        return wrapped

    def remember_device(resp, token):
        resp.set_cookie(DEVICE_COOKIE, token, max_age=400 * 24 * 3600, httponly=True, samesite='Lax',
                        secure=app.config['SESSION_COOKIE_SECURE'])
        return resp

    def csrf_token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(24)
        return session['csrf']

    def check_csrf(token):
        if not token or not secrets.compare_digest(str(token), session.get('csrf', '')):
            abort(400, '頁面已過期，請重新整理。')

    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.after_request
    def no_cache(resp):
        resp.headers['Cache-Control'] = 'no-store'
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Referrer-Policy'] = 'no-referrer'
        resp.headers['X-Frame-Options'] = 'DENY'
        return resp

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """只輸入員工編號。第一支手機自動綁定；換手機要督導在 CareFlow 04 頁核准。"""
        device = current_device()
        if request.method == 'GET' and device and device['status'] == store.DEVICE_ACTIVE:
            session.permanent = True
            session['cg'] = device['caregiver_id']
            return redirect(url_for('today'))
        if request.method == 'GET' and device and device['status'] == store.DEVICE_PENDING:
            return redirect(url_for('pending'))
        error = ''
        if request.method == 'POST':
            check_csrf(request.form.get('csrf'))
            cg = request.form.get('cg', '').strip().upper()
            if not store.known_caregiver(cg, db()):
                error = '查不到這個員工編號，或督導還沒發布你的班表。'
            elif device and device['caregiver_id'] == cg and device['status'] == store.DEVICE_ACTIVE:
                session.permanent = True
                session['cg'] = cg
                return redirect(url_for('today'))
            else:
                token, status = store.request_device(cg, request.headers.get('User-Agent', ''), db())
                session.pop('cg', None)
                if status == store.DEVICE_ACTIVE:
                    session.permanent = True
                    session['cg'] = cg
                    session['flash'] = '這支手機已綁定，以後打開就能直接打卡。'
                    return remember_device(redirect(url_for('today')), token)
                return remember_device(redirect(url_for('pending')), token)
        return render_template('login.html', error=error)

    @app.get('/pending')
    def pending():
        device = current_device()
        if not device:
            return redirect(url_for('login'))
        if device['status'] == store.DEVICE_ACTIVE:
            session.permanent = True
            session['cg'] = device['caregiver_id']
            return redirect(url_for('today'))
        return render_template('pending.html', device=device, rejected=device['status'] != store.DEVICE_PENDING)

    @app.get('/device-status')
    def device_status():
        device = current_device()
        return jsonify(status=device['status'] if device else 'none')

    @app.post('/logout')
    def logout():
        """登出只清這次的登入；手機綁定還在，下次輸入同一個員編就直接進來。"""
        check_csrf(request.form.get('csrf'))
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    @login_required
    def today():
        cg = session['cg']
        day = store.now().date()
        punches = store.punches_for(cg, day, db())
        shifts = []
        for s in store.shifts_for(cg, day, db()):
            mine = [p for p in punches if p['task_id'] == s['task_id']]
            s['state'] = store.shift_state(mine)
            s['punches'] = {p['kind']: p for p in mine}
            shifts.append(s)
        weekday = '一二三四五六日'[day.weekday()]
        return render_template('today.html', cg=cg, day=day, weekday=weekday, shifts=shifts, flash=session.pop('flash', ''))

    @app.post('/punch')
    @login_required
    def punch():
        data = request.get_json(silent=True) or {}
        check_csrf(request.headers.get('X-CSRF-Token'))
        ok, msg = store.add_punch(session['cg'], str(data.get('task_id', '')), str(data.get('kind', '')),
                                  lat=data.get('lat'), lon=data.get('lon'), accuracy=data.get('accuracy'),
                                  at=data.get('at') or None, note=data.get('note', ''), path=db())
        return jsonify(ok=ok, message=msg), (200 if ok else 409)

    @app.get('/healthz')
    def healthz():
        return 'ok'

    @app.errorhandler(400)
    def bad_request(err):
        if request.path == '/punch':
            return jsonify(ok=False, message=err.description), 400
        return redirect(url_for('login'))

    return app


def main():
    parser = argparse.ArgumentParser(description='CareFlow 居服員打卡網頁')
    parser.add_argument('--host', default=os.getenv('CAREFLOW_CHECKIN_HOST', '0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.getenv('CAREFLOW_CHECKIN_PORT', '8600')))
    parser.add_argument('--https', action='store_true', help='用自簽憑證啟動 HTTPS（同一 Wi-Fi 測試 GPS 用，手機會出現警告）')
    args = parser.parse_args()
    app = create_app(secure_cookie=args.https or None)
    print(f'CareFlow 打卡網頁：{"https" if args.https else "http"}://<這台電腦的 IP>:{args.port}  （Ctrl+C 結束）')
    if args.https:
        app.run(host=args.host, port=args.port, ssl_context='adhoc')
        return
    try:
        from waitress import serve
    except ImportError:
        app.run(host=args.host, port=args.port)
    else:
        serve(app, host=args.host, port=args.port, threads=8)


if __name__ == '__main__':
    main()
