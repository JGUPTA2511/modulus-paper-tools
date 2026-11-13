"""Synthetic client source-reader integration; cropper/API operations are simulated."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_batch import pdf
from test_workspace import image, w


class ClientSourceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'Generated jobs'
        self.source = self.base / 'Input originals'
        self.source.mkdir()
        self.output = io.StringIO()
        redirect = redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.question = pdf(self.source / 'PAPER-02_QP.pdf')
        self.final = pdf(self.source / 'PAPER-02_Final Key.pdf')
        self.combined = pdf(self.source / 'PAPER-02_Key & Sols.pdf')
        self.association = {
            'method': 'explicit_companion_filenames', 'paper_id': 'PAPER-02',
            'answer_key_basis': 'final_key',
            'final_key_filename': self.final.name,
            'combined_key_solutions_filename': self.combined.name,
            'filenames': {'questions.pdf': self.question.name,
                          'answer_key.pdf': self.final.name,
                          'solutions.pdf': self.combined.name}}
        self.commands = []

    def args(self, **changes):
        args = argparse.Namespace(source=self.question, key=self.final,
                                  solutions=self.combined, root=self.root,
                                  paper_id='PAPER-02', profile='unspecified',
                                  offline=True, keep_color=False, no_review=True,
                                  source_association=self.association)
        for name, value in changes.items():
            setattr(args, name, value)
        return args

    def crop(self, command, **kwargs):
        self.commands.append(command)
        out = Path(command[command.index('--out') + 1])
        (out / 'crops').mkdir(parents=True)
        for number in range(1, 4):
            (out / 'crops' / f'Q{number:03d}.png').write_bytes(image())
        data = {'count': 3, 'questions': [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0,
             'source_type': 'numerical'},
            {'number': 2, 'image': 'crops/Q002.png', 'answer': '1',
             'source_type': 'numerical'},
            {'number': 3, 'image': 'crops/Q003.png', 'answer': 'Add'}],
            'source_key': {'correction_sheet': True,
                           'corrections': [{'number': 3, 'given': '2',
                                            'finalized': 'Add', 'page': 1}],
                           'unresolved': [{'number': 3, 'marker': 'Add'}],
                           'sources': [{'source_sha256': 'synthetic-hash'}]},
            'source_layout': {'expected_count': 3, 'single_column': True}}
        (out / 'exam.json').write_text(json.dumps(data))
        (out / 'source_key.json').write_text(json.dumps(data['source_key']))
        (out / 'source_layout.json').write_text(json.dumps(data['source_layout']))
        return subprocess.CompletedProcess(command, 0)

    def run_process(self, args=None):
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            return w.process(args or self.args())

    def test_final_and_combined_association_passes_exact_base_without_input_changes(self):
        originals = {p.name: p.read_bytes() for p in self.source.iterdir()}
        folder = self.run_process()
        command = self.commands[0]
        self.assertIn('--source-compat', command)
        self.assertEqual(command[command.index('--add-key-policy') + 1], 'original')
        self.assertEqual(Path(command[command.index('--key') + 1]),
                         folder / 'input/answer_key.pdf')
        self.assertEqual(Path(command[command.index('--key-base') + 1]),
                         folder / 'input/solutions.pdf')
        self.assertEqual((folder / 'input/answer_key.pdf').read_bytes(), self.final.read_bytes())
        self.assertEqual((folder / 'input/solutions.pdf').read_bytes(), self.combined.read_bytes())
        self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()}, originals)

    def test_arbitrary_solutions_are_never_passed_as_base_key(self):
        self.run_process(self.args(source_association=None))
        self.assertNotIn('--key-base', self.commands[0])

    def test_mismatched_or_incomplete_association_never_uses_a_base(self):
        folder, job = w.prepare_job(self.root, 'PAPER-02', self.question,
                                    self.final, self.combined,
                                    source_association=self.association)
        self.assertIsNotNone(w.associated_base_key(folder, job))
        mutations = [
            lambda a: a.update(method='explicit_manual_selection'),
            lambda a: a.update(answer_key_basis='answer_key'),
            lambda a: a.update(final_key_filename='OTHER_Final Key.pdf'),
            lambda a: a.update(combined_key_solutions_filename='OTHER_Key & Sols.pdf'),
            lambda a: a['filenames'].update({'solutions.pdf': 'Unrelated solutions.pdf'}),
            lambda a: a.update(filenames=False),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                changed = json.loads(json.dumps(job))
                mutate(changed['source_association'])
                self.assertIsNone(w.associated_base_key(folder, changed))
        # Even consistent metadata cannot cross two paper IDs.
        changed = json.loads(json.dumps(job))
        name = 'OTHER_Key & Sols.pdf'
        changed['inputs']['solutions']['original_name'] = name
        changed['source_association']['combined_key_solutions_filename'] = name
        changed['source_association']['filenames']['solutions.pdf'] = name
        self.assertIsNone(w.associated_base_key(folder, changed))

    def test_batch_summary_carries_source_adjudication_hold(self):
        args = argparse.Namespace(source=self.source, root=self.root,
                                  profile='unspecified', offline=True,
                                  keep_color=False, paper_profile=[])
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            rows = w.run_batch(args)
        self.assertEqual(rows[0]['integration_state'], 'held_pending_source_key_adjudication')
        self.assertFalse(rows[0]['exam_launch_ready'])
        self.assertEqual(rows[0]['unresolved_source_adjudication'],
                         [{'number': 3, 'marker': 'Add'}])
        self.assertIn('Exam grading held', rows[0]['reason'])

    def test_unresolved_source_marker_preserved_and_grading_held_without_a_profile(self):
        folder = self.run_process()
        status = json.loads((folder / 'status.json').read_text())
        run = folder / 'runs' / status['run_id']
        data = json.loads((run / 'exam.json').read_text())
        check = json.loads((run / 'checks.json').read_text())
        self.assertEqual([q['answer'] for q in data['questions']], [0, '1', 'Add'])
        self.assertEqual(data['questions'][0]['source_type'], 'numerical')
        self.assertEqual(data['questions'][1]['source_type'], 'numerical')
        self.assertEqual(check['source_key'], data['source_key'])
        self.assertEqual(check['source_layout'], data['source_layout'])
        self.assertEqual(check['answer_status'], 'source_adjudication_required')
        self.assertEqual(check['marking_state'], 'source_adjudication_required')
        self.assertFalse(check['exam_launch_ready'])
        self.assertEqual(status['integration_state'], 'held_pending_source_key_adjudication')
        self.assertFalse(status['exam_launch_ready'])
        self.assertEqual(status['publication'], 'not_deployed')
        self.assertEqual(status['solutions'], 'supplied_not_matched')
        with zipfile.ZipFile(run / 'export.zip') as archive:
            self.assertEqual(json.loads(archive.read('source_key.json')), data['source_key'])
            self.assertEqual(json.loads(archive.read('source_layout.json')), data['source_layout'])

    def test_main_profile_does_not_override_unresolved_key_hold(self):
        folder = self.run_process(self.args(profile='jee-main-mock'))
        status = json.loads((folder / 'status.json').read_text())
        check = json.loads((folder / 'runs' / status['run_id'] / 'checks.json').read_text())
        self.assertEqual(check['marking_state'], 'source_adjudication_required')
        self.assertFalse(status['exam_launch_ready'])

    def test_owner_selected_original_policy_preserves_zero_and_raw_correction_basis(self):
        def owner_policy_crop(command, **kwargs):
            result = self.crop(command, **kwargs)
            out = Path(command[command.index('--out') + 1])
            data = json.loads((out / 'exam.json').read_text())
            data['questions'][2]['answer'] = 0
            data['source_key']['unresolved'] = []
            data['source_key']['adjudication_policy'] = {
                'add': 'original', 'basis': 'owner_selected_original_for_add'}
            data['source_key']['corrections'] = [{
                'number': 3, 'given': '0', 'finalized': 'Add', 'page': 1,
                'base_answer': 0, 'effective_answer': 0,
                'adjudication_basis': 'owner_selected_original_for_add'}]
            (out / 'exam.json').write_text(json.dumps(data))
            (out / 'source_key.json').write_text(json.dumps(data['source_key']))
            return result

        with patch.object(w.subprocess, 'run', side_effect=owner_policy_crop):
            folder = w.process(self.args())
        status = json.loads((folder / 'status.json').read_text())
        run = folder / 'runs' / status['run_id']
        data = json.loads((run / 'exam.json').read_text())
        checks = json.loads((run / 'checks.json').read_text())
        self.assertEqual([q['answer'] for q in data['questions']], [0, '1', 0])
        self.assertEqual(data['source_key']['corrections'][0]['finalized'], 'Add')
        self.assertEqual(checks['source_key']['adjudication_policy']['basis'],
                         'owner_selected_original_for_add')
        self.assertEqual(checks['answer_status'], 'source_transcribed_unverified')
        self.assertNotIn('unresolved_source_adjudication', status)
        self.assertNotIn('requested_scoring', json.loads((folder / 'job.json').read_text()))

    def test_approved_legacy_success_is_reused_without_recropping_or_new_run(self):
        folder = self.run_process()
        status_path = folder / 'status.json'
        status = json.loads(status_path.read_text())
        run = folder / 'runs' / status['run_id']
        meta_path = run / 'run.json'
        meta = json.loads(meta_path.read_text())
        meta['cropper_sha256'] = w.APPROVED_LEGACY_CROPPER_SHA256
        w.save_json(meta_path, meta)
        original_manifest = (folder / 'job.json').read_bytes()
        original_archive = (run / 'export.zip').read_bytes()
        runs_before = sorted(p.name for p in (folder / 'runs').iterdir())
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Do not recrop')):
            self.assertTrue(w.cached_run(folder))
            w.process(self.args())
        self.assertEqual((folder / 'job.json').read_bytes(), original_manifest)
        self.assertEqual((run / 'export.zip').read_bytes(), original_archive)
        self.assertEqual(sorted(p.name for p in (folder / 'runs').iterdir()), runs_before)

    def test_unknown_cropper_hash_does_not_get_legacy_cache_exception(self):
        folder = self.run_process()
        status = json.loads((folder / 'status.json').read_text())
        meta_path = folder / 'runs' / status['run_id'] / 'run.json'
        meta = json.loads(meta_path.read_text())
        meta['cropper_sha256'] = 'unapproved-reader-change'
        w.save_json(meta_path, meta)
        self.assertFalse(w.cached_run(folder))

    def test_legacy_cache_keeps_manifest_and_archive_integrity_checks(self):
        folder = self.run_process()
        status = json.loads((folder / 'status.json').read_text())
        run = folder / 'runs' / status['run_id']
        meta_path = run / 'run.json'
        meta = json.loads(meta_path.read_text())
        meta['cropper_sha256'] = w.APPROVED_LEGACY_CROPPER_SHA256
        w.save_json(meta_path, meta)
        (run / 'export.zip').write_bytes((run / 'export.zip').read_bytes() + b'changed')
        with self.assertRaisesRegex(w.WorkspaceError, 'Completed export changed'):
            w.cached_run(folder)

    def test_held_attempt_is_retained_and_next_attempt_gets_new_run(self):
        args = self.args()
        folder, job = w.prepare_job(self.root, 'PAPER-02', self.question,
                                    self.final, self.combined,
                                    source_association=self.association)
        old = w.new_run(folder)
        (old / 'REFUSED.txt').write_text('Synthetic incomplete detection')
        w.record_status(folder, job, 'held', old.name, reason='Synthetic incomplete detection')
        self.run_process(args)
        new = json.loads((folder / 'status.json').read_text())['run_id']
        self.assertNotEqual(new, old.name)
        self.assertEqual((old / 'REFUSED.txt').read_text(), 'Synthetic incomplete detection')
        self.assertEqual(len(list((folder / 'runs').iterdir())), 2)

    def test_malformed_adjudication_metadata_is_rejected(self):
        folder = self.run_process()
        status = json.loads((folder / 'status.json').read_text())
        path = folder / 'runs' / status['run_id'] / 'export.zip'
        with zipfile.ZipFile(path) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        data = json.loads(contents['exam.json'])
        data['source_key']['unresolved'] = False
        contents['exam.json'] = json.dumps(data).encode()
        invalid = self.base / 'Invalid evidence.zip'
        with zipfile.ZipFile(invalid, 'w') as archive:
            for name, body in contents.items():
                archive.writestr(name, body)
        with self.assertRaisesRegex(w.WorkspaceError, 'Malformed source-key adjudication'):
            w.validate_export(invalid)


if __name__ == '__main__':
    unittest.main()
