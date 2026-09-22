"""PAZME. WSGI app with existing leads/events schema preserved, optional profile and private admin export."""
import base64
import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('PAZME_DB', str(ROOT / 'local.sqlite3')))
ADMIN_CREDENTIALS = Path(os.environ.get('PAZME_ADMIN_CREDENTIALS', str(DB.parent / 'admin_credentials.json')))
CONSENT_VERSION = '2026-09-22-v4-puzzle-wordmark'
SOURCES = re.compile(r'^[a-z0-9_-]{1,32}$')
PHONES = re.compile(r'^7\d{10}$')
EMAILS = re.compile(r'^[^@\s<>]{1,64}@[^@\s<>]{1,185}\.[^@\s<>.]{2,}$')
ORIGINS = {'https://pazme.ru', 'https://www.pazme.ru', 'http://localhost:8765', 'http://127.0.0.1:8765'}
LOCK = Lock()
RECENT = defaultdict(deque)
MIME = {'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.svg':'image/svg+xml','.webp':'image/webp','.png':'image/png','.txt':'text/plain; charset=utf-8'}
FIELDS = {'name':80,'city':200,'business_name':160,'business_activity':160,'business_offer':600,'business_site':250}
EXPORT_HEADERS = ['Телефон','Электронная почта','Способ связи','Роль','Имя','Город / география','Название бизнеса','Направление бизнеса','Предложение первым клиентам','Сайт / соцсеть','Источник','Дата согласия UTC','Версия согласия','Дополнено UTC']


def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB), timeout=15)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=15000')
    db.execute('''CREATE TABLE IF NOT EXISTS events (
        date TEXT NOT NULL, source TEXT NOT NULL,
        views INTEGER NOT NULL DEFAULT 0, interested INTEGER NOT NULL DEFAULT 0,
        declined INTEGER NOT NULL DEFAULT 0, submissions INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(date,source))''')
    db.execute('''CREATE TABLE IF NOT EXISTS leads (
        phone TEXT PRIMARY KEY,
        role TEXT NOT NULL CHECK(role IN ('participant','business')),
        source TEXT NOT NULL, consent_at TEXT NOT NULL, consent_version TEXT NOT NULL)''')
    # Non-destructive migrations from the live PAZME v1 database; never drop leads or events.
    existing = {r[1] for r in db.execute('PRAGMA table_info(leads)')}
    for col, typ in [('email','TEXT'),('contact_type',"TEXT NOT NULL DEFAULT 'phone'"),('name','TEXT'),('city','TEXT'),('business_name','TEXT'),('business_activity','TEXT'),('business_offer','TEXT'),('business_site','TEXT'),('profile_at','TEXT'),('edit_hash','TEXT'),('edit_expires','TEXT')]:
        if col not in existing:
            db.execute(f'ALTER TABLE leads ADD COLUMN {col} {typ}')
    if 'profiles' not in {r[1] for r in db.execute('PRAGMA table_info(events)')}:
        db.execute('ALTER TABLE events ADD COLUMN profiles INTEGER NOT NULL DEFAULT 0')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS leads_unique_email ON leads(email) WHERE email IS NOT NULL AND email != \'\'')
    db.commit()
    return db


def respond(start, code, body=b'', content_type='text/plain; charset=utf-8', extra=None):
    reasons = {200:'OK',201:'Created',204:'No Content',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',413:'Content Too Large',415:'Unsupported Media Type',429:'Too Many Requests'}
    hdr = [('Content-Type',content_type),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY')]
    if extra:hdr.extend(extra)
    start(f'{code} {reasons[code]}', hdr)
    return [body]


def jres(start, code, obj):
    return respond(start,code,json.dumps(obj,ensure_ascii=False).encode('utf-8'),'application/json; charset=utf-8')


def source(value):
    return value if isinstance(value,str) and SOURCES.fullmatch(value) else 'site'


def bump(src, col):
    with connection() as db:
        db.execute(f'INSERT INTO events(date,source,{col}) VALUES(?,?,1) ON CONFLICT(date,source) DO UPDATE SET {col}={col}+1',(datetime.now(timezone.utc).strftime('%Y-%m-%d'),src))


def blocked(env, label='lead', limit=7, period=60):
    ip = env.get('HTTP_X_REAL_IP') or env.get('REMOTE_ADDR','unknown')
    now = time.monotonic()
    with LOCK:
        key=(label,ip)
        q=RECENT[key]
        while q and q[0] < now-period:q.popleft()
        if len(q)>=limit:return True
        q.append(now)
        if len(RECENT)>5000:
            for k in list(RECENT)[:1000]:
                if not RECENT[k] or RECENT[k][-1]<now-period:del RECENT[k]
    return False


def safe_text(data,key):
    value=data.get(key,'')
    if not isinstance(value,str):raise ValueError('Неверный формат поля.')
    value=value.strip()
    if len(value)>FIELDS[key] or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise ValueError('Проверьте длину текста.')
    return value


def admin_authorized(env):
    if env.get('HTTP_X_FORWARDED_PROTO')!='https':return False
    try:
        contents=json.loads(ADMIN_CREDENTIALS.read_text())
        header=env.get('HTTP_AUTHORIZATION','')
        if not header.startswith('Basic '):return False
        user, pwd=base64.b64decode(header[6:], validate=True).decode('utf-8').split(':',1)
        if not hmac.compare_digest(user,'admin'):return False
        digest=hashlib.pbkdf2_hmac('sha256',pwd.encode(),bytes.fromhex(contents['salt']),200000)
        return hmac.compare_digest(digest.hex(),contents['hash'])
    except (OSError,ValueError,UnicodeDecodeError,KeyError,TypeError):return False


def export_rows(db):
    return db.execute('''SELECT CASE WHEN contact_type='email' THEN '' ELSE phone END,
        COALESCE(email,''), COALESCE(contact_type,'phone'),role,
        COALESCE(name,''),COALESCE(city,''),COALESCE(business_name,''),
        COALESCE(business_activity,''),COALESCE(business_offer,''),
        COALESCE(business_site,''),source,consent_at,consent_version,
        COALESCE(profile_at,'')
        FROM leads ORDER BY consent_at DESC''').fetchall()


def safe_csv_row(row):
    # Prevent spreadsheet formula execution from applicant-submitted fields.
    return ["'"+str(value) if str(value).lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value for value in row]


def admin_page(db):
    rows=export_rows(db)
    stats=db.execute('SELECT COALESCE(SUM(views),0),COALESCE(SUM(interested),0),COALESCE(SUM(declined),0),COALESCE(SUM(submissions),0),COALESCE(SUM(profiles),0) FROM events').fetchone()
    p=sum(1 for r in rows if r[3]=='participant')
    b=sum(1 for r in rows if r[3]=='business')
    tr=''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in r)+'</tr>' for r in rows)
    heads=''.join('<th>'+html.escape(x)+'</th>' for x in EXPORT_HEADERS)
    body=f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>PAZME · Заявки</title><style>body{{font:15px system-ui,Arial;max-width:1250px;margin:30px auto;padding:0 18px;color:#20251d}}h1{{font-size:29px}}.top{{background:#f2fbe5;padding:22px;border-radius:18px;line-height:1.8}}a.btn{{display:inline-block;background:#c9f58c;color:#15200c;border-radius:12px;padding:12px 19px;text-decoration:none;font-weight:700;margin:22px 0}}.table{{overflow:auto;border:1px solid #ddd;border-radius:14px}}table{{border-collapse:collapse;white-space:nowrap}}th,td{{border-bottom:1px solid #eee;padding:12px;text-align:left}}th{{background:#f4f7ef}}</style></head><body><h1>PAZME · заявки и аналитика</h1><div class="top">Открытия: <b>{stats[0]}</b> · Да: <b>{stats[1]}</b> · Пока нет: <b>{stats[2]}</b><br>Отправки контактов: <b>{stats[3]}</b> · Анкеты: <b>{stats[4]}</b><br>Актуальные контакты: <b>{len(rows)}</b> · Участники: <b>{p}</b> · Бизнес: <b>{b}</b></div><a class="btn" href="/admin/export.csv">Скачать все заявки в CSV ↓</a><div class="table"><table><thead><tr>{heads}</tr></thead><tbody>{tr}</tbody></table></div><p>Доступ защищён паролем. Храните выгрузки только на своём устройстве и не загружайте в открытый GitHub.</p></body></html>'''
    return body.encode('utf-8')


def application(env, start):
    method=env.get('REQUEST_METHOD','GET')
    path=env.get('PATH_INFO','/')
    static={'/':'index.html','/qr/':'index.html','/participant/':'participant.html','/business/':'business.html','/privacy/':'privacy.html','/consent/':'consent.html','/assets/style.css':'assets/style.css','/assets/app.js':'assets/app.js','/assets/profile.js':'assets/profile.js','/assets/sticker.webp':'assets/sticker.webp','/assets/qr-site.png':'assets/qr-site.png','/assets/wordmark.png':'assets/wordmark.png','/assets/smile-partner.webp':'assets/smile-partner.webp','/assets/smile-business.webp':'assets/smile-business.webp','/assets/hero-city.webp':'assets/hero-city.webp','/favicon.svg':'favicon.svg','/robots.txt':'robots.txt'}
    if method=='GET' and path in static:
        file=ROOT/static[path]
        return respond(start,200,file.read_bytes(),MIME.get(file.suffix,'application/octet-stream'))
    if path in ('/admin/','/admin/export.csv'):
        if method!='GET':return respond(start,405)
        if blocked(env,'admin',limit=25,period=300):return respond(start,429,b'Try again later')
        if not admin_authorized(env):
            return respond(start,401,b'Login required',extra=[('WWW-Authenticate','Basic realm="PAZME private"'),('Referrer-Policy','no-referrer')])
        with connection() as db:
            if path=='/admin/':
                return respond(start,200,admin_page(db),'text/html; charset=utf-8',extra=[('X-Robots-Tag','noindex, nofollow'),('Referrer-Policy','no-referrer')])
            sio=io.StringIO(newline='')
            writer=csv.writer(sio)
            writer.writerow(EXPORT_HEADERS)
            writer.writerows(safe_csv_row(r) for r in export_rows(db))
            payload=b'\xef\xbb\xbf'+sio.getvalue().encode('utf-8')
            return respond(start,200,payload,'text/csv; charset=utf-8',extra=[('Content-Disposition','attachment; filename="PAZME-contacts.csv"'),('X-Robots-Tag','noindex, nofollow'),('Referrer-Policy','no-referrer')])
    if path not in ('/api/view','/api/choice','/api/lead','/api/profile'):return respond(start,404,b'Not found')
    if method!='POST':return respond(start,405,b'Method not allowed')
    origin=env.get('HTTP_ORIGIN')
    if origin and origin not in ORIGINS:return respond(start,403,b'Origin denied')
    # Require the browser-facing site to use HTTPS except localhost testing.
    if env.get('HTTP_X_FORWARDED_PROTO') not in ('https',None) and origin not in ('http://localhost:8765','http://127.0.0.1:8765'):
        return respond(start,403,b'HTTPS required')
    if env.get('CONTENT_TYPE','').split(';')[0].strip().lower()!='application/json':return respond(start,415,b'JSON expected')
    try:length=int(env.get('CONTENT_LENGTH','0'))
    except ValueError:return respond(start,400,b'Invalid size')
    if length<1 or length>4096:return respond(start,413,b'Invalid size')
    try:
        data=json.loads(env['wsgi.input'].read(length))
        if not isinstance(data,dict):raise ValueError('Object expected')
    except (ValueError,UnicodeDecodeError):return jres(start,400,{'ok':False})
    src=source(data.get('source'))
    if path=='/api/view':
        bump(src,'views');return jres(start,200,{'ok':True})
    if path=='/api/choice':
        if data.get('answer') not in ('yes','no'):return jres(start,400,{'ok':False})
        bump(src,'interested' if data['answer']=='yes' else 'declined');return jres(start,200,{'ok':True})
    if blocked(env):return jres(start,429,{'ok':False,'message':'Попробуйте чуть позже.'})
    if path=='/api/lead':
        if data.get('website'):return jres(start,200,{'ok':True})
        kind=data.get('contact_type','phone')
        digits=re.sub(r'\D','',str(data.get('phone','')))
        if len(digits)==11 and digits.startswith('8'):digits='7'+digits[1:]
        email=str(data.get('email','')).strip().lower()
        role=data.get('role')
        if kind not in ('phone','email') or role not in ('participant','business') or data.get('consent') is not True:
            return jres(start,400,{'ok':False,'message':'Проверьте роль, контакт и согласие.'})
        if kind=='phone':
            if not PHONES.fullmatch(digits):return jres(start,400,{'ok':False,'message':'Проверьте телефон.'})
            contact_key='+'+digits
            email=''
        else:
            if len(email)>254 or not EMAILS.fullmatch(email) or '..' in email or email.startswith('.'):
                return jres(start,400,{'ok':False,'message':'Проверьте адрес электронной почты.'})
            # Keep the live phone-PK schema intact; export contact_key only for phone rows.
            contact_key='email:'+hashlib.sha256(email.encode('utf-8')).hexdigest()
        import secrets
        token=secrets.token_urlsafe(32)
        edithash=hashlib.sha256(token.encode()).hexdigest()
        now=datetime.now(timezone.utc)
        at=now.strftime('%Y-%m-%d %H:%M:%S')
        expiry=(now+timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
        with connection() as db:
            # Existing user may return; preserve previously filled profile when choosing same role.
            db.execute('''INSERT INTO leads(phone,email,contact_type,role,source,consent_at,consent_version,edit_hash,edit_expires)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(phone) DO UPDATE SET
                email=excluded.email,contact_type=excluded.contact_type,
                role=excluded.role,source=excluded.source,consent_at=excluded.consent_at,
                consent_version=excluded.consent_version,edit_hash=excluded.edit_hash,edit_expires=excluded.edit_expires''',
                (contact_key,email,kind,role,src,at,CONSENT_VERSION,edithash,expiry))
            db.execute('''INSERT INTO events(date,source,submissions) VALUES(?,?,1)
                ON CONFLICT(date,source) DO UPDATE SET submissions=submissions+1''',(at[:10],src))
        return jres(start,201,{'ok':True,'edit_token':token})
    if path=='/api/profile':
        token=data.get('edit_token')
        if not isinstance(token,str) or len(token)<30 or len(token)>100:return jres(start,403,{'ok':False,'message':'Начните с заявки на главной странице.'})
        digest=hashlib.sha256(token.encode()).hexdigest()
        with connection() as db:
            row=db.execute('SELECT phone,role,source,edit_expires,profile_at FROM leads WHERE edit_hash=?',(digest,)).fetchone()
            if not row or row[1]!=data.get('role') or row[3]<datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'):
                return jres(start,403,{'ok':False,'message':'Для продолжения заполните короткую заявку ещё раз.'})
            try: cleaned={f:safe_text(data,f) for f in FIELDS}
            except ValueError as e:return jres(start,400,{'ok':False,'message':str(e)})
            if not cleaned['city'] or (row[1]=='business' and (not cleaned['business_name'] or not cleaned['business_activity'])):
                return jres(start,400,{'ok':False,'message':'Заполните основные поля анкеты.'})
            now=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            db.execute('''UPDATE leads SET name=?,city=?,business_name=?,business_activity=?,business_offer=?,business_site=?,profile_at=?,edit_hash=NULL,edit_expires=NULL WHERE phone=?''',
                (cleaned['name'],cleaned['city'],cleaned['business_name'] if row[1]=='business' else '',cleaned['business_activity'] if row[1]=='business' else '',cleaned['business_offer'] if row[1]=='business' else '',cleaned['business_site'] if row[1]=='business' else '',now,row[0]))
            db.execute('''INSERT INTO events(date,source,profiles) VALUES(?,?,1) ON CONFLICT(date,source) DO UPDATE SET profiles=profiles+1''',(now[:10],row[2]))
        return jres(start,200,{'ok':True})
    return respond(start,404,b'Not found')


if __name__=='__main__':
    from wsgiref.simple_server import make_server
    with make_server('127.0.0.1',8765,application) as server:
        print('PAZME: http://127.0.0.1:8765/qr/')
        server.serve_forever()
