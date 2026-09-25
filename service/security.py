import secrets
import time
import uuid
import threading
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash('timing-equalizer-not-an-account')
_password_slots=threading.BoundedSemaphore(2)

class AuthenticationBusy(Exception):
    pass

def login(store, settings, username, password):
    with store.connection() as db:
        row = db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
    if not _password_slots.acquire(blocking=False):
        raise AuthenticationBusy()
    try:
        try:
            hasher.verify(row['password'] if row else DUMMY_HASH,password)
        except VerificationError:
            return None
    finally:
        _password_slots.release()
    if not row:
        return None
    actor = {k:row[k] for k in ('id','tenant','username','role')}
    now, jti, csrf = int(time.time()), uuid.uuid4().hex, secrets.token_urlsafe(32)
    claims = {'sub':actor['id'],'jti':jti,'iat':now,'exp':now+settings.session_seconds,'iss':'it-service-desk','aud':'it-portal'}
    token = jwt.encode(claims,settings.jwt_secret,algorithm='HS256')
    with store.connection(True) as db:
        db.execute('DELETE FROM sessions WHERE expires<?',(now,))
        db.execute('INSERT INTO sessions(jti,user_id,expires,csrf) VALUES(?,?,?,?)',(jti,actor['id'],claims['exp'],csrf))
        store.audit(db,actor,'auth.login')
    return {'access_token':token,'csrf_token':csrf,'user':actor,'expires_in':settings.session_seconds}

def authenticate(store, settings, token):
    try:
        claims = jwt.decode(token,settings.jwt_secret,algorithms=['HS256'],audience='it-portal',issuer='it-service-desk',options={'require':['sub','exp','iat','jti']})
    except jwt.PyJWTError:
        return None
    with store.connection() as db:
        row = db.execute('SELECT u.id,u.tenant,u.username,u.role,s.jti,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.jti=? AND s.user_id=? AND s.revoked=0 AND s.expires>?',(claims['jti'],claims['sub'],time.time())).fetchone()
    return dict(row) if row else None
