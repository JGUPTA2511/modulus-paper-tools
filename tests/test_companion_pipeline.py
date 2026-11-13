"""Synthetic end-to-end companion intake; actual cropper subprocess/API are mocked."""
import argparse
import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_batch import pdf
from test_workspace import FakeAPI, export, image, w


class CompanionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'Original mixed folder '
        self.source.mkdir()
        self.root = self.base / 'Modulus Papers'
        self.output = io.StringIO()
        redirect = redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.calls = []

    def files(self, pid='GTM-28', final=False, advanced=False):
        title = 'JEE (Advanced) 2026 Paper II Section 1 partial marks' if advanced else 'JEE (Main) 2026 Section A correct marks'
        question = pdf(self.source / (pid + '_QP.PDF'), title)
        combined = pdf(self.source / (pid + '_KEY-&-SOLS.PDF'), 'Synthetic key and worked solutions')
        final_file = pdf(self.source / (pid + '_FINAL-KEY.PDF'), 'Synthetic final answer table') if final else None
        return question, combined, final_file

    def args(self, **changes):
        args = argparse.Namespace(source=self.source, root=self.root, profile='unspecified',
                                  offline=True, keep_color=False, paper_profile=[],
                                  exam_policy='main-standard-advanced-source')
        for key, value in changes.items():
            setattr(args, key, value)
        return args

    def crop(self, command, **kwargs):
        self.calls.append(command)
        output = Path(command[command.index('--out') + 1])
        (output / 'crops').mkdir(parents=True)
        (output / 'crops/Q001.png').write_bytes(image())
        (output / 'exam.json').write_text(json.dumps({'count': 1, 'questions': [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0}]}))
        return subprocess.CompletedProcess(command, 0)

    def test_flat_folder_runs_each_question_paper_once_and_uses_correct_key_source(self):
        q1, c1, f1 = self.files(final=True)
        q2, c2, _ = self.files('GTM-29', advanced=True)
        before = {path.name: path.read_bytes() for path in self.source.iterdir()}
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            rows = w.run_batch(self.args())
        self.assertEqual([row['paper_id'] for row in rows], ['GTM-28', 'GTM-29'])
        self.assertEqual(len(self.calls), 2)
        for command in self.calls:
            key = Path(command[command.index('--key') + 1])
            self.assertEqual(key.name, 'answer_key.pdf')
            expected = f1 if key.parent.parent.name == 'GTM-28' else c2
            self.assertEqual(key.read_bytes(), expected.read_bytes())
        main = json.loads((self.root / 'GTM-28/job.json').read_text())
        advanced = json.loads((self.root / 'GTM-29/job.json').read_text())
        self.assertEqual(main['source_association']['answer_key_basis'], 'final_key')
        self.assertEqual(main['exam_intent']['exam_pattern'], 'jee-main')
        self.assertEqual(advanced['source_association']['answer_key_basis'], 'combined_key_solutions')
        self.assertEqual(advanced['exam_intent']['exam_pattern'], 'jee-advanced-2')
        self.assertNotIn('requested_scoring', advanced)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.source.iterdir()})
        self.assertEqual((self.root / 'GTM-28/input/solutions.pdf').read_bytes(), c1.read_bytes())
        self.assertEqual((self.root / 'GTM-29/input/answer_key.pdf').read_bytes(), c2.read_bytes())
        self.assertEqual((self.root / 'GTM-29/input/solutions.pdf').read_bytes(), c2.read_bytes())
        state = json.loads((self.root / 'GTM-29/status.json').read_text())
        self.assertEqual(state['solutions'], 'supplied_not_matched')
        self.assertFalse(state['exam_launch_ready'])

    def test_interactive_auto_groups_without_layout_or_per_file_role_questions(self):
        self.files(final=True)
        with patch.object(w.Path, 'home', return_value=self.base), \
             patch('builtins.input', side_effect=['x']) as prompts, \
             patch.object(w.subprocess, 'run', side_effect=self.crop), \
             patch.object(w, 'GitHub', side_effect=AssertionError('No foreground API')):
            rows = w.interactive_batch(self.source)
        self.assertEqual(prompts.call_count, 1)
        self.assertEqual(len(rows), 1)
        self.assertTrue((self.root / '.upload_queue/GTM-28.json').is_file())
        self.assertIn('Matched paper IDs', self.output.getvalue())

    def test_complete_file_flag_still_groups_companions_instead_of_cropping_keys(self):
        self.files()
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            rows = w.run_batch(self.args(all_papers=True))
        self.assertEqual([row['paper_id'] for row in rows], ['GTM-28'])
        self.assertEqual(len(self.calls), 1)

    def test_combined_pdf_uploads_once_and_refs_both_canonical_roles(self):
        _, combined, _ = self.files()
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            w.run_batch(self.args())
        wanted = combined.read_bytes()

        class CountAPI(FakeAPI):
            combined_posts = 0
            def call(self, endpoint, method='GET', body=None):
                if endpoint == 'git/blobs' and base64.b64decode(body['content']) == wanted:
                    self.combined_posts += 1
                return super().call(endpoint, method, body)

        api = CountAPI()
        folder = self.root / 'GTM-28'
        w.sync_job(folder, api)
        self.assertEqual(api.combined_posts, 1)
        self.assertEqual(api.files['paper_jobs/GTM-28/input/answer_key.pdf'],
                         api.files['paper_jobs/GTM-28/input/solutions.pdf'])
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No repeated crop')):
            w.run_batch(self.args())
            w.sync_job(folder, api)
        self.assertEqual(len(api.commit_trees), 1)
        self.assertEqual(api.combined_posts, 1)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)

    def test_upload_outage_and_resume_do_not_repeat_cropping_or_duplicate_jobs(self):
        self.files()
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            w.run_batch(self.args(offline=False, queue_uploads=True))
        class Outage(FakeAPI):
            def upload(self, files, message):
                raise w.WorkspaceError('Synthetic network outage')
        self.assertEqual(w.drain_queue(self.root, Outage())[0]['outcome'], 'queued')
        api = FakeAPI()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No recropping')):
            self.assertEqual(w.drain_queue(self.root, api)[0]['outcome'], 'synced')
            self.assertEqual(w.drain_queue(self.root, api), [])
        self.assertEqual(len(api.commit_trees), 1)
        self.assertEqual(len(self.calls), 1)

    def test_changed_final_key_cannot_replace_original_or_completed_run(self):
        _, _, final = self.files(final=True)
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            w.run_batch(self.args())
        folder = self.root / 'GTM-28'
        original = (folder / 'input/answer_key.pdf').read_bytes()
        pdf(final, 'Changed final answer table')
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Do not recrop changed originals')):
            row = w.run_batch(self.args())[0]
        self.assertEqual(row['outcome'], 'held')
        self.assertIn('different files or settings', row['reason'])
        self.assertEqual((folder / 'input/answer_key.pdf').read_bytes(), original)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)

    def test_existing_export_and_all_originals_are_preserved_without_recropping(self):
        self.files(final=True)
        archive = export(self.source / 'GTM-28_RESULT.ZIP')
        original = archive.read_bytes()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No cropper for existing export')):
            row = w.run_batch(self.args())[0]
        folder = self.root / row['paper_id']
        self.assertEqual(next((folder / 'runs').glob('*/export.zip')).read_bytes(), original)
        self.assertEqual(len(list((folder / 'input').glob('*.pdf'))), 3)
        data = json.loads(next((folder / 'runs').glob('*/exam.json')).read_text())
        self.assertEqual(data['questions'][0]['answer'], '0')

    def test_ambiguous_parenthesized_pdf_is_not_overruled_by_main_export_metadata(self):
        question, _, _ = self.files()
        pdf(question, 'JEE (Main) and JEE (Advanced) Section 1 marks')
        export(self.source / 'GTM-28_RESULT.ZIP', [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0, 'exam_pattern': 'jee-main'}])
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Existing export stays unchanged')):
            row = w.run_batch(self.args())[0]
        job = json.loads((self.root / 'GTM-28/job.json').read_text())
        self.assertEqual(job['exam_intent']['exam_family'], 'unknown')
        self.assertIsNone(job['exam_intent']['exam_pattern'])
        self.assertNotIn('requested_scoring', job)
        self.assertFalse(row['exam_launch_ready'])

    def test_legacy_manifest_bytes_and_crops_survive_new_association_annotations(self):
        q, combined, _ = self.files()
        folder, job = w.prepare_job(self.root, 'GTM-28', q, combined, combined)
        (folder / 'job.json').write_text(json.dumps(job, separators=(',', ':')))
        raw_manifest = (folder / 'job.json').read_bytes()
        args = argparse.Namespace(source=q, root=self.root, paper_id='GTM-28', key=combined,
                                  solutions=combined, profile='unspecified', offline=True, keep_color=False)
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            w.process(args)
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Reuse immutable previous run')):
            result = w.run_batch(self.args(exam_policy=None))[0]
        self.assertEqual(result['outcome'], 'already processed')
        self.assertEqual((folder / 'job.json').read_bytes(), raw_manifest)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
