"""Synthetic batch regressions. GitHub and cropper calls are simulated explicitly."""
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

from test_workspace import FakeAPI, export, image, w


def pdf(path, text='Synthetic fixture'):
    """Generate a small valid PDF, with no external fixture files or real papers."""
    content = ('BT /F1 12 Tf 20 60 Td (' + text + ') Tj ET').encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 120 100] '
               b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream']
    body = b'%PDF-1.4\n'
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += str(number).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    start = len(body)
    body += b'xref\n0 6\n0000000000 65535 f \n'
    body += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    body += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n'
    path.write_bytes(body)
    return path


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.source = self.base / 'Modulus Batch With Spaces'
        self.source.mkdir()
        self.root = self.base / 'Modulus Papers With Spaces'
        self.console = io.StringIO()
        self.redirect = redirect_stdout(self.console)
        self.redirect.__enter__()
        self.crop_calls = []

    def tearDown(self):
        self.redirect.__exit__(None, None, None)
        self.tmp.cleanup()

    def paper(self, pid, roles=('questions.pdf',)):
        folder = self.source / pid
        folder.mkdir()
        for role in roles:
            pdf(folder / role, role)
        return folder

    def args(self, offline=True, **extra):
        return argparse.Namespace(source=self.source, root=self.root, profile='unspecified',
                                  offline=offline, keep_color=False, paper_profile=[], **extra)

    def cropper(self, command, **kwargs):
        self.crop_calls.append(command)
        output = Path(command[command.index('--out') + 1])
        (output / 'crops').mkdir(parents=True)
        (output / 'crops/Q001.png').write_bytes(image())
        (output / 'exam.json').write_text(json.dumps({'count': 1, 'questions': [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0,
             'source_section': 'numerical', 'passage': 'Synthetic passage retained'}]}))
        return subprocess.CompletedProcess(command, 0)

    def test_discovery_associates_multiple_separate_and_embedded_keys_read_only(self):
        self.paper('GTM-02', ('questions.pdf', 'answer_key.pdf', 'solutions.pdf'))
        self.paper('GTM-03')
        folder = self.paper('GTM-04', ())
        export(folder / 'export.zip')
        entries = w.discover_batch(self.source, self.root)
        self.assertEqual([e['paper_id'] for e in entries], ['GTM-02', 'GTM-03', 'GTM-04'])
        self.assertTrue(all(e['reason'] is None for e in entries))
        self.assertEqual(set(entries[0]['files']), {'questions.pdf', 'answer_key.pdf', 'solutions.pdf'})
        self.assertEqual(set(entries[1]['files']), {'questions.pdf'})
        self.assertEqual(set(entries[2]['files']), {'export.zip'})
        self.assertFalse(self.root.exists(), 'Preflight must not create job folders')

    def test_multiple_papers_crop_sequentially_with_correct_key_associations(self):
        self.paper('GTM-02', ('questions.pdf', 'answer_key.pdf', 'solutions.pdf'))
        self.paper('GTM-03')
        with patch.object(w.subprocess, 'run', side_effect=self.cropper):
            rows = w.run_batch(self.args())
        self.assertEqual([r['outcome'] for r in rows], ['upload failed/local-only'] * 2)
        self.assertEqual(len(self.crop_calls), 2)
        self.assertIn('--key', self.crop_calls[0])
        self.assertNotIn('--key', self.crop_calls[1])
        first = json.loads((self.root / 'GTM-02/job.json').read_text())
        self.assertEqual(set(first['inputs']), {'questions', 'answer_key', 'solutions'})
        state = json.loads((self.root / 'GTM-02/status.json').read_text())
        self.assertEqual(state['solutions'], 'supplied_not_matched')
        self.assertEqual(state['publication'], 'not_deployed')
        exam = json.loads(next((self.root / 'GTM-02/runs').glob('*/exam.json')).read_text())
        self.assertEqual(exam['questions'][0]['answer'], 0)
        self.assertEqual(exam['questions'][0]['passage'], 'Synthetic passage retained')

    def test_existing_export_with_optional_originals_is_reused_byte_for_byte(self):
        folder = self.paper('GTM-04', ('questions.pdf', 'answer_key.pdf', 'solutions.pdf'))
        archive = export(folder / 'export.zip', [
            {'number': 1, 'image': 'crops/Q001.png', 'answer': 0, 'source_type': 'numerical'},
            {'number': 2, 'image': 'crops/Q002.png', 'answer': '1', 'source_type': 'numerical'},
            {'number': 3, 'image': 'crops/Q003.png', 'answer': '2.50', 'source_type': 'numerical'}])
        original = archive.read_bytes()
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No recropping allowed')):
            first = w.run_batch(self.args())
            second = w.run_batch(self.args())
        self.assertEqual(first[0]['outcome'], 'upload failed/local-only')
        self.assertEqual(second[0]['outcome'], 'already processed')
        runs = list((self.root / 'GTM-04/runs').iterdir())
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0] / 'export.zip').read_bytes(), original)
        exam = json.loads((runs[0] / 'exam.json').read_text())
        self.assertEqual([r['answer'] for r in exam['questions']], [0, '1', '2.50'])
        self.assertEqual([r['source_type'] for r in exam['questions']], ['numerical'] * 3)
        self.assertEqual((self.root / 'GTM-04/input/solutions.pdf').read_bytes(), (folder / 'solutions.pdf').read_bytes())

    def test_profiles_are_explicit_and_per_paper_override_is_preserved(self):
        self.paper('A')
        folder = self.paper('B')
        (folder / 'paper.json').write_text(json.dumps({'profile': 'unspecified'}))
        default = w.discover_batch(self.source, self.root)
        self.assertTrue(all(e['profile'] == 'unspecified' for e in default))
        entries = w.discover_batch(self.source, self.root, 'jee-main-mock')
        self.assertEqual([e['profile'] for e in entries], ['jee-main-mock', 'unspecified'])
        overridden = w.discover_batch(self.source, self.root, overrides={'B': 'jee-main-mock'})
        self.assertEqual(overridden[1]['profile'], 'jee-main-mock')
        with self.assertRaises(w.WorkspaceError):
            w.discover_batch(self.source, self.root, overrides={'UNKNOWN': 'unspecified'})

    def test_duplicate_normalized_ids_hold_all_related_folders_before_processing(self):
        self.paper('Paper A')
        self.paper('PAPER-A')
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all('Duplicate paper ID' in e['reason'] for e in entries))
        with patch.object(w, 'process', side_effect=AssertionError('Do not process duplicate IDs')):
            rows = w.run_batch(self.args(), entries)
        self.assertTrue(all(r['outcome'] == 'held' for r in rows))
        self.assertFalse((self.root / 'PAPER-A').exists())

    def test_duplicate_configured_ids_are_held(self):
        for name in ('A', 'B'):
            folder = self.paper(name)
            (folder / 'paper.json').write_text(json.dumps({'paper_id': 'SAME'}))
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all('Duplicate paper ID SAME' in e['reason'] for e in entries))

    def test_changed_original_is_held_without_overwriting_prior_run(self):
        folder = self.paper('A')
        with patch.object(w.subprocess, 'run', side_effect=self.cropper):
            w.run_batch(self.args())
            first_runs = list((self.root / 'A/runs').iterdir())
            preserved = (self.root / 'A/input/questions.pdf').read_bytes()
            pdf(folder / 'questions.pdf', 'Changed source')
            rows = w.run_batch(self.args())
        self.assertEqual(rows[0]['outcome'], 'held')
        self.assertIn('different files or settings', rows[0]['reason'])
        self.assertEqual((self.root / 'A/input/questions.pdf').read_bytes(), preserved)
        self.assertEqual(list((self.root / 'A/runs').iterdir()), first_runs)
        self.assertEqual(len(self.crop_calls), 1)

    def test_spaces_in_finder_folder_paths(self):
        self.assertEqual(w.path_from_prompt(str(self.source).replace(' ', '\\ ')), self.source)
        self.assertEqual(w.path_from_prompt('"' + str(self.source) + '"'), self.source)
        self.paper('Paper A')
        entries = w.discover_batch(self.source, self.root)
        self.assertEqual(entries[0]['paper_id'], 'PAPER-A')
        self.assertIsNone(entries[0]['reason'])

    def test_ambiguous_names_duplicate_roles_and_nested_folders_are_held(self):
        a = self.paper('A')
        pdf(a / 'another question paper.pdf')
        b = self.paper('B')
        pdf(b / 'QUESTIONS.PDF')
        c = self.paper('C')
        (c / 'nested').mkdir()
        entries = w.discover_batch(self.source, self.root)
        self.assertIn('Ambiguous input', entries[0]['reason'])
        self.assertIn('Duplicate input role', entries[1]['reason'])
        self.assertIn('nested', entries[2]['reason'])

    def test_corrupt_oversized_and_unsafe_inputs_are_held_before_processing(self):
        corrupt = self.paper('A')
        (corrupt / 'questions.pdf').write_bytes(b'%PDF-1.7\nnot actually a pdf')
        oversized = self.paper('B')
        with (oversized / 'questions.pdf').open('wb') as stream:
            stream.truncate(w.MAX_FILE + 1)
        archive = self.paper('C', ())
        (archive / 'export.zip').write_bytes(b'not a zip')
        unsafe = self.paper('D', ())
        export(unsafe / 'export.zip', extra={'../escape': b'unsafe'})
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all(e['reason'] for e in entries))
        self.assertIn('corrupt', entries[0]['reason'])
        self.assertIn('24 MiB', entries[1]['reason'])
        self.assertIn('Invalid export', entries[2]['reason'])
        self.assertIn('Unsafe archive path', entries[3]['reason'])
        self.assertFalse(self.root.exists())

    def test_symlink_inputs_and_unsafe_ids_are_held(self):
        external = pdf(self.base / 'external.pdf')
        linked = self.paper('A', ())
        (linked / 'questions.pdf').symlink_to(external)
        configured = self.paper('B')
        (configured / 'paper.json').write_text(json.dumps({'paper_id': '../ESCAPE'}))
        self.source.joinpath('C').symlink_to(configured, target_is_directory=True)
        entries = w.discover_batch(self.source, self.root)
        self.assertTrue(all(e['reason'] for e in entries))
        self.assertIn('Symlinks', entries[0]['reason'])
        self.assertIn('Paper ID', entries[1]['reason'])
        self.assertIn('symlinks', entries[2]['reason'])

    def test_input_output_overlap_and_symlink_batch_root_are_refused(self):
        self.paper('A')
        for root in (self.source, self.source / 'generated', self.base):
            with self.assertRaises(w.WorkspaceError):
                w.discover_batch(self.source, root)
        linked = self.base / 'batch-link'
        linked.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(w.WorkspaceError):
            w.discover_batch(linked, self.root)

    def test_one_cropper_failure_does_not_stop_other_papers(self):
        self.paper('A')
        self.paper('B')
        def crop(command, **kwargs):
            if '/A/' in command[2]:
                output = Path(command[command.index('--out') + 1])
                output.mkdir(parents=True)
                (output / 'REFUSED.txt').write_text('Synthetic missing key')
                self.crop_calls.append(command)
                return subprocess.CompletedProcess(command, 1)
            return self.cropper(command, **kwargs)
        with patch.object(w.subprocess, 'run', side_effect=crop):
            rows = w.run_batch(self.args())
        self.assertEqual([r['outcome'] for r in rows], ['held', 'upload failed/local-only'])
        self.assertIn('Synthetic missing key', rows[0]['reason'])
        self.assertEqual(len(self.crop_calls), 2)
        self.assertTrue(list((self.root / 'B/runs').glob('*/export.zip')))

    def test_upload_failure_retry_and_duplicate_prevention_without_recropping(self):
        self.paper('A')
        api = FakeAPI()
        with patch.object(w.subprocess, 'run', side_effect=self.cropper), patch.object(w, 'GitHub', return_value=api):
            with patch.object(api, 'upload', side_effect=w.WorkspaceError('Synthetic upload outage')):
                failed = w.run_batch(self.args(offline=False))
            retried = w.run_batch(self.args(offline=False))
            repeated = w.run_batch(self.args(offline=False))
        self.assertEqual(failed[0]['outcome'], 'upload failed/local-only')
        self.assertEqual(retried[0]['outcome'], 'synced')
        self.assertEqual(repeated[0]['outcome'], 'already processed')
        self.assertEqual(len(self.crop_calls), 1)
        self.assertEqual(len(list((self.root / 'A/runs').iterdir())), 1)
        self.assertEqual(len(api.commit_trees), 1)
        self.assertEqual(json.loads((self.root / 'A/status.json').read_text())['publication'], 'not_deployed')

    def test_private_target_and_same_path_conflict_do_not_stop_next_paper(self):
        self.paper('A')
        self.paper('B')
        api = FakeAPI(private=False)
        with patch.object(w.subprocess, 'run', side_effect=self.cropper), patch.object(w, 'GitHub', return_value=api):
            rows = w.run_batch(self.args(offline=False))
        self.assertTrue(all(r['outcome'] == 'upload failed/local-only' for r in rows))
        self.assertEqual(len(self.crop_calls), 2)
        self.assertFalse(api.blobs)
        api = FakeAPI(conflict='paper_jobs/A/status.json')
        with patch.object(w, 'GitHub', return_value=api):
            rows = w.run_batch(self.args(offline=False))
        self.assertEqual(rows[0]['outcome'], 'upload failed/local-only')
        self.assertEqual(rows[1]['outcome'], 'synced')
        self.assertEqual(api.files['paper_jobs/A/status.json'], 'someone-elses-version')

    def test_changed_snapshot_after_preflight_is_held(self):
        folder = self.paper('A')
        entries = w.discover_batch(self.source, self.root)
        pdf(folder / 'questions.pdf', 'Changed after discovery')
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Do not process changed inputs')):
            rows = w.run_batch(self.args(), entries)
        self.assertEqual(rows[0]['outcome'], 'held')
        self.assertIn('Input changed after preflight', rows[0]['reason'])

    def test_real_cli_batch_export_and_machine_readable_report(self):
        folder = self.paper('CLI-01', ())
        export(folder / 'export.zip')
        result = subprocess.run([sys.executable, str(w.HERE / 'workspace_client.py'), 'batch',
                                 str(self.source), '--root', str(self.root), '--offline'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)  # local-only is intentionally incomplete sync
        self.assertIn('CLI-01: upload failed/local-only', result.stdout)
        report = json.loads(next((self.root / '.batch_reports').glob('*.json')).read_text())
        self.assertEqual(report['papers'][0]['paper_id'], 'CLI-01')
        self.assertEqual(report['publication'], 'not_deployed')


    def test_custom_id_for_folder_with_punctuation_and_invalid_config_isolation(self):
        folder = self.paper('Paper (Algebra)')
        (folder / 'paper.json').write_text(json.dumps({'paper_id': 'ALGEBRA-01'}))
        other = self.paper('B')
        (other / 'paper.json').write_text(json.dumps({'paper_id': None}))
        entries = w.discover_batch(self.source, self.root)
        mapped = {e['paper_id']: e for e in entries}
        self.assertIsNone(mapped['ALGEBRA-01']['reason'])
        self.assertIsNotNone(mapped['B']['reason'])
        args = self.args()
        args.paper_profile = ['UNKNOWN=unspecified']
        with self.assertRaises(w.WorkspaceError):
            w.run_batch(args)
        self.assertFalse(self.root.exists())

    def test_oversized_export_is_held(self):
        folder = self.paper('A', ())
        with (folder / 'export.zip').open('wb') as stream:
            stream.truncate(w.MAX_FILE + 1)
        entries = w.discover_batch(self.source, self.root)
        self.assertIn('24 MiB', entries[0]['reason'])
        self.assertFalse(self.root.exists())

    def test_output_job_symlink_refused_and_overlap_through_alias_detected(self):
        self.paper('A')
        self.root.mkdir()
        external = self.base / 'external-job'
        external.mkdir()
        (self.root / 'A').symlink_to(external, target_is_directory=True)
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Do not crop through symlink')):
            rows = w.run_batch(self.args())
        self.assertEqual(rows[0]['outcome'], 'held')
        self.assertIn('Symlink job', rows[0]['reason'])
        self.assertEqual(list(external.iterdir()), [])
        alias = self.base / 'input-alias'
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(w.WorkspaceError):
            w.discover_batch(self.source, alias / 'outputs')

    def test_unexpected_one_paper_exception_does_not_abort_batch(self):
        self.paper('A')
        self.paper('B')
        actual_process = w.process
        def unexpected(args):
            if args.paper_id == 'A':
                raise RuntimeError('Synthetic unexpected per-paper failure')
            return actual_process(args)
        with patch.object(w, 'process', side_effect=unexpected), patch.object(w.subprocess, 'run', side_effect=self.cropper):
            rows = w.run_batch(self.args())
        self.assertEqual([r['outcome'] for r in rows], ['held', 'upload failed/local-only'])
        self.assertIn('unexpected per-paper failure', rows[0]['reason'])
        self.assertEqual(len(self.crop_calls), 1)

    def test_interactive_folder_prompts_once_for_shared_profile_and_opens_no_tabs(self):
        self.paper('A')
        folder = self.paper('B')
        (folder / 'paper.json').write_text(json.dumps({'profile': 'unspecified'}))
        api = FakeAPI()
        with patch.object(w.Path, 'home', return_value=self.base), \
             patch('builtins.input', side_effect=[str(self.source).replace(' ', '\\ '), 'y', 'q']) as prompts, \
             patch.object(w.subprocess, 'run', side_effect=self.cropper), \
             patch.object(w, 'GitHub', return_value=api), \
             patch.object(w.sys, 'platform', 'darwin'):
            w.interactive()
        output_root = self.base / 'Modulus Papers'
        self.assertEqual(prompts.call_count, 3)
        self.assertEqual(len(self.crop_calls), 2)
        self.assertEqual(json.loads((output_root / 'A/job.json').read_text())['profile'], 'jee-main-mock')
        self.assertEqual(json.loads((output_root / 'B/job.json').read_text())['profile'], 'unspecified')
        self.assertFalse(any(command[0] == 'open' for command in self.crop_calls))

    def test_interactive_mixed_profile_selection(self):
        for pid in ('A', 'B'):
            folder = self.paper(pid, ())
            export(folder / 'export.zip')
        api = FakeAPI()
        with patch.object(w.Path, 'home', return_value=self.base), \
             patch('builtins.input', side_effect=[str(self.source), 'm', 'y', 'n', 'q']) as prompts, \
             patch.object(w, 'GitHub', return_value=api), \
             patch.object(w.subprocess, 'run', side_effect=AssertionError('Exports must not recrop')):
            w.interactive()
        output_root = self.base / 'Modulus Papers'
        self.assertEqual(prompts.call_count, 5)
        self.assertEqual(json.loads((output_root / 'A/job.json').read_text())['profile'], 'jee-main-mock')
        self.assertEqual(json.loads((output_root / 'B/job.json').read_text())['profile'], 'unspecified')



    def test_symlink_runs_directory_is_held_without_writing_outside_job(self):
        self.paper('A')
        (self.root / 'A').mkdir(parents=True)
        external = self.base / 'external-runs'
        external.mkdir()
        (self.root / 'A/runs').symlink_to(external, target_is_directory=True)
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('Unsafe output must not run cropper')):
            rows = w.run_batch(self.args())
        self.assertEqual(rows[0]['outcome'], 'held')
        self.assertIn('Symlink', rows[0]['reason'])
        self.assertEqual(list(external.iterdir()), [])



    def test_report_directory_symlink_refused_before_jobs_are_created(self):
        self.paper('A')
        self.root.mkdir()
        outside = self.base / 'external-reports'
        outside.mkdir()
        (self.root / '.batch_reports').symlink_to(outside, target_is_directory=True)
        with patch.object(w, 'process', side_effect=AssertionError('Unsafe reports must stop before processing')):
            with self.assertRaisesRegex(w.WorkspaceError, 'Unsafe batch report directory'):
                w.run_batch(self.args())
        self.assertFalse((self.root / 'A').exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_added_input_or_replaced_folder_after_preflight_is_held(self):
        first = self.paper('A')
        second = self.paper('B')
        entries = w.discover_batch(self.source, self.root)
        pdf(first / 'extra-paper.pdf')
        outside = self.base / 'moved-input'
        second.rename(outside)
        second.symlink_to(outside, target_is_directory=True)
        with patch.object(w, 'process', side_effect=AssertionError('Do not process changed input inventory')):
            rows = w.run_batch(self.args(), entries)
        self.assertEqual([r['outcome'] for r in rows], ['held', 'held'])
        self.assertTrue(all('Input folder changed after preflight' in r['reason'] for r in rows))
        self.assertFalse((self.root / 'A').exists())
        self.assertFalse((self.root / 'B').exists())


if __name__ == '__main__':
    unittest.main()
