import copy
import json
import unittest
from unittest.mock import patch
from app import ai, grounding

def fact(value='', evidence=None): return {'value': value, 'evidence': evidence or []}
def candidate():
    raw = {k: fact() for k in grounding.TEXT}
    raw.update(allergy_status=fact('not_assessed'), medicines=[], uncertainties=[])
    return raw
def medication():
    m = {k: fact() for k in grounding.MED}
    m.update(id='', name=fact('Paracetamol', ['Paracetamol 500 mg every 6-8 hours as needed for fever.']),
        strength=fact('500 mg', ['Paracetamol 500 mg every 6-8 hours as needed for fever.']),
        frequency=fact('Every 6-8 hours as needed for fever', ['Paracetamol 500 mg every 6-8 hours as needed for fever.']))
    return m
SOURCE = 'Patient: Cough for 3 days. No known allergies. Doctor: Paracetamol 500 mg every 6-8 hours as needed for fever. Maximum 3000 mg per day. Rest and warm fluids.'

class Grounding(unittest.TestCase):
    def generate(self, raw, issues=None, source=SOURCE, current=None, instruction=None):
        with patch.object(grounding, 'structured', side_effect=[raw, {'issues': issues or []}]):
            return grounding.generate(source, current, instruction)
    def test_fabricated_source_is_not_accepted(self):
        raw=candidate();raw['complaints']=fact('Yellow sputum', ['Patient: Yellow sputum.'])
        result=self.generate(raw)
        self.assertEqual(result['complaints'],'');self.assertTrue(result['uncertainties'])
    def test_missing_allergies_remain_unknown(self):
        result=self.generate(candidate())
        self.assertEqual(result['allergy_status'],'not_assessed');self.assertFalse(result['allergy_reviewed'])
    def test_explicit_patient_denial_preserved(self):
        raw=candidate();raw['allergy_status']=fact('none_reported',['No known allergies.'])
        self.assertEqual(self.generate(raw)['allergy_status'],'none_reported')
    def test_absence_of_allergy_cannot_be_added_without_quote(self):
        raw=candidate();raw['allergy_status']=fact('none_reported')
        self.assertEqual(self.generate(raw)['allergy_status'],'not_assessed')
    def test_unspoken_route_form_dose_duration_not_completed(self):
        raw=candidate();raw['medicines']=[medication()]
        result=self.generate(raw);med=result['medicines'][0]
        for k in ('route','dose','duration'):self.assertEqual(med[k],'')
        self.assertEqual(med['name'],'Paracetamol');self.assertFalse(med['confirmed'])
        self.assertTrue(any('missing' in x for x in result['uncertainties']))
    def test_invented_numeric_detail_removed(self):
        raw=candidate();m=medication();m['dose']=fact('10 ml',['Paracetamol 500 mg every 6-8 hours as needed for fever.']);raw['medicines']=[m]
        self.assertEqual(self.generate(raw)['medicines'][0]['dose'],'')
    def test_auditor_rejects_interval_as_liquid_dose(self):
        raw=candidate();m=medication();m['dose']=fact('6-8 ml',['Paracetamol 500 mg every 6-8 hours as needed for fever.']);raw['medicines']=[m]
        result=self.generate(raw,[{'field':'medicines','medicine_index':0,'reason':'Interval was incorrectly converted to liquid volume.'}])
        self.assertEqual(result['medicines'],[])
    def test_auditor_removes_inferred_diagnosis(self):
        raw=candidate();raw['assessment']=fact('Viral infection',['Cough for 3 days.'])
        result=self.generate(raw,[{'field':'assessment','medicine_index':-1,'reason':'No clinician diagnosis was stated.'}])
        self.assertEqual(result['assessment'],'')
    def test_quote_whitespace_normalisation_only(self):
        self.assertTrue(grounding.check_fact(fact('rest',['Rest and\n warm fluids.']), SOURCE))
        self.assertFalse(grounding.check_fact(fact('rest',['Doctor said to rest.']), SOURCE))
    def test_fresh_generation_excludes_existing_draft_and_catalogue(self):
        with patch.object(grounding,'structured',side_effect=[candidate(),{'issues':[]}]) as call:
            grounding.generate(SOURCE, {'assessment':'Old fictional diagnosis'}, catalogue=[{'name':'Old medicine'}])
        payload=call.call_args_list[0].args[1]
        self.assertIsNone(payload['CURRENT']);self.assertNotIn('Old fictional',json.dumps(payload));self.assertNotIn('Old medicine',json.dumps(payload))
    def test_unchanged_manual_amendment_fields_survive(self):
        current={k:'' for k in grounding.TEXT};current.update(history='Manually checked history',allergy_status='not_assessed',medicines=[])
        raw=candidate();raw['history']=fact(current['history'])
        self.assertEqual(self.generate(raw,current=current,instruction='Update follow-up only.')['history'],current['history'])
    def test_invalid_review_response_fails_closed(self):
        with patch.object(grounding,'structured',side_effect=[candidate(),{}]):
            with self.assertRaises(ai.ProviderError):grounding.generate(SOURCE)
    def test_provider_review_failure_never_returns_unaudited_note(self):
        with patch.object(grounding,'structured',side_effect=[candidate(),ai.ProviderError('offline')]):
            with self.assertRaises(ai.ProviderError):grounding.generate(SOURCE)
    def test_control_characters_rejected(self):
        self.assertFalse(grounding.check_fact(fact('Oral\x13',['Rest and warm fluids.']),SOURCE))
    def test_speaker_labels_not_assigned_clinical_roles(self):
        response={'segments':[{'speaker':'A','start':0,'end':1,'text':'Hello'},{'speaker':'B','start':1,'end':2,'text':'Cough'}]}
        with patch.object(ai,'request',return_value=response) as call:
            result=ai.transcribe(b'fictional audio','audio/webm')
        self.assertIn('Speaker A',result);self.assertIn('Speaker B',result);self.assertNotIn('Doctor:',result)
        body=call.call_args.args[1];self.assertIn(b'diarized_json',body);self.assertIn(b'chunking_strategy',body)
    def test_no_speech_is_an_error_not_invented_transcript(self):
        with patch.object(ai,'request',return_value={'segments':[]}):
            with self.assertRaises(ai.ProviderError):ai.transcribe(b'fictional','audio/webm')

if __name__=='__main__': unittest.main()
