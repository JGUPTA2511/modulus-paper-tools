"""Source/UI intent for mixed complete-paper batches; cropper/API are simulated."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_batch import pdf
from test_workspace import export, image, w


class MixedExamPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'Mixed Original Papers'
        self.source.mkdir()
        self.root = self.base / 'Modulus Papers'
        self.output = io.StringIO()
        redirect = redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.crops = []

    def paper(self, name, title):
        return pdf(self.source / name, title)

    def args(self, **changes):
        args = argparse.Namespace(source=self.source, root=self.root, profile='unspecified',
                                  offline=True, keep_color=False, paper_profile=[], all_papers=True,
                                  exam_policy='main-standard-advanced-source')
        for key, value in changes.items():
            setattr(args, key, value)
        return args

    def crop(self, command, **kwargs):
        self.crops.append(command)
        output = Path(command[command.index('--out') + 1])
        (output / 'crops').mkdir(parents=True)
        (output / 'crops/Q001.png').write_bytes(image())
        (output / 'exam.json').write_text(json.dumps({'count': 1, 'questions': [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0}]}))
        return subprocess.CompletedProcess(command, 0)

    def test_main_standard_and_both_advanced_parts_keep_distinct_patterns_and_rules(self):
        self.paper('Original Main.pdf', 'JEE Main Section A correct +4 incorrect -1 unattempted 0')
        self.paper('Original Advanced one.pdf', 'JEE Advanced Paper I Section 1 partial marks +3 -2')
        self.paper('Original Advanced two.pdf', 'JEE Advanced Paper II Section 2 partial marks +4 -1')
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            rows = w.run_batch(self.args())
        self.assertEqual(len(rows), 3)
        patterns = set()
        for row in rows:
            job = json.loads((self.root / row['paper_id'] / 'job.json').read_text())
            status = json.loads((self.root / row['paper_id'] / 'status.json').read_text())
            patterns.add(job['exam_intent']['exam_pattern'])
            self.assertFalse(status['exam_launch_ready'])
            self.assertIn('held_pending', status['integration_state'])
            self.assertFalse(row['exam_launch_ready'])
            checks = json.loads(next((self.root / row['paper_id'] / 'runs').glob('*/checks.json')).read_text())
            self.assertIn('unverified', checks['source_completeness'])
            if job['exam_intent']['exam_family'] == 'jee-main':
                self.assertEqual(job['profile'], 'jee-main-mock')
                self.assertEqual(job['requested_scoring'], {'correct': 4, 'incorrect': -1, 'unanswered': 0,
                                                           'duration_minutes': 180, 'basis': 'owner_selected_mock_rules'})
            else:
                self.assertEqual(job['profile'], 'jee-advanced-source')
                self.assertEqual(job['profile_basis'], 'source_required')
                self.assertNotIn('requested_scoring', job)
                self.assertIsNone(job['exam_intent']['verified_rules'])
                self.assertTrue(job['exam_intent']['instruction_evidence'])
        self.assertEqual(patterns, {'jee-main', 'jee-advanced-1', 'jee-advanced-2'})

    def test_unknown_source_is_not_main_from_its_filename_or_answers(self):
        self.paper('JEE Main standard paper.pdf', 'Synthetic unlabeled source, answer 1 and 0')
        entries = w._batch_entries(self.args(), {})
        self.assertEqual(entries[0]['profile'], 'unspecified')
        self.assertEqual(entries[0]['exam_intent']['exam_family'], 'unknown')
        self.assertIsNone(entries[0]['exam_intent']['exam_pattern'])
        self.assertFalse(entries[0]['exam_intent']['launch_ready'])

    def test_advanced_part_without_source_identity_does_not_default_to_paper_one(self):
        self.paper('Unknown part.pdf', 'JEE Advanced Section 1 correct marks and partial credit')
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            row = w.run_batch(self.args())[0]
        job = json.loads((self.root / row['paper_id'] / 'job.json').read_text())
        self.assertEqual(job['profile'], 'jee-advanced-source')
        self.assertIsNone(job['exam_intent']['advanced_paper'])
        self.assertIsNone(job['exam_intent']['exam_pattern'])
        self.assertNotIn('requested_scoring', job)

    def test_explicit_main_override_cannot_silently_mark_an_advanced_source(self):
        self.paper('Advanced.pdf', 'JEE Advanced Paper I Section 1 marks')
        args = self.args(paper_profile=['ADVANCED=jee-main-mock'])
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Do not crop conflicting settings')):
            row = w.run_batch(args)[0]
        self.assertEqual(row['outcome'], 'held')
        self.assertIn('conflicts', row['reason'])
        self.assertFalse((self.root / 'ADVANCED/job.json').exists())

    def test_main_only_batch_profile_cannot_override_a_known_advanced_source(self):
        self.paper('Advanced.pdf', 'JEE Advanced Paper II Section 1 marking')
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Do not crop a profile conflict')):
            row = w.run_batch(self.args(exam_policy=None, profile='jee-main-mock'))[0]
        self.assertEqual(row['outcome'], 'held')
        self.assertIn('conflicts', row['reason'])
        self.assertFalse((self.root / 'ADVANCED/job.json').exists())

    def test_prepare_job_itself_prevents_main_rules_with_advanced_intent(self):
        source = self.paper('Advanced.pdf', 'JEE Advanced Paper II')
        intent = w.inspect_exam_source(source=source)
        with self.assertRaisesRegex(w.WorkspaceError, 'Main marking cannot'):
            w.prepare_job(self.root, 'A', source, profile='jee-main-mock', exam_intent=intent)
        self.assertFalse((self.root / 'A/job.json').exists())

    def test_accepted_existing_advanced_export_keeps_multikeys_zero_and_images_unchanged(self):
        rows = [{'number': 1, 'image': 'crops/Q001.png', 'answer': 'A,C',
                 'answer_format': 'multi', 'exam_pattern': 'jee-advanced-2'},
                {'number': 2, 'image': 'crops/Q002.png', 'answer': 0,
                 'answer_format': 'number', 'exam_pattern': 'jee-advanced-2'}]
        archive = export(self.source / 'Original accepted result.zip', rows)
        original = archive.read_bytes()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Do not recrop exports')):
            result = w.run_batch(self.args())[0]
        folder = self.root / result['paper_id']
        self.assertEqual(next((folder / 'runs').glob('*/export.zip')).read_bytes(), original)
        self.assertEqual(json.loads(next((folder / 'runs').glob('*/exam.json')).read_text())['questions'], rows)
        self.assertEqual(result['required_exam_pattern'], 'jee-advanced-2')
        self.assertFalse(result['exam_launch_ready'])

    def test_legacy_main_manifest_and_completed_run_are_reused_without_rewriting(self):
        folder = self.source / 'A'
        folder.mkdir()
        paper = pdf(folder / 'questions.pdf', 'JEE Main Section A correct marks')
        args = argparse.Namespace(source=paper, root=self.root, paper_id='A', profile='jee-main-mock',
                                  key=None, solutions=None, offline=True, keep_color=False)
        with patch.object(w.subprocess, 'run', side_effect=self.crop):
            job_folder = w.process(args)
        manifest = (job_folder / 'job.json').read_bytes()
        saved = next((job_folder / 'runs').glob('*/export.zip')).read_bytes()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No repeated crops')):
            result = w.run_batch(self.args(all_papers=False))[0]
        self.assertEqual(result['outcome'], 'already processed')
        self.assertEqual((job_folder / 'job.json').read_bytes(), manifest)
        self.assertEqual(next((job_folder / 'runs').glob('*/export.zip')).read_bytes(), saved)
        self.assertEqual(len(list((job_folder / 'runs').iterdir())), 1)

    def test_loose_mixed_folder_collects_settings_once_and_queues_every_paper(self):
        self.paper('Main.pdf', 'JEE Main Section A correct marks')
        self.paper('Advanced.pdf', 'JEE Advanced Paper I Section 1 partial marks')
        with patch.object(w.Path, 'home', return_value=self.base), \
             patch('builtins.input', side_effect=['a', 'x']) as prompts, \
             patch.object(w.subprocess, 'run', side_effect=self.crop), \
             patch.object(w, 'GitHub', side_effect=AssertionError('No foreground GitHub call')):
            rows = w.interactive_batch(self.source)
        self.assertEqual(prompts.call_count, 2)
        self.assertEqual(len(self.crops), 2)
        self.assertEqual({row['exam_family'] for row in rows}, {'jee-main', 'jee-advanced'})
        self.assertEqual(sorted(path.stem for path in (self.root / '.upload_queue').glob('*.json')),
                         sorted(row['paper_id'] for row in rows))

    def test_advanced_cropper_refusal_preserves_source_and_does_not_lose_main_paper(self):
        self.paper('A advanced.pdf', 'JEE Advanced Paper II Section 1 partial marks')
        self.paper('B main.pdf', 'JEE Main Section A correct marks')
        def crop_or_refuse(command, **kwargs):
            if '/A-ADVANCED/' in command[2]:
                return subprocess.CompletedProcess(command, 2)
            return self.crop(command, **kwargs)
        with patch.object(w.subprocess, 'run', side_effect=crop_or_refuse):
            rows = w.run_batch(self.args(offline=False, queue_uploads=True))
        self.assertEqual(rows[0]['outcome'], 'held')
        self.assertEqual(rows[1]['upload_state'], 'queued')
        held = self.root / rows[0]['paper_id']
        self.assertEqual(json.loads((held / 'job.json').read_text())['profile'], 'jee-advanced-source')
        self.assertTrue((held / 'input/questions.pdf').is_file())
        self.assertTrue((self.root / '.upload_queue' / (rows[0]['paper_id'] + '.json')).is_file())

    def test_real_offline_cli_accepts_recursive_mixed_source_policy(self):
        nested = self.source / 'Subfolder'
        nested.mkdir()
        archive = export(nested / 'Original advanced result.zip', [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0,
             'answer_format': 'number', 'exam_pattern': 'jee-advanced-1'}])
        original = archive.read_bytes()
        result = subprocess.run([sys.executable, str(w.HERE / 'workspace_client.py'), 'batch', str(self.source),
                                 '--root', str(self.root), '--all-papers', '--exam-policy',
                                 'main-standard-advanced-source', '--offline'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        job = next(self.root.glob('*/job.json'))
        data = json.loads(job.read_text())
        self.assertEqual(data['profile'], 'jee-advanced-source')
        self.assertEqual(data['exam_intent']['exam_pattern'], 'jee-advanced-1')
        self.assertNotIn('requested_scoring', data)
        self.assertEqual(next(job.parent.glob('runs/*/export.zip')).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
