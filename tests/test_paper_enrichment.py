"""Synthetic immutable metadata revisions; no real cropper/API calls."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest
from unittest.mock import patch
import zipfile

import paper_enrichment as enrichment
from source_keys import combine_keys
from test_batch import pdf
from test_image_ready import image_export, row
from test_workspace import FakeAPI, w


CLIENT_FACTORY = enrichment._client


class PaperEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'Generated jobs'
        self.runtime = self.base / 'Installed tool'
        self.runtime.mkdir()
        (self.runtime / 'question_boundaries.py').write_text('# synthetic fixture runner\n')
        self.addCleanup(patch.stopall)
        patch.object(enrichment, '_client', return_value=w).start()
        patch.object(w, 'HERE', self.runtime).start()
        self.question = pdf(self.base / 'Original question paper.pdf', 'Paper ID: PAPER-02 JEE Main')
        self.answers = {n: str((n - 1) % 5) for n in range(1, 13)}
        self.key = self.key_pdf('Original source key.pdf', self.answers)
        self.fixture = self.base / 'Synthetic images.zip'
        self.rows = [row(n, None, source_type='numerical',
                         source_number=(n - 1) % 4 + 1,
                         source_section={'text': 'NUMERICAL VALUE TYPE', 'page': 1})
                     for n in range(1, 13)]
        image_export(self.fixture, self.rows, image_only=True)
        self.crop_calls = 0

        def crop(command, **kwargs):
            self.crop_calls += 1
            out = Path(command[command.index('--out') + 1])
            out.mkdir(parents=True)
            with zipfile.ZipFile(self.fixture) as z:
                z.extractall(out)
            return SimpleNamespace(returncode=0)

        args = argparse.Namespace(source=self.question, key=None, solutions=None,
            root=self.root, paper_id='PAPER-02', profile='unspecified', offline=True,
            keep_color=False, no_review=True)
        with patch.object(w.subprocess, 'run', side_effect=crop):
            self.folder = w.process(args)
        self.original_manifest = (self.folder / 'job.json').read_bytes()
        self.initial_status = (self.folder / 'status.json').read_bytes()
        self.initial = self.folder / 'runs' / json.loads(self.initial_status)['run_id']
        self.original_archive = (self.initial / 'export.zip').read_bytes()

    def key_pdf(self, name, answers, paper='PAPER-02'):
        text = 'Paper ID: ' + paper + ' JEE Main ' + ' '.join(str(n) + ') ' + str(value) for n, value in answers.items())
        return pdf(self.base / name, text.replace('(', r'\(').replace(')', r'\)'))

    def enrich(self, **kwargs):
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No cropper, login or service')):
            return enrichment.enrich(self.folder, **kwargs)

    def current(self):
        status = json.loads((self.folder / 'status.json').read_text())
        run = self.folder / 'runs' / status['run_id']
        with zipfile.ZipFile(run / 'export.zip') as z:
            data = json.loads(z.read('exam.json'))
        return status, run, data

    def test_late_real_synthetic_key_updates_answers_without_recropping(self):
        result = self.enrich(key=self.key)
        status, run, data = self.current()
        self.assertFalse(result['reused'])
        self.assertEqual(self.crop_calls, 1)
        self.assertEqual((self.folder / 'job.json').read_bytes(), self.original_manifest)
        self.assertEqual((self.initial / 'export.zip').read_bytes(), self.original_archive)
        self.assertEqual(status['state'], 'ready_for_integration')
        self.assertEqual(status['publication'], 'not_deployed')
        self.assertEqual([q['answer'] for q in data['questions']], list(self.answers.values()))
        self.assertEqual(data['questions'][0]['answer'], '0')
        self.assertEqual(data['questions'][1]['source_type'], 'numerical')
        self.assertEqual([q['source_number'] for q in data['questions']], [q['source_number'] for q in self.rows])
        with zipfile.ZipFile(self.initial / 'export.zip') as old, zipfile.ZipFile(run / 'export.zip') as new:
            for q in self.rows:
                self.assertEqual(old.read(q['image']), new.read(q['image']))
        self.assertTrue(enrichment.supplement_payload(self.folder, run))

    def test_key_replacement_creates_another_immutable_revision(self):
        first = self.enrich(key=self.key)
        first_run = Path(first['run'])
        saved = (first_run / 'export.zip').read_bytes()
        answers = dict(self.answers, **{})
        answers[2] = '4'
        revised = self.key_pdf('Revised source key.pdf', answers)
        self.enrich(key=revised)
        _, current, data = self.current()
        self.assertNotEqual(current, first_run)
        self.assertEqual((first_run / 'export.zip').read_bytes(), saved)
        self.assertEqual(data['questions'][1]['answer'], '4')
        self.assertEqual(self.crop_calls, 1)

    def test_repeated_batch_automatically_attaches_late_key_then_reuses_revision(self):
        args = argparse.Namespace(source=self.question, key=self.key, solutions=None,
            root=self.root, paper_id='PAPER-02', profile='unspecified', offline=True,
            keep_color=False, no_review=True)
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Existing pixels must be reused')):
            w.process(args)
            first = json.loads((self.folder / 'status.json').read_text())['run_id']
            w.process(args)
        self.assertEqual(json.loads((self.folder / 'status.json').read_text())['run_id'], first)
        self.assertEqual(len(list((self.folder / 'runs').iterdir())), 2)
        self.assertEqual((self.folder / 'job.json').read_bytes(), self.original_manifest)

    def test_sync_includes_supplement_and_its_original_image_parent(self):
        result = self.enrich(key=self.key)
        run = Path(result['run'])
        api = FakeAPI()
        w.sync_job(self.folder, api)
        prefix = 'paper_jobs/PAPER-02/runs/'
        self.assertIn(prefix + run.name + '/supplement.json', api.files)
        self.assertIn(prefix + run.name + '/supplements/answer_key.pdf', api.files)
        self.assertIn(prefix + self.initial.name + '/export.zip', api.files)
        self.assertEqual(api.blobs[api.files[prefix + self.initial.name + '/export.zip']], self.original_archive)

    def test_duplicate_same_supplement_bytes_reuses_current_revision(self):
        first = self.enrich(key=self.key)
        alias = self.base / 'Same key with another filename.pdf'
        alias.write_bytes(self.key.read_bytes())
        second = self.enrich(key=alias)
        self.assertTrue(second['reused'])
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertEqual(len(list((self.folder / 'runs').iterdir())), 2)

    def test_can_enrich_requires_the_same_original_question_bytes(self):
        self.assertTrue(enrichment.can_enrich(self.folder, self.question, key=self.key)['eligible'])
        other = pdf(self.base / 'Different questions.pdf', 'Paper ID: PAPER-99 JEE Main')
        self.assertFalse(enrichment.can_enrich(self.folder, other, key=self.key)['eligible'])
        self.assertEqual((self.folder / 'status.json').read_bytes(), self.initial_status)

    def test_incomplete_key_does_not_replace_current_status_or_create_a_run(self):
        incomplete = self.key_pdf('Incomplete key.pdf', {n: self.answers[n] for n in range(1, 10)})
        with self.assertRaisesRegex(w.WorkspaceError, 'incomplete or mismatched'):
            self.enrich(key=incomplete)
        self.assertEqual((self.folder / 'status.json').read_bytes(), self.initial_status)
        self.assertEqual(len(list((self.folder / 'runs').iterdir())), 1)

    def test_mismatched_printed_identity_does_not_retain_stale_answers(self):
        wrong = self.key_pdf('Another paper key.pdf', self.answers, paper='PAPER-99')
        with self.assertRaisesRegex(w.WorkspaceError, 'incomplete or mismatched'):
            self.enrich(key=wrong)
        self.assertEqual((self.folder / 'status.json').read_bytes(), self.initial_status)
        self.assertEqual(len(list((self.folder / 'runs').iterdir())), 1)

    def test_solutions_only_stay_unmatched_and_survive_later_key_revision(self):
        solution = pdf(self.base / 'Worked solutions.pdf', 'Synthetic supplied solutions')
        self.enrich(solutions=solution)
        status, run, data = self.current()
        self.assertEqual(status['state'], 'images_ready')
        self.assertEqual(status['solutions'], 'supplied_not_matched')
        self.assertTrue(all(q['answer'] is None for q in data['questions']))
        self.enrich(key=self.key)
        status, run, data = self.current()
        supplement = json.loads((run / 'supplement.json').read_text())
        self.assertEqual(set(supplement['sources']), {'answer_key', 'solutions'})
        self.assertEqual((run / 'supplements/solutions.pdf').read_bytes(), solution.read_bytes())
        self.assertEqual(status['solutions'], 'supplied_not_matched')

    def test_selected_profile_is_a_run_snapshot_not_a_rewritten_root_manifest(self):
        self.enrich(key=self.key, profile='jee-main-mock')
        status, run, _ = self.current()
        processing = json.loads((run / 'processing.json').read_text())
        self.assertEqual(processing['job']['profile'], 'jee-main-mock')
        self.assertEqual(processing['job']['requested_scoring']['correct'], 4)
        self.assertEqual(json.loads(self.original_manifest)['profile'], 'unspecified')
        self.assertEqual((self.folder / 'job.json').read_bytes(), self.original_manifest)
        self.assertEqual(status['profile'], 'jee-main-mock')

    def test_effective_profile_change_is_an_enrichment_without_source_changes(self):
        self.assertTrue(enrichment.can_enrich(self.folder, self.question, profile='jee-main-mock')['eligible'])
        result = self.enrich(profile='jee-main-mock')
        status, run, data = self.current()
        self.assertEqual(status['state'], 'images_ready')
        self.assertEqual(status['profile'], 'jee-main-mock')
        self.assertFalse(enrichment.can_enrich(self.folder, self.question, profile='jee-main-mock')['eligible'])
        self.assertEqual(self.crop_calls, 1)
        self.assertEqual((self.folder / 'job.json').read_bytes(), self.original_manifest)

    def test_cli_entry_point_reuses_its_error_and_lock_context(self):
        main = SimpleNamespace(__file__=str(Path(enrichment.__file__).with_name('workspace_client.py')))
        with patch.dict(sys.modules, {'__main__': main}):
            self.assertIs(CLIENT_FACTORY(), main)

    def test_owner_add_policy_keeps_original_zero_and_raw_finalized_marker(self):
        base = {'key': self.answers, 'identity': {}, 'source_sha256': 'synthetic-base',
                'unresolved': [], 'correction_sheet': False}
        final = {'key': {}, 'identity': {}, 'source_sha256': 'synthetic-final', 'unresolved': [],
                 'correction_sheet': True, 'corrections': [
                     {'page': 1, 'number': 1, 'given': '0', 'finalized': 'Add'}]}

        def reader(**kwargs):
            self.assertEqual(kwargs['add_policy'], 'original')
            return combine_keys(base, final, add_policy=kwargs['add_policy'],
                                expected_numbers=kwargs['expected_numbers'])

        with patch('source_keys.read_key_sources', side_effect=reader):
            self.enrich(key=self.key, base_key=self.key)
        _, _, data = self.current()
        self.assertEqual(data['questions'][0]['answer'], '0')
        correction = data['source_key']['corrections'][0]
        self.assertEqual(correction['finalized'], 'Add')
        self.assertEqual(correction['adjudication_basis'], 'owner_selected_original_for_add')

    def test_symlink_supplement_is_refused_without_changing_status(self):
        alias = self.base / 'Unsafe key.pdf'
        alias.symlink_to(self.key)
        with self.assertRaisesRegex(w.WorkspaceError, 'Symlink'):
            self.enrich(key=alias)
        self.assertEqual((self.folder / 'status.json').read_bytes(), self.initial_status)

    def test_supplement_pdf_tampering_is_detected_before_reuse(self):
        self.enrich(key=self.key)
        _, run, _ = self.current()
        (run / 'supplements/answer_key.pdf').write_bytes(b'%PDF-1.7\nchanged supplement')
        with self.assertRaisesRegex(w.WorkspaceError, 'Supplemental source changed'):
            enrichment.supplement_payload(self.folder, run)
        self.assertFalse(enrichment.can_enrich(self.folder, self.question, key=self.key)['eligible'])

    def test_unsafe_supplement_manifest_path_cannot_escape_the_run(self):
        self.enrich(key=self.key)
        _, run, _ = self.current()
        path = run / 'supplement.json'
        manifest = json.loads(path.read_text())
        manifest['sources']['answer_key']['file'] = '../../outside.pdf'
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(w.WorkspaceError, 'Unsafe supplemental source path'):
            enrichment.supplement_payload(self.folder, run)

    def test_payload_rejects_a_run_path_outside_the_job(self):
        outside = self.base / 'Other folder' / 'RUN-01'
        outside.mkdir(parents=True)
        with self.assertRaisesRegex(w.WorkspaceError, 'outside the saved paper job'):
            enrichment.supplement_payload(self.folder, outside)

    def test_shared_job_lock_prevents_a_concurrent_enrichment(self):
        with w.job_lock(self.root, 'PAPER-02'):
            with self.assertRaisesRegex(w.WorkspaceError, 'already running'):
                self.enrich(key=self.key)
        self.assertEqual((self.folder / 'status.json').read_bytes(), self.initial_status)

    def test_incompatible_profile_is_not_applied_to_the_printed_source(self):
        with self.assertRaisesRegex(w.WorkspaceError, 'conflicts with the printed source'):
            self.enrich(key=self.key, profile='jee-advanced-source')
        self.assertEqual((self.folder / 'status.json').read_bytes(), self.initial_status)


if __name__ == '__main__':
    unittest.main()
