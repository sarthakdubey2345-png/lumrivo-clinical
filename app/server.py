"""Lumrivo Clinical: portable demonstration/pilot foundation, WSGI + SQLite."""
import base64
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from wsgiref.simple_server import make_server
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer
from . import ai

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', ROOT.parent / 'data'))
DB = DATA / 'clinical.sqlite3'
DEMO = os.environ.get('DEMO_MODE', 'true').lower() == 'true'
PRODUCTION = os.environ.get('PRODUCTION', 'false').lower() == 'true'
SECRET = os.environ.get('APP_SECRET', 'local-development-only-change-before-hosting')
MAX_BODY = 20 * 1024 * 1024
SESSION_TTL = 8 * 60 * 60
LIMIT_LOCK = threading.Lock()
LOGIN_ATTEMPTS = {}

class Error(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def uid(prefix):
    return prefix + '_' + secrets.token_hex(10)

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 210000).hex()
    return salt + ':' + digest

def session_hash(token):
    return hmac.new(SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()

def password_valid(password, saved):
    salt = saved.split(':')[0]
    return hmac.compare_digest(password_hash(password, salt), saved)

def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=20000')
    return c

def blank():
    return {**{f: '' for f in ai.TEXT_FIELDS}, 'allergy_status': 'not_assessed', 'medicines': [], 'uncertainties': []}

def audit(c, user, action, target, detail=''):
    c.execute('INSERT INTO audit(org,user,action,target,detail,created) VALUES(?,?,?,?,?,?)',
              (user['org'], user['id'], action, target, detail, now()))

def initialise():
    if PRODUCTION and (len(SECRET) < 32 or SECRET.startswith(('local-', 'REPLACE_'))):
        raise RuntimeError('Set a random APP_SECRET of at least 32 characters before hosting.')
    if not DEMO:
        raise RuntimeError('This release is demonstration-only. Real-patient mode is not enabled or clinically validated.')
    demo_password = os.environ.get('DEMO_PASSWORD', 'LumrivoDemo!2026')
    if PRODUCTION and (len(demo_password) < 16 or demo_password == 'LumrivoDemo!2026' or demo_password.startswith('REPLACE_')):
        raise RuntimeError('Set a unique DEMO_PASSWORD of at least 16 characters before hosting.')
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    c = connect()
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS organisations(id TEXT PRIMARY KEY,name TEXT NOT NULL,address TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,org TEXT NOT NULL REFERENCES organisations(id),name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,specialty TEXT NOT NULL DEFAULT '',registration TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user TEXT NOT NULL REFERENCES users(id),csrf TEXT NOT NULL,expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY,org TEXT NOT NULL REFERENCES organisations(id),mrn TEXT NOT NULL,name TEXT NOT NULL,age INTEGER NOT NULL,sex TEXT NOT NULL,phone TEXT NOT NULL DEFAULT '',created TEXT NOT NULL,UNIQUE(org,mrn));
    CREATE TABLE IF NOT EXISTS encounters(id TEXT PRIMARY KEY,org TEXT NOT NULL REFERENCES organisations(id),patient TEXT NOT NULL REFERENCES patients(id),doctor TEXT NOT NULL REFERENCES users(id),token INTEGER NOT NULL,day TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'waiting',draft TEXT NOT NULL,transcript TEXT NOT NULL DEFAULT '',consent TEXT NOT NULL DEFAULT 'pending',version INTEGER NOT NULL DEFAULT 1,reason TEXT NOT NULL DEFAULT '',created TEXT NOT NULL,updated TEXT NOT NULL,UNIQUE(org,doctor,day,token));
    CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,org TEXT NOT NULL,encounter TEXT NOT NULL REFERENCES encounters(id),snapshot TEXT NOT NULL,created TEXT NOT NULL,author TEXT NOT NULL REFERENCES users(id),supersedes TEXT,version INTEGER NOT NULL,UNIQUE(encounter,version));
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,org TEXT NOT NULL,user TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,detail TEXT NOT NULL,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS catalogue(id TEXT PRIMARY KEY,org TEXT NOT NULL,name TEXT NOT NULL,strength TEXT NOT NULL,form TEXT NOT NULL,source TEXT NOT NULL);
    ''')
    if not c.execute('SELECT 1 FROM organisations').fetchone():
        c.execute('INSERT INTO organisations VALUES(?,?,?,?)', ('hospital_demo', 'Lumrivo Demo Hospital', 'Sector 18, Noida · Fictional demonstration', '+91 00000 00000'))
        accounts = [
            ('dr_meera', 'Dr Meera Kapoor', 'meera@demo.lumrivo.com', 'doctor', 'General Medicine', 'DEMO-GM-001'),
            ('dr_arjun', 'Dr Arjun Rao', 'arjun@demo.lumrivo.com', 'doctor', 'Orthopaedics', 'DEMO-OR-002'),
            ('dr_sana', 'Dr Sana Khan', 'sana@demo.lumrivo.com', 'doctor', 'Dermatology', 'DEMO-DE-003'),
            ('reception', 'Ananya Singh', 'reception@demo.lumrivo.com', 'reception', 'Front desk', ''),
            ('admin', 'Hospital Administrator', 'admin@demo.lumrivo.com', 'admin', 'Administration', '')]
        for id_, name, email, role, specialty, registration in accounts:
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)', (id_, 'hospital_demo', name, email, password_hash(demo_password), role, specialty, registration))
        people = [('p_aanya', 'LH-1001', 'Aanya Sharma', 32, 'Female', 'dr_meera'),
                  ('p_rohan', 'LH-1002', 'Rohan Malhotra', 46, 'Male', 'dr_arjun'),
                  ('p_kavya', 'LH-1003', 'Kavya Verma', 28, 'Female', 'dr_sana'),
                  ('p_dev', 'LH-1004', 'Dev Patel', 54, 'Male', 'dr_meera')]
        for i, (pid, mrn, name, age, sex, doctor) in enumerate(people):
            c.execute('INSERT INTO patients VALUES(?,?,?,?,?,?,?,?)', (pid, 'hospital_demo', mrn, name, age, sex, '', now()))
            doc = blank()
            doc['complaints'] = ['Follow-up consultation', 'Knee discomfort reported', 'Hair concerns reported', 'Review of previous advice'][i]
            c.execute('INSERT INTO encounters(id,org,patient,doctor,token,day,draft,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                ('e_' + pid, 'hospital_demo', pid, doctor, 21+i, now()[:10], json.dumps(doc), now(), now()))
        # Prior approved fictional visit demonstrates longitudinal history without copying the uploaded prescription.
        old = blank()
        old.update(complaints='Fictional follow-up demonstration', history='Previous visit recorded for continuity.', allergy_status='not_assessed', instructions='Bring previous records to the next consultation.', followup='Review at the next scheduled visit.')
        oldtime = '2026-10-07T05:00:00+00:00'
        c.execute('INSERT INTO encounters(id,org,patient,doctor,token,day,status,draft,consent,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('e_previous', 'hospital_demo', 'p_aanya', 'dr_meera', 1, '2026-10-07', 'approved', json.dumps(old), 'manual', oldtime, oldtime))
        snapshot = {'draft': old, 'patient': {'id':'p_aanya','name':'Aanya Sharma','mrn':'LH-1001','age':32,'sex':'Female'},
                    'doctor': {'name':'Dr Meera Kapoor','specialty':'General Medicine','registration':'DEMO-GM-001'},
                    'organisation': {'name':'Lumrivo Demo Hospital','address':'Fictional demonstration','phone':''}, 'token':1, 'reason':'Initial fictional record', 'approved_at':oldtime}
        c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc_previous','hospital_demo','e_previous',json.dumps(snapshot),oldtime,'dr_meera',None,1))
        for id_, name, strength, form in [('demo_a','Demo Medicine A','10 demo units','Fictional tablet'),('demo_b','Demo Medicine B','5 demo units','Fictional tablet'),('demo_c','Demo Topical C','1 demo unit','Fictional topical')]:
            c.execute('INSERT INTO catalogue VALUES(?,?,?,?,?,?)',(id_,'hospital_demo',name,strength,form,'Synthetic catalogue — not for clinical use'))
    c.commit()
    os.chmod(DB, 0o600)
    c.close()

def public_user(u):
    return {k: u[k] for k in ('id','org','name','email','role','specialty','registration')}

def require_role(u, *roles):
    if u['role'] not in roles:
        raise Error('Your role does not have access to this action.', 403)

def encounter(c, u, id_, clinical=True):
    e = c.execute('SELECT * FROM encounters WHERE id=? AND org=?', (id_,u['org'])).fetchone()
    if not e:
        raise Error('Encounter not found.',404)
    if clinical and (u['role'] != 'doctor' or e['doctor'] != u['id']):
        raise Error('Only the assigned doctor can access or edit this consultation.',403)
    return e

def check_version(e, body):
    if type(body.get('version')) is not int or body['version'] != e['version']:
        raise Error('This record has changed. Reload it before continuing; your action was not applied.',409)

def validate_draft(d):
    if not isinstance(d,dict): raise Error('Invalid draft.')
    out = blank()
    for key in ai.TEXT_FIELDS:
        if not isinstance(d.get(key,''),str) or len(d.get(key,'')) > 12000:
            raise Error('Invalid or overlong field: ' + key)
        out[key] = d.get(key,'').strip()
    if out['allergy_status'] not in ('not_assessed','none_reported','reported','uncertain'):
        raise Error('Choose a valid allergy assessment status.')
    if not isinstance(d.get('medicines',[]),list) or len(d.get('medicines',[]))>40:
        raise Error('Invalid medicine list.')
    seen = set()
    for med in d.get('medicines',[]):
        if not isinstance(med,dict): raise Error('Invalid medicine row.')
        row = {}
        for key in ai.MED_FIELDS + ['confirmed']:
            if key=='confirmed':
                row[key] = med.get(key) is True
            else:
                if not isinstance(med.get(key,''),str) or len(med.get(key,''))>2000: raise Error('Invalid medicine detail.')
                row[key] = med.get(key,'').strip()
        row['id'] = row['id'] or uid('med')
        if row['id'] in seen: raise Error('Duplicate medicine row identifier.')
        seen.add(row['id'])
        out['medicines'].append(row)
    uncertainties = d.get('uncertainties',[])
    if not isinstance(uncertainties,list) or len(uncertainties)>60 or any(not isinstance(x,str) or len(x)>2000 for x in uncertainties):
        raise Error('Invalid review items.')
    out['uncertainties'] = uncertainties
    return out

def approval_errors(d):
    errors=[]
    if not d['complaints']: errors.append('Presenting complaint is missing.')
    if not d['assessment']: errors.append('Clinician assessment is missing.')
    if d['allergy_status'] in ('not_assessed','uncertain'): errors.append('Review the allergy status before issuing.')
    if d['allergy_status']=='reported' and not d['allergies']: errors.append('Document the reported allergy/reaction.')
    if d['allergy_status']=='none_reported' and d['allergies']: errors.append('Resolve the conflict between allergy status and reaction details.')
    if d['uncertainties']: errors.append('Resolve or explicitly remove each review item after checking its source.')
    for i,m in enumerate(d['medicines']):
        if any(not m[k] for k in ['name','strength','dose','route','frequency','duration']): errors.append(f'Medicine {i+1}: complete name, strength, dose, route, frequency and duration (or specify clinician-approved not-applicable text).')
        if not m['confirmed']: errors.append(f'Medicine {i+1}: confirm the medicine and its instructions.')
    return errors

def json_body(env):
    size=int(env.get('CONTENT_LENGTH') or 0)
    if size>MAX_BODY: raise Error('Request is too large.',413)
    try:
        data=json.loads(env['wsgi.input'].read(size) or b'{}')
    except (ValueError,UnicodeDecodeError): raise Error('Invalid JSON request.')
    if not isinstance(data,dict): raise Error('Expected a JSON object.')
    return data

def login(c, body, ip):
    email=str(body.get('email','')).strip().lower()[:254]
    key=(ip,email)
    with LIMIT_LOCK:
        cutoff=time.time()-900
        for k in list(LOGIN_ATTEMPTS):
            LOGIN_ATTEMPTS[k]=[t for t in LOGIN_ATTEMPTS[k] if t>cutoff]
            if not LOGIN_ATTEMPTS[k]: del LOGIN_ATTEMPTS[k]
        if len(LOGIN_ATTEMPTS.get(key,[]))>=8: raise Error('Too many sign-in attempts. Please try again in 15 minutes.',429)
        LOGIN_ATTEMPTS.setdefault(key,[]).append(time.time())
    u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    candidate=str(body.get('password',''))[:1024]
    # Same derivation cost for unknown users.
    valid=password_valid(candidate,u['password'] if u else '0'*32+':'+'0'*64)
    if not u or not valid: raise Error('Email or password is incorrect.',401)
    token=secrets.token_urlsafe(36)
    csrf=secrets.token_urlsafe(24)
    c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
    c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(session_hash(token),u['id'],csrf,time.time()+SESSION_TTL))
    audit(c,u,'sign_in',u['id'])
    c.commit()
    with LIMIT_LOCK: LOGIN_ATTEMPTS.pop(key,None)
    return {'user':public_user(u),'csrf':csrf}, [('Set-Cookie',f'lumrivo_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}' + ('; Secure' if PRODUCTION else ''))]

def get_session(c,env):
    cookie=SimpleCookie()
    try: cookie.load(env.get('HTTP_COOKIE',''))
    except Exception: return None,None
    token=cookie.get('lumrivo_session')
    if not token: return None,None
    s=c.execute('SELECT * FROM sessions WHERE token=? AND expires>?',(session_hash(token.value),time.time())).fetchone()
    if not s:return None,None
    return c.execute('SELECT * FROM users WHERE id=?',(s['user'],)).fetchone(),s

def bootstrap(c,u,csrf):
    org=dict(c.execute('SELECT * FROM organisations WHERE id=?',(u['org'],)).fetchone())
    doctors=[public_user(x) for x in c.execute("SELECT * FROM users WHERE org=? AND role='doctor' ORDER BY name",(u['org'],))]
    where='e.org=?'; args=[u['org']]
    if u['role']=='doctor': where+=' AND e.doctor=?';args.append(u['id'])
    rows=c.execute('SELECT e.*,p.name,p.mrn,p.age,p.sex,p.phone,u.name doctor_name,u.specialty FROM encounters e JOIN patients p ON p.id=e.patient JOIN users u ON u.id=e.doctor WHERE '+where+' ORDER BY e.created DESC',args).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        if u['role']!='doctor':
            for key in ('draft','transcript','reason','consent'): item.pop(key,None)
        else:item['draft']=json.loads(item['draft'])
        result.append(item)
    patients=[dict(x) for x in c.execute('SELECT p.* FROM patients p WHERE p.org=?'+(' AND EXISTS(SELECT 1 FROM encounters e WHERE e.patient=p.id AND e.doctor=?)' if u['role']=='doctor' else '')+' ORDER BY p.name', [u['org']]+([u['id']] if u['role']=='doctor' else []))]
    output={'user':public_user(u),'csrf':csrf,'organisation':org,'doctors':doctors,'patients':patients,'encounters':result,'demo':DEMO,'ai_configured':ai.configured(),
            'catalogue':[dict(x) for x in c.execute('SELECT * FROM catalogue WHERE org=?',(u['org'],))] if u['role']=='doctor' else []}
    if u['role']=='admin':
        output['users']=[public_user(x) for x in c.execute('SELECT * FROM users WHERE org=?',(u['org'],))]
        output['audit']=[dict(x) for x in c.execute('SELECT a.*,u.name actor FROM audit a LEFT JOIN users u ON u.id=a.user WHERE a.org=? ORDER BY a.id DESC LIMIT 100',(u['org'],))]
    return output

def route(c,env,u,s,path,method,body):
    if path=='/api/bootstrap' and method=='GET': return bootstrap(c,u,s['csrf'])
    if path=='/api/logout' and method=='POST':
        c.execute('DELETE FROM sessions WHERE token=?',(s['token'],));c.commit();return {'ok':True}
    if path=='/api/password' and method=='POST':
        if not password_valid(str(body.get('old','')),u['password']):raise Error('Current password is incorrect.')
        new=str(body.get('new',''))
        if len(new)<16 or len(new)>1024:raise Error('Use a password between 16 and 1024 characters.')
        c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(new),u['id']))
        c.execute('DELETE FROM sessions WHERE user=? AND token<>?',(u['id'],s['token']))
        audit(c,u,'password_changed',u['id']);c.commit();return {'ok':True}
    if path=='/api/organisation' and method=='POST':
        require_role(u,'admin')
        name=str(body.get('name','')).strip()[:180]
        if not name:raise Error('Hospital name is required.')
        c.execute('UPDATE organisations SET name=?,address=?,phone=? WHERE id=?',(name,str(body.get('address',''))[:500],str(body.get('phone',''))[:40],u['org']))
        audit(c,u,'organisation_updated',u['org']);c.commit();return {'ok':True}
    if path=='/api/users' and method=='POST':
        require_role(u,'admin')
        role=body.get('role')
        if role not in ('doctor','reception','admin'):raise Error('Invalid role.')
        name=str(body.get('name','')).strip()[:120]; email=str(body.get('email','')).strip().lower()[:254]; pw=str(body.get('password',''))
        if not name or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(pw)<16:raise Error('Name, valid email and password of at least 16 characters are required.')
        id_=uid('user')
        try:c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)',(id_,u['org'],name,email,password_hash(pw),role,str(body.get('specialty',''))[:100],str(body.get('registration',''))[:100]))
        except sqlite3.IntegrityError:raise Error('That email is already registered.')
        audit(c,u,'user_created',id_);c.commit();return {'id':id_}
    if path=='/api/patients' and method=='POST':
        require_role(u,'reception','admin')
        name=str(body.get('name','')).strip()[:120]; sex=body.get('sex','')
        try:age=int(body.get('age',-1))
        except (ValueError,TypeError):raise Error('Enter a valid age.')
        if not name or not 18<=age<=120 or sex not in ('Female','Male','Other','Not specified'):raise Error('Name, adult age and sex field are required. This demo covers adult OPD only.')
        id_=uid('patient');mrn='LH-'+secrets.token_hex(4).upper()
        c.execute('INSERT INTO patients VALUES(?,?,?,?,?,?,?,?)',(id_,u['org'],mrn,name,age,sex,str(body.get('phone',''))[:30],now()))
        audit(c,u,'fictional_patient_created',id_);c.commit();return {'id':id_}
    if path=='/api/encounters' and method=='POST':
        require_role(u,'reception','admin')
        patient=c.execute('SELECT * FROM patients WHERE id=? AND org=?',(body.get('patient'),u['org'])).fetchone()
        doctor=c.execute("SELECT * FROM users WHERE id=? AND org=? AND role='doctor'",(body.get('doctor'),u['org'])).fetchone()
        if not patient or not doctor:raise Error('Choose a patient and doctor from this hospital.')
        c.execute('BEGIN IMMEDIATE')
        day=now()[:10]
        active=c.execute("SELECT id FROM encounters WHERE patient=? AND doctor=? AND day=? AND status IN ('waiting','in_consultation','draft')",(patient['id'],doctor['id'],day)).fetchone()
        if active:raise Error('This patient already has an active visit with this doctor today.',409)
        token=c.execute('SELECT COALESCE(MAX(token),0)+1 FROM encounters WHERE org=? AND doctor=? AND day=?',(u['org'],doctor['id'],day)).fetchone()[0]
        id_=uid('encounter')
        c.execute('INSERT INTO encounters(id,org,patient,doctor,token,day,draft,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',(id_,u['org'],patient['id'],doctor['id'],token,day,json.dumps(blank()),now(),now()))
        audit(c,u,'encounter_created',id_);c.commit();return {'id':id_,'token':token}
    match=re.fullmatch(r'/api/encounters/([a-zA-Z0-9_]+)/([a-z-]+)',path)
    if not match: raise Error('Route not found.',404)
    id_,action=match.groups(); e=encounter(c,u,id_)
    if action=='history' and method=='GET':
        # Assigned current doctor may view the patient's approved hospital history.
        documents=[dict(x) for x in c.execute('SELECT d.* FROM documents d JOIN encounters e ON e.id=d.encounter WHERE e.org=? AND e.patient=? ORDER BY d.created DESC',(u['org'],e['patient']))]
        for d in documents:d['snapshot']=json.loads(d['snapshot'])
        audit(c,u,'history_viewed',e['patient']);c.commit();return {'documents':documents}
    if method!='POST': raise Error('Method not allowed.',405)
    if action in ('draft','approve','reopen','consent'):
        c.execute('BEGIN IMMEDIATE');e=encounter(c,u,id_);check_version(e,body)
    else:check_version(e,body)
    if action=='reopen':
        if e['status']!='approved':raise Error('Only an approved record can be amended.')
        reason=str(body.get('reason','')).strip()
        if len(reason)<5:raise Error('Record the reason for this amendment.')
        c.execute("UPDATE encounters SET status='draft',reason=?,version=version+1,updated=? WHERE id=?",(reason[:1000],now(),id_))
        audit(c,u,'amendment_started',id_);c.commit();return {'ok':True}
    if e['status']=='approved':raise Error('This record is approved. Start a versioned amendment before editing.',409)
    if action=='consent':
        value=body.get('consent')
        if value not in ('granted','declined','manual'):raise Error('Choose a consent decision.')
        c.execute('UPDATE encounters SET consent=?,version=version+1,updated=? WHERE id=?',(value,now(),id_))
        audit(c,u,'recording_consent_'+value,id_);c.commit();return {'ok':True}
    if action=='draft':
        draft=validate_draft(body.get('draft'))
        transcript=body.get('transcript',e['transcript'])
        if not isinstance(transcript,str) or len(transcript)>50000:raise Error('Transcript exceeds the allowed length.')
        c.execute("UPDATE encounters SET draft=?,transcript=?,status='draft',version=version+1,updated=? WHERE id=?",(json.dumps(draft),transcript,now(),id_))
        audit(c,u,'draft_saved',id_);c.commit();return {'ok':True}
    if action=='approve':
        if body.get('attest') is not True:raise Error('Clinician review confirmation is required.')
        draft=json.loads(e['draft']);errors=approval_errors(draft)
        if errors:raise Error('\n'.join(errors),422)
        patient=dict(c.execute('SELECT id,name,mrn,age,sex FROM patients WHERE id=?',(e['patient'],)).fetchone())
        org=dict(c.execute('SELECT * FROM organisations WHERE id=?',(u['org'],)).fetchone())
        previous=c.execute('SELECT id FROM documents WHERE encounter=? ORDER BY version DESC LIMIT 1',(id_,)).fetchone()
        snapshot={'draft':draft,'patient':patient,'doctor':{k:u[k] for k in ('name','specialty','registration')},'organisation':org,'token':e['token'],'reason':e['reason'],'approved_at':now()}
        docid=uid('doc')
        c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',(docid,u['org'],id_,json.dumps(snapshot),now(),u['id'],previous['id'] if previous else None,e['version']))
        c.execute("UPDATE encounters SET status='approved',version=version+1,updated=? WHERE id=?",(now(),id_))
        audit(c,u,'document_approved',docid);c.commit();return {'id':docid}
    if action=='transcribe':
        if e['consent']!='granted':raise Error('Recording consent must be granted before audio processing.',403)
        encoded=body.get('audio','')
        if not isinstance(encoded,str) or len(encoded)>MAX_BODY:raise Error('Recording too large. Use shorter segments.')
        try:audio=base64.b64decode(encoded,validate=True)
        except ValueError:raise Error('Invalid recording.')
        if not audio or len(audio)>14*1024*1024:raise Error('Recording is empty or too large. Use a shorter segment.')
        result=ai.transcribe(audio,str(body.get('mime','')).split(';')[0])
        audit(c,u,'audio_transcribed',id_);c.commit();return {'transcript':result,'version':e['version']}
    if action in ('generate','propose-amendment'):
        transcript=body.get('transcript','')
        if not isinstance(transcript,str) or len(transcript)>50000:raise Error('Invalid transcript.')
        instruction=str(body.get('instruction','')).strip()[:10000]
        if action=='generate' and not transcript.strip():raise Error('Enter or record a consultation first.')
        if action=='propose-amendment' and not instruction:raise Error('Enter a clinician amendment instruction.')
        current=validate_draft(body.get('draft',json.loads(e['draft'])))
        catalogue=[dict(x) for x in c.execute('SELECT name,strength,form,source FROM catalogue WHERE org=?',(u['org'],))]
        proposed=validate_draft(ai.generate(transcript,current,instruction if action=='propose-amendment' else None,catalogue))
        # Model output never carries medicine confirmation or approval authority.
        for med in proposed['medicines']:med['confirmed']=False
        audit(c,u,'ai_proposal_generated',id_);c.commit();return {'proposal':proposed,'version':e['version']}
    raise Error('Action not found.',404)

def application(env,start_response):
    headers=[('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),
        ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"),
        ('Permissions-Policy','microphone=(self), camera=(), geolocation=()')]
    if PRODUCTION:headers.append(('Strict-Transport-Security','max-age=31536000'))
    path=env.get('PATH_INFO','/');method=env.get('REQUEST_METHOD','GET');status=200;c=None
    try:
        if path=='/health':result={'status':'ok','mode':'demonstration'}
        elif not path.startswith('/api/'):
            if method not in ('GET','HEAD'):raise Error('Method not allowed.',405)
            files={'/':'index.html','/app.js':'app.js','/styles.css':'styles.css'}
            if path not in files:raise Error('Not found.',404)
            file=ROOT/'static'/files[path]; raw=file.read_bytes()
            headers.append(('Content-Type',mimetypes.guess_type(str(file))[0] or 'application/octet-stream'))
            start_response('200 OK',headers+[('Content-Length',str(len(raw)))]);return [b'' if method=='HEAD' else raw]
        else:
            if method not in ('GET','POST'):raise Error('Method not allowed.',405)
            if method=='POST':
                origin=env.get('HTTP_ORIGIN','')
                host=env.get('HTTP_HOST','')
                if origin and origin not in ('https://'+host,'http://'+host):raise Error('Cross-origin request rejected.',403)
                if env.get('HTTP_SEC_FETCH_SITE')=='cross-site':raise Error('Cross-site request rejected.',403)
            c=connect()
            body=json_body(env) if method=='POST' else {}
            if path=='/api/login' and method=='POST':result,extra=login(c,body,env.get('REMOTE_ADDR','unknown'));headers+=extra
            else:
                u,s=get_session(c,env)
                if not u:raise Error('Please sign in to continue.',401)
                if method=='POST' and not hmac.compare_digest(str(env.get('HTTP_X_CSRF_TOKEN','')),s['csrf']):raise Error('Session verification failed. Refresh and try again.',403)
                result=route(c,env,u,s,path,method,body)
    except Error as e:status=e.status;result={'error':e.message}
    except ai.ProviderError as e:status=503;result={'error':str(e)}
    except Exception:
        # Do not put patient content, provider responses or credentials in logs.
        status=500;result={'error':'The request could not be completed. No pending transaction was committed.'}
    finally:
        if c:c.close()
    labels={200:'OK',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',409:'Conflict',413:'Content Too Large',422:'Unprocessable Content',429:'Too Many Requests',500:'Internal Server Error',503:'Service Unavailable'}
    raw=json.dumps(result).encode();headers+=[('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(raw)))]
    start_response(f'{status} {labels.get(status,"Error")}',headers);return [raw]

class ThreadedServer(ThreadingMixIn,WSGIServer):
    daemon_threads=True

initialise()
if __name__=='__main__':
    host=os.environ.get('HOST','127.0.0.1');port=int(os.environ.get('PORT','8000'))
    print(f'Lumrivo Clinical demonstration: http://{host}:{port}')
    with make_server(host,port,application,server_class=ThreadedServer) as httpd:httpd.serve_forever()
