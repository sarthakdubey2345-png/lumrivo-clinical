import copy
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=BASE.name
os.environ['DEMO_MODE']='true'
os.environ['DEMO_PASSWORD']='Fictional-test-password!2026'
os.environ['PRODUCTION']='false'
from app import server

class Client:
    def __init__(self):self.cookie='';self.csrf=''
    def request(self,path,body=None,csrf=True,origin=None):
        raw=json.dumps(body).encode() if body is not None else b''
        env={'REQUEST_METHOD':'POST' if body is not None else 'GET','PATH_INFO':'/api/'+path,
             'wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),'HTTP_HOST':'localhost:8000',
             'REMOTE_ADDR':'127.0.0.1','HTTP_COOKIE':self.cookie}
        if csrf:env['HTTP_X_CSRF_TOKEN']=self.csrf
        if origin:env['HTTP_ORIGIN']=origin
        result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        data=json.loads(b''.join(server.application(env,start)))
        if 'Set-Cookie' in result['headers']:self.cookie=result['headers']['Set-Cookie'].split(';')[0]
        if 'csrf' in data:self.csrf=data['csrf']
        return result['status'],data
    def login(self,name='meera'):
        status,data=self.request('login',{'email':name+'@demo.lumrivo.com','password':'Fictional-test-password!2026'})
        assert status==200,(status,data)
        return self
    def encounter(self,id='e_p_aanya'):
        return next(e for e in self.request('bootstrap')[1]['encounters'] if e['id']==id)
    def post(self,action,extra=None,id='e_p_aanya'):
        return self.request(f'encounters/{id}/{action}',{'version':self.encounter(id)['version'],**(extra or {})})

class Workflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=Path(BASE.name)/'base.sqlite3'
        with server.connect() as c:
            target=sqlite3.connect(cls.base);c.backup(target);target.close()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        server.DATA=Path(self.temp.name);server.DB=server.DATA/'clinical.sqlite3'
        source=sqlite3.connect(self.base);dest=sqlite3.connect(server.DB);source.backup(dest);source.close();dest.close()
        server.LOGIN_ATTEMPTS.clear()
        self.doctor=Client().login()
    def tearDown(self):self.temp.cleanup()
    def good(self):
        d=server.blank();d.update(complaints='Fictional complaint',assessment='Fictional clinician assessment',allergy_status='none_reported',instructions='Bring fictional records.',followup='Next fictional visit')
        return d
    def save(self,d):
        r=self.doctor.post('draft',{'draft':d,'transcript':'Fictional source'})
        self.assertEqual(r[0],200,r)
    def test_anonymous_access_rejected(self):self.assertEqual(Client().request('bootstrap')[0],401)
    def test_wrong_password_rejected(self):self.assertEqual(Client().request('login',{'email':'meera@demo.lumrivo.com','password':'incorrect'})[0],401)
    def test_csrf_required(self):self.assertEqual(self.doctor.request('logout',{},csrf=False)[0],403)
    def test_cross_origin_rejected(self):self.assertEqual(self.doctor.request('logout',{},origin='https://untrusted.test')[0],403)
    def test_reception_never_receives_clinical_notes(self):
        d=Client().login('reception').request('bootstrap')[1]
        self.assertTrue(d['encounters'])
        for e in d['encounters']:
            self.assertNotIn('draft',e);self.assertNotIn('transcript',e);self.assertNotIn('consent',e)
    def test_reception_cannot_access_history(self):self.assertEqual(Client().login('reception').request('encounters/e_p_aanya/history')[0],403)
    def test_doctor_only_sees_assigned_visits(self):
        data=self.doctor.request('bootstrap')[1]
        self.assertTrue(all(e['doctor']=='dr_meera' for e in data['encounters']))
        self.assertNotIn('p_rohan',[p['id'] for p in data['patients']])
    def test_other_doctor_edit_rejected(self):self.assertEqual(self.doctor.request('encounters/e_p_rohan/draft',{'version':1,'draft':self.good()})[0],403)
    def test_admin_has_no_clinical_edit_right(self):self.assertEqual(Client().login('admin').request('encounters/e_p_aanya/draft',{'version':1,'draft':self.good()})[0],403)
    def test_cross_hospital_patient_hidden(self):
        with server.connect() as c:
            c.execute('INSERT INTO organisations VALUES(?,?,?,?)',('other','Other Hospital','',''))
            c.execute('INSERT INTO patients VALUES(?,?,?,?,?,?,?,?)',('other_p','other','OTHER','Fictional Other',30,'Male','',server.now()))
        rows=Client().login('reception').request('bootstrap')[1]['patients']
        self.assertNotIn('other_p',[p['id'] for p in rows])
    def test_cross_hospital_visit_creation_rejected(self):
        with server.connect() as c:
            c.execute('INSERT INTO organisations VALUES(?,?,?,?)',('other','Other Hospital','',''))
            c.execute('INSERT INTO patients VALUES(?,?,?,?,?,?,?,?)',('other_p','other','OTHER','Fictional Other',30,'Male','',server.now()))
        r=Client().login('reception').request('encounters',{'patient':'other_p','doctor':'dr_meera'})
        self.assertEqual(r[0],400)
    def test_stale_save_does_not_overwrite(self):
        version=self.doctor.encounter()['version'];self.save(self.good())
        stale=self.good();stale['assessment']='Overwrite attempt'
        self.assertEqual(self.doctor.request('encounters/e_p_aanya/draft',{'version':version,'draft':stale})[0],409)
        self.assertEqual(self.doctor.encounter()['draft']['assessment'],'Fictional clinician assessment')
    def test_audio_requires_explicit_consent(self):self.assertEqual(self.doctor.post('transcribe',{'audio':'YXVkaW8=','mime':'audio/webm'})[0],403)
    def test_declined_consent_allows_manual_documentation(self):
        self.assertEqual(self.doctor.post('consent',{'consent':'declined'})[0],200)
        self.save(self.good());self.assertEqual(self.doctor.post('approve',{'attest':True})[0],200)
    def test_no_default_allergy_claim(self):self.assertEqual(server.blank()['allergy_status'],'not_assessed')
    def test_approval_blocks_unassessed_allergies(self):
        d=self.good();d['allergy_status']='not_assessed';self.save(d)
        self.assertEqual(self.doctor.post('approve',{'attest':True})[0],422)
    def test_approval_blocks_allergy_conflict(self):
        d=self.good();d['allergies']='Reported fictional reaction';self.save(d)
        self.assertEqual(self.doctor.post('approve',{'attest':True})[0],422)
    def test_approval_blocks_incomplete_medicine(self):
        d=self.good();d['medicines']=[{'name':'Demo Medicine A','confirmed':True}];self.save(d)
        self.assertEqual(self.doctor.post('approve',{'attest':True})[0],422)
    def test_approval_blocks_unconfirmed_medicine(self):
        d=self.good();d['medicines']=[{**{k:'fictional' for k in server.ai.MED_FIELDS},'confirmed':False}];self.save(d)
        self.assertEqual(self.doctor.post('approve',{'attest':True})[0],422)
    def test_approval_requires_attestation(self):
        self.save(self.good());self.assertEqual(self.doctor.post('approve',{'attest':False})[0],400)
    def test_approval_blocks_review_items(self):
        d=self.good();d['uncertainties']=['Unclear name'];self.save(d)
        self.assertEqual(self.doctor.post('approve',{'attest':True})[0],422)
    def test_approved_record_cannot_be_directly_edited(self):
        self.save(self.good());self.doctor.post('approve',{'attest':True})
        self.assertEqual(self.doctor.post('draft',{'draft':self.good()})[0],409)
    def test_amendment_preserves_original_and_links_versions(self):
        self.save(self.good());first=self.doctor.post('approve',{'attest':True})[1]['id']
        before=self.doctor.request('encounters/e_p_aanya/history')[1]['documents']
        original=next(d for d in before if d['id']==first)['snapshot']
        self.assertEqual(self.doctor.post('reopen',{'reason':'Late fictional clarification'})[0],200)
        changed=self.good();changed['instructions']='Updated fictional advice';self.save(changed)
        second=self.doctor.post('approve',{'attest':True})[1]['id']
        docs=self.doctor.request('encounters/e_p_aanya/history')[1]['documents']
        self.assertEqual(next(d for d in docs if d['id']==first)['snapshot'],original)
        self.assertEqual(next(d for d in docs if d['id']==second)['supersedes'],first)
    def test_double_approval_does_not_issue_duplicate(self):
        self.save(self.good());version=self.doctor.encounter()['version']
        self.assertEqual(self.doctor.post('approve',{'attest':True})[0],200)
        self.assertEqual(self.doctor.request('encounters/e_p_aanya/approve',{'version':version,'attest':True})[0],409)
        with server.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM documents WHERE encounter='e_p_aanya'").fetchone()[0],1)
    def test_missing_ai_key_never_fakes_generated_output(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}):
            r=self.doctor.post('generate',{'transcript':'fictional consultation','draft':self.good()})
        self.assertEqual(r[0],503);self.assertIn('not connected',r[1]['error'])
    def test_ai_proposal_cannot_confirm_or_persist_medicines(self):
        proposal=self.good();proposal['medicines']=[{**{k:'fictional' for k in server.ai.MED_FIELDS},'confirmed':True}]
        before=self.doctor.encounter()
        with patch.object(server.ai,'generate',return_value=proposal):
            r=self.doctor.post('generate',{'transcript':'fictional','draft':self.good()})
        self.assertEqual(r[0],200);self.assertFalse(r[1]['proposal']['medicines'][0]['confirmed'])
        self.assertEqual(self.doctor.encounter()['draft'],before['draft'])
    def test_duplicate_medicine_ids_rejected(self):
        d=self.good();d['medicines']=[{'id':'same','name':'A'},{'id':'same','name':'B'}]
        self.assertEqual(self.doctor.post('draft',{'draft':d})[0],400)
    def test_duplicate_active_visit_rejected(self):
        self.assertEqual(Client().login('reception').request('encounters',{'patient':'p_aanya','doctor':'dr_meera'})[0],409)
    def test_registration_creates_distinct_patient_id(self):
        client=Client().login('reception');a=client.request('patients',{'name':'Fictional One','age':30,'sex':'Female','phone':'shared'})
        b=client.request('patients',{'name':'Fictional Two','age':40,'sex':'Male','phone':'shared'})
        self.assertEqual(a[0],200);self.assertEqual(b[0],200);self.assertNotEqual(a[1]['id'],b[1]['id'])
    def test_backup_and_restore_preserve_approved_snapshot(self):
        self.save(self.good());self.doctor.post('approve',{'attest':True})
        backup=Path(self.temp.name)/'backup.sqlite3'
        with server.connect() as source:
            target=sqlite3.connect(backup);source.backup(target);target.close()
        with sqlite3.connect(backup) as restored:
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            snap=json.loads(restored.execute("SELECT snapshot FROM documents WHERE encounter='e_p_aanya'").fetchone()[0])
            self.assertEqual(snap['draft']['instructions'],'Bring fictional records.')
    def test_logout_revokes_session(self):
        self.assertEqual(self.doctor.request('logout',{})[0],200);self.assertEqual(self.doctor.request('bootstrap')[0],401)
    def test_audit_logs_approval_without_clinical_content(self):
        self.save(self.good());self.doctor.post('approve',{'attest':True})
        with server.connect() as c:rows=[dict(r) for r in c.execute('SELECT * FROM audit')]
        self.assertTrue(any(r['action']=='document_approved' for r in rows))
        self.assertNotIn('Fictional complaint',json.dumps(rows))

if __name__=='__main__':unittest.main()
