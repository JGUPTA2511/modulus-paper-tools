"""Synthetic image jobs and simulated development-only GitHub transfers."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import portal_bridge as bridge
from test_workspace import image


class DevelopmentAPI:
    def __init__(self, *, private=True, name=bridge.REPO, conflict=None, fail_blob=False):
        self.private, self.name, self.conflict, self.fail_blob = private, name, conflict, fail_blob
        self.files, self.blobs, self.trees, self.flats, self.commits, self.calls = {}, {}, {}, {}, {}, []
        self.head = 'initial'
        self.head_trees = {self.head: self.tree({})}

    def tree(self, files):
        trie = {}
        for path, sha in files.items():
            node = trie
            parts = path.split('/')
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = sha
        def build(node):
            entries = [{'path': key, 'type': 'tree' if isinstance(value, dict) else 'blob',
                        'sha': build(value) if isinstance(value, dict) else value}
                       for key, value in sorted(node.items())]
            sha = bridge.digest(bridge.encoded(entries))
            self.trees[sha] = entries
            return sha
        sha = build(trie)
        self.flats[sha] = dict(files)
        return sha

    def check_private(self):
        self.calls.append(('privacy', 'GET'))
        if not self.private:
            raise bridge.PortalError('Private repository required')

    def call(self, endpoint, method='GET', body=None):
        self.calls.append((endpoint, method))
        if endpoint == '':
            return {'full_name': self.name, 'private': self.private, 'permissions': {'push': True}}
        if endpoint == 'git/ref/heads/development':
            return {'object': {'sha': self.head}}
        if endpoint.startswith('git/commits/'):
            return {'tree': {'sha': self.head_trees[endpoint.split('/')[-1]]}}
        if endpoint.startswith('git/trees/'):
            assert '?' not in endpoint, 'No recursive application-bank scan'
            return {'tree': self.trees[endpoint.split('/')[-1]], 'truncated': False}
        if endpoint == 'git/blobs':
            if self.fail_blob:
                self.fail_blob = False
                raise bridge.PortalError('Simulated upload outage')
            blob = base64.b64decode(body['content'])
            sha = bridge.git_digest(blob)
            self.blobs[sha] = blob
            return {'sha': sha}
        if endpoint == 'git/trees':
            files = dict(self.flats[body['base_tree']])
            files.update({item['path']: item['sha'] for item in body['tree']})
            return {'sha': self.tree(files)}
        if endpoint == 'git/commits':
            assert '[skip ci]' in body['message'] and '[skip netlify]' in body['message']
            assert body['parents'] == [self.head]
            sha = 'commit-' + str(len(self.commits))
            self.commits[sha] = body['tree']
            return {'sha': sha}
        if endpoint == 'git/refs/heads/development':
            assert body['force'] is False
            if self.conflict:
                target = self.conflict
                self.conflict = None
                self.files[target] = 'concurrent-bytes'
                self.head = 'concurrent'
                self.head_trees[self.head] = self.tree(self.files)
                raise bridge.PortalError('Simulated concurrent update')
            self.head = body['sha']
            self.head_trees[self.head] = self.commits[self.head]
            self.files = dict(self.flats[self.head_trees[self.head]])
            return {}
        raise AssertionError(endpoint)


class PortalBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'PAPER-02'
        self.run = self.folder / 'runs' / 'RUN-01'
        self.run.mkdir(parents=True)

    def job(self, *, pattern='jee-main', profile='jee-main-mock', missing=False, unresolved=False,
            no_proof=False, no_subject=False, numeric_choice=False, typed_multi=False, state='ready_for_integration'):
        job = {'schema': 'modulus_shared_paper_v1', 'paper_id': 'PAPER-02', 'inputs': {},
               'profile': profile, 'exam_intent': {'exam_pattern': pattern}}
        if profile == 'jee-main-mock':
            job['requested_scoring'] = {'correct': 4, 'incorrect': -1, 'unanswered': 0,
                                        'duration_minutes': 180, 'basis': 'owner_selected_mock_rules'}
        (self.folder / 'job.json').write_bytes(bridge.encoded(job))
        status = {'paper_id': 'PAPER-02', 'run_id': 'RUN-01', 'state': state}
        (self.folder / 'status.json').write_bytes(bridge.encoded(status))
        layout = {'basis': 'unique_complete_printed_single_column_sequence', 'expected_count': 12,
                  'question_count_detected': 12, 'first_page_index': 0, 'last_page_index': 2,
                  'continuation_page_indexes': [], 'tail_pages_verified': False}
        rows = []
        for n in range(1, 13):
            row = {'number': n, 'image': f'crops/Q{n:03d}.png', 'answer': str((n - 1) % 5),
                   'source_type': 'numerical', 'subject': ['Physics', 'Chemistry', 'Mathematics'][(n - 1) // 4],
                   'source_section': {'text': 'NUMERICAL VALUE TYPE', 'page': (n - 1) // 4 + 1}}
            if no_subject:
                row.pop('subject')
            rows.append(row)
        rows[0]['answer'] = 0
        if numeric_choice:
            rows[0].update(source_type='single_choice', answer='1')
        if typed_multi:
            rows[0].update(source_type='multiple_correct', answer=['A', 'C'])
        if missing:
            rows[0]['answer'] = None
        data = {'count': 12, 'questions': rows, 'source_key': {'unresolved': ['unknown marker'] if unresolved else []}}
        if not no_proof:
            data['source_layout'] = layout
        if missing or state == 'images_ready':
            data.update(schema='modulus_image_exam_v1', extraction_state='images_ready', grading_ready=False,
                        image_completeness={'status': 'source_sequence_complete', 'expected_count': 12,
                            'basis': 'printed_count_unique_existing_anchor_sequence_nonempty_parts'})
        boundaries = {'pages': [{'w': 600, 'h': 800} for _ in range(3)], 'questions': [
            {'number': n, 'parts': [{'page': (n - 1) // 4, 'box': [10, 20, 400, 140]}]} for n in range(1, 13)]}
        with zipfile.ZipFile(self.run / 'export.zip', 'w') as z:
            z.writestr('exam.json', json.dumps(data))
            z.writestr('boundaries.json', json.dumps(boundaries))
            for row in rows:
                z.writestr(row['image'], image())
        sha = bridge.digest((self.run / 'export.zip').read_bytes())
        (self.run / 'run.json').write_bytes(bridge.encoded({'export_sha256': sha,
            'input_manifest_sha256': bridge.digest((self.folder / 'job.json').read_bytes())}))
        (self.run / 'checks.json').write_bytes(bridge.encoded({'export_sha256': sha, 'structural_check': 'passed'}))
        return data, job, sha

    def test_source_ready_main_preserves_zero_and_originals(self):
        _, _, sha = self.job()
        original = {p: p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        report = bridge.prepare_job(self.folder)
        self.assertEqual(report['status'], 'ready_for_development')
        manifest = json.loads(Path(report['manifest_path']).read_text())
        self.assertEqual(manifest['exam_pattern'], 'jee-main')
        self.assertEqual(manifest['paper_combination'], 'PCM')
        self.assertEqual(manifest['marking_scheme']['correct'], 4)
        self.assertEqual(manifest['export_sha256'], sha)
        self.assertTrue(manifest['allow_source_key_grading'])
        self.assertFalse(manifest['validation']['answers']['evidence']['independently_math_verified'])
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        first = Path(report['manifest_path']).read_bytes()
        self.assertEqual(bridge.prepare_job(self.folder)['status'], 'ready_for_development')
        self.assertEqual(Path(report['manifest_path']).read_bytes(), first)

    def test_missing_answer_retains_images_and_holds_without_network(self):
        self.job(missing=True, state='images_ready')
        api = DevelopmentAPI()
        report = bridge.sync_job(self.folder, api)
        self.assertEqual(report['status'], 'held')
        self.assertTrue((self.run / 'export.zip').is_file())
        self.assertFalse(Path(report['manifest_path']).exists())
        self.assertEqual(api.calls, [])

    def test_images_ready_with_complete_metadata_can_progress_without_new_crops(self):
        self.job(state='images_ready')
        self.assertEqual(bridge.prepare_job(self.folder)['status'], 'ready_for_development')

    def test_unknown_subject_or_type_and_unresolved_key_remain_held(self):
        for fields in ({'no_subject': True}, {'unresolved': True}, {'no_proof': True}):
            with self.subTest(fields=fields):
                self.job(**fields)
                self.assertEqual(bridge.prepare_job(self.folder)['status'], 'held')

    def test_advanced_never_inherits_main_rules_or_duration(self):
        self.job(pattern='jee-advanced-2', profile='jee-advanced-source')
        report = bridge.prepare_job(self.folder)
        self.assertEqual(report['status'], 'held')
        self.assertTrue(any('marking' in reason for reason in report['blocking_requirements']))
        self.assertTrue(any('duration' in reason for reason in report['blocking_requirements']))

    def test_explicit_advanced_source_metadata_and_partial_rule_can_prepare(self):
        _, _, sha = self.job(pattern='jee-advanced-2', profile='jee-advanced-source')
        override = {'export_sha256': sha, 'duration_minutes': 170,
                    'duration_basis': {'basis': 'source', 'evidence': 'Printed source instructions'},
                    'paper_origin': 'institutional_mock', 'marking_scheme': {
                        'correct': 3, 'incorrect': -2, 'unanswered': 0, 'partial': {'type': 'none'}},
                    'marking_basis': {'basis': 'source', 'evidence': 'Source section rules'}}
        report = bridge.prepare_job(self.folder, override)
        self.assertEqual(report['status'], 'ready_for_development')
        manifest = json.loads(Path(report['manifest_path']).read_text())
        self.assertEqual(manifest['duration_minutes'], 170)
        self.assertEqual(manifest['marking_scheme']['correct'], 3)
        self.assertEqual(manifest['exam_pattern'], 'jee-advanced-2')

    def test_numeric_choice_key_requires_source_option_label_basis(self):
        _, _, sha = self.job(numeric_choice=True)
        self.assertEqual(bridge.prepare_job(self.folder)['status'], 'held')
        self.assertEqual(bridge.prepare_job(self.folder, {'export_sha256': sha,
            'question_metadata': [{'number': 1, 'option_key_basis': 'one_based_options'}]})['status'],
                         'ready_for_development')

    def test_explicit_multiple_correct_answers_keep_array_and_advanced_rules(self):
        data, _, sha = self.job(pattern='jee-advanced-1', profile='jee-advanced-source', typed_multi=True)
        override = {'export_sha256': sha, 'duration_minutes': 165,
                    'duration_basis': {'basis': 'source', 'evidence': 'Source duration'},
                    'paper_origin': 'institutional_mock', 'marking_scheme': {
                        'correct': 4, 'incorrect': -2, 'unanswered': 0, 'partial': {'type': 'none'}},
                    'marking_basis': {'basis': 'source', 'evidence': 'Printed source section'}}
        report = bridge.prepare_job(self.folder, override)
        self.assertEqual(report['status'], 'ready_for_development')
        with zipfile.ZipFile(report['export_path']) as archive:
            self.assertEqual(json.loads(archive.read('exam.json'))['questions'][0]['answer'], ['A', 'C'])
        self.assertEqual(data['questions'][1]['source_type'], 'numerical')

    def test_conflicting_or_unknown_types_cannot_be_relabelled_from_answer_values(self):
        _, _, sha = self.job()
        for metadata in ({'number': 1, 'source_type': 'unrecognised', 'answer_format': 'number'},
                         {'number': 1, 'source_type': 'numerical', 'answer_format': 'choice'}):
            with self.subTest(metadata=metadata):
                report = bridge.prepare_job(self.folder, {'export_sha256': sha, 'question_metadata': [metadata]})
                self.assertEqual(report['status'], 'held')

    def test_override_hash_identity_and_live_target_rejected(self):
        _, _, sha = self.job()
        for override in ({'export_sha256': 'bad'}, {'export_sha256': sha, 'paper_id': 'OTHER'},
                         {'export_sha256': sha, 'target_branch': 'main'}):
            with self.subTest(override=override), self.assertRaises(bridge.PortalError):
                bridge.prepare_job(self.folder, override)

    def test_tampered_export_or_source_run_is_rejected(self):
        self.job()
        with (self.run / 'export.zip').open('ab') as f:
            f.write(b'changed')
        with self.assertRaises(bridge.PortalError):
            bridge.prepare_job(self.folder)

    def retry_settings(self):
        _, original, _ = self.job(profile='unspecified')
        effective = deepcopy(original)
        effective.update(profile='jee-main-mock', requested_scoring={
            'correct': 4, 'incorrect': -1, 'unanswered': 0, 'duration_minutes': 180})
        root = (self.folder / 'job.json').read_bytes()
        processing = {'schema': 'modulus_run_processing_v1', 'paper_id': 'PAPER-02',
                      'input_manifest_sha256': bridge.digest(root), 'job': effective}
        body = bridge.encoded(processing)
        (self.run / 'processing.json').write_bytes(body)
        for path in (self.folder / 'status.json', self.run / 'run.json'):
            value = json.loads(path.read_text())
            value['processing_sha256'] = bridge.digest(body)
            path.write_bytes(bridge.encoded(value))
        return root

    def test_retry_snapshot_supplies_new_selected_profile_without_root_manifest_write(self):
        original = self.retry_settings()
        report = bridge.prepare_job(self.folder)
        self.assertEqual(report['status'], 'ready_for_development')
        self.assertEqual((self.folder / 'job.json').read_bytes(), original)
        self.assertEqual(json.loads(Path(report['manifest_path']).read_text())['marking_scheme']['correct'], 4)

    def test_retry_snapshot_tampering_cannot_supply_different_integration_settings(self):
        self.retry_settings()
        value = json.loads((self.run / 'processing.json').read_text())
        value['job']['requested_scoring']['correct'] = 500
        (self.run / 'processing.json').write_bytes(bridge.encoded(value))
        with self.assertRaises(bridge.PortalError):
            bridge.prepare_job(self.folder)

    def test_symlinked_portal_destination_is_rejected(self):
        self.job()
        (self.run / 'portal').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(bridge.PortalError):
            bridge.prepare_job(self.folder)

    def test_private_development_transfer_is_idempotent_and_has_no_bank_scan(self):
        self.job()
        api = DevelopmentAPI()
        first = bridge.sync_job(self.folder, api)
        self.assertEqual(first['status'], 'staged_in_development')
        self.assertEqual(first['publication'], 'not_deployed')
        self.assertEqual(first['receipt']['changed'], 2)
        self.assertEqual(set(api.files), {'paper_imports/PAPER-02/RUN-01/export.zip',
                                          'paper_imports/PAPER-02/RUN-01/integration.json'})
        second = bridge.sync_job(self.folder, api)
        self.assertEqual(second['receipt']['changed'], 0)
        self.assertEqual(len(api.commits), 1)
        self.assertFalse(any('recursive' in endpoint or 'main' in endpoint for endpoint, _ in api.calls))

    def test_private_target_and_immutable_existing_files_enforced_before_blobs(self):
        self.job()
        for api in (DevelopmentAPI(private=False), DevelopmentAPI(name='OTHER/private-repository')):
            with self.subTest(name=api.name), self.assertRaises(bridge.PortalError):
                bridge.sync_job(self.folder, api)
            self.assertFalse(any(endpoint == 'git/blobs' for endpoint, _ in api.calls))
        api = DevelopmentAPI()
        api.files['paper_imports/PAPER-02/RUN-01/export.zip'] = 'different'
        api.head_trees[api.head] = api.tree(api.files)
        with self.assertRaises(bridge.PortalError):
            bridge.sync_job(self.folder, api)
        self.assertEqual(api.blobs, {})

    def test_unrelated_concurrent_edit_rebases_but_same_import_edit_holds(self):
        self.job()
        api = DevelopmentAPI(conflict='unrelated/catalog-note.txt')
        report = bridge.sync_job(self.folder, api)
        self.assertEqual(report['status'], 'staged_in_development')
        self.assertIn('unrelated/catalog-note.txt', api.files)
        same = DevelopmentAPI(conflict='paper_imports/PAPER-02/RUN-01/export.zip')
        with self.assertRaises(bridge.PortalError):
            bridge.sync_job(self.folder, same)
        self.assertEqual(same.files['paper_imports/PAPER-02/RUN-01/export.zip'], 'concurrent-bytes')

    def test_upload_outage_retries_saved_export_without_reprocessing(self):
        self.job()
        body = (self.run / 'export.zip').read_bytes()
        api = DevelopmentAPI(fail_blob=True)
        with self.assertRaises(bridge.PortalError):
            bridge.sync_job(self.folder, api)
        self.assertEqual(json.loads((self.run / 'portal/status.json').read_text())['status'], 'upload_pending')
        self.assertEqual(bridge.sync_job(self.folder, api)['status'], 'staged_in_development')
        self.assertEqual((self.run / 'export.zip').read_bytes(), body)

    def test_uploader_rejects_main_outside_paths_and_oversize(self):
        with self.assertRaises(bridge.PortalError):
            bridge.DevelopmentUploader(DevelopmentAPI(), branch='main')
        for path, body in [('library_data/chapters/OTHER/questions.json', b'{}'),
                           ('paper_imports/PAPER-02/RUN-01/export.zip', b'12345')]:
            api = DevelopmentAPI()
            with patch.object(bridge, 'MAX_FILE', 4), self.assertRaises(bridge.PortalError):
                bridge.DevelopmentUploader(api).upload({path: body})
            self.assertEqual(api.blobs, {})


if __name__ == '__main__':
    unittest.main()
