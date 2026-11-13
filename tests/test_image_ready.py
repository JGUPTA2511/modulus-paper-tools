"""Synthetic image-only extraction and explicitly typed source-answer regressions."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from test_batch import pdf
from test_workspace import FakeAPI, image, w


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('image_ready_cropper', ROOT / 'question_boundaries.py')
cropper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cropper)


def image_export(path, rows, *, image_only=False, **fields):
    data = {'count': len(rows), 'questions': rows}
    if image_only:
        data.update(schema='modulus_image_exam_v1', extraction_state='images_ready',
                    grading_ready=False,
                    image_completeness={'status': 'source_sequence_complete',
                                        'expected_count': len(rows),
                                        'basis': 'printed_count_unique_existing_anchor_sequence_nonempty_parts'},
                    grading_issues=['Source answers remain unavailable.'])
    data.update(fields)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('exam.json', json.dumps(data))
        for row in rows:
            archive.writestr(row['image'], image())
    return path


def row(number, answer, **metadata):
    return {'number': number, 'image': f'crops/Q{number:03d}.png',
            'answer': answer, **metadata}


def detected_fixture():
    pages = [{'file': 'p01.png', 'w': 40, 'h': 40, 'source': 'text', 'ncols': 1}]
    questions = [{'number': number, 'parts': [{'page': 0, 'box': [1, 1, 30, 30],
                                               'continuation': False}],
                  'flags': [], 'options_found': 0} for number in range(1, 4)]
    layout = {'expected_count': 3,
              'basis': 'unique_complete_printed_single_column_sequence',
              'anchors_by_page': [[{'n': n} for n in range(1, 4)]]}
    types = {n: {'source_type': 'numerical',
                 'source_section': {'page': 1, 'text': 'NUMERICAL VALUE TYPE',
                                    'basis': 'explicit_printed_section_heading'}}
             for n in range(1, 4)}
    return pages, questions, layout, types


class ImageReadyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.output = io.StringIO()
        redirect = redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def test_explicit_multiple_correct_array_is_preserved_without_type_inference(self):
        answers = ['A', 'c']
        rows = [row(1, answers, source_type='multiple_correct'),
                row(2, 0, source_type='numerical'),
                row(3, '1', source_type='numerical')]
        data, check = w.validate_export(image_export(self.base / 'typed.zip', rows))
        self.assertEqual(data['questions'][0]['answer'], answers)
        self.assertEqual(data['questions'][1]['answer'], 0)
        self.assertEqual(data['questions'][2]['source_type'], 'numerical')
        self.assertEqual(check['answers'], 3)

    def test_untyped_or_numerical_arrays_remain_invalid(self):
        for metadata in ({}, {'source_type': 'numerical'}, {'source_type': 'single_choice'}):
            with self.subTest(metadata=metadata), self.assertRaises(w.WorkspaceError):
                w.validate_export(image_export(self.base / 'bad.zip', [row(1, [1, 3], **metadata)]))

    def test_multi_correct_duplicate_empty_and_invalid_options_are_rejected(self):
        for answer in ([], ['A', 'a'], [1, 'A'], [0, 2], [True, 2], [1.0, 2], ['A', 'E']):
            with self.subTest(answer=answer), self.assertRaises(w.WorkspaceError):
                w.validate_export(image_export(self.base / 'bad.zip',
                                              [row(1, answer, source_type='multiple_correct')]))

    def test_conflicting_explicit_question_formats_are_held(self):
        with self.assertRaisesRegex(w.WorkspaceError, 'Conflicting'):
            w.validate_export(image_export(self.base / 'bad.zip', [
                row(1, [1, 3], source_type='numerical', question_type='multiple_correct')]))

    def test_legacy_export_still_requires_each_source_answer(self):
        with self.assertRaises(w.WorkspaceError):
            w.validate_export(image_export(self.base / 'legacy.zip', [row(1, None)]))

    def test_keyless_export_requires_explicit_ungradeable_image_schema(self):
        rows = [row(1, None, source_type='numerical'), row(2, 0, source_type='numerical')]
        data, check = w.validate_export(image_export(self.base / 'images.zip', rows, image_only=True))
        self.assertIsNone(data['questions'][0]['answer'])
        self.assertEqual(check['images'], 2)
        self.assertEqual(check['answers'], 1)
        self.assertEqual(check['missing_source_answers'], [1])
        self.assertFalse(check['gradeable'])
        self.assertFalse(check['grading_ready'])
        self.assertFalse(check['exam_launch_ready'])

    def test_bad_image_ready_declarations_are_rejected(self):
        for fields in ({'schema': 'another-schema'}, {'grading_ready': True},
                       {'image_completeness': {}},
                       {'image_completeness': {'status': 'source_sequence_complete', 'expected_count': 1}},
                       {'image_completeness': {'status': 'source_sequence_complete', 'expected_count': 99}}):
            with self.subTest(fields=fields), self.assertRaises(w.WorkspaceError):
                w.validate_export(image_export(self.base / 'bad.zip', [row(1, None)],
                                              image_only=True, **fields))

    def test_source_numeric_sections_allow_image_only_extraction_without_options_or_keys(self):
        pages, questions, layout, types = detected_fixture()
        problems, grading = cropper.image_first_gate(pages, questions, {}, layout, types)
        self.assertEqual(problems, [])
        self.assertTrue(grading)
        self.assertEqual([q['number'] for q in questions], [1, 2, 3])

    def test_source_multiple_correct_sections_allow_image_only_extraction(self):
        pages, questions, layout, types = detected_fixture()
        for value in types.values():
            value['source_type'] = 'multiple_correct'
        problems, grading = cropper.image_first_gate(pages, questions, {}, layout, types)
        self.assertEqual(problems, [])
        self.assertTrue(grading)

    def test_count_without_source_sequence_proof_cannot_unlock_images(self):
        pages, questions, layout, types = detected_fixture()
        for value in (None, {'expected_count': 3}, {**layout, 'anchors_by_page': []}):
            with self.subTest(layout=value):
                self.assertTrue(cropper.image_first_gate(pages, questions, {}, value, types)[0])

    def test_missing_number_or_empty_part_still_holds_extraction(self):
        pages, questions, layout, types = detected_fixture()
        self.assertTrue(cropper.image_first_gate(pages, questions[1:], {}, layout, types)[0])
        questions[0]['parts'] = []
        self.assertTrue(cropper.image_first_gate(pages, questions, {}, layout, types)[0])

    def test_unknown_low_option_formats_are_not_assumed_numerical(self):
        pages, questions, layout, _ = detected_fixture()
        self.assertTrue(cropper.image_first_gate(pages, questions, {}, layout, {})[0])

    def run_cropper(self, *, images_first, kind='numerical', labels=None, key=None):
        original = self.base / 'Synthetic source.pdf'
        original.write_bytes(b'%PDF-1.4\nsynthetic only')
        out = self.base / ('Image output' if images_first else 'Legacy output')
        pages, questions, layout, types = detected_fixture()
        for record in types.values():
            record['source_type'] = kind
        if labels is not None:
            for question in questions:
                question['option_labels'] = list(labels)
                question['options_found'] = len(labels)

        def render(source, directory, dpi):
            directory.mkdir(parents=True)
            file = directory / 'p01.png'
            Image.new('RGB', (40, 40), 'white').save(file)
            return [file]

        def detect(source, files, args):
            args._source_layout = deepcopy(layout)
            args._source_types = deepcopy(types)
            return deepcopy(pages), deepcopy(questions), key if key is not None else {1: 0, 2: '1'}

        arguments = ['question_boundaries.py', str(original), '--out', str(out),
                     '--source-compat', '--no-clean'] + (['--images-first'] if images_first else [])
        with patch.object(sys, 'argv', arguments), patch.object(cropper, 'render_pdf', side_effect=render), \
             patch.object(cropper, 'detect', side_effect=detect), \
             patch.object(cropper, 'draw_previews'):
            cropper.main()
        return out

    def test_opt_in_cli_creates_real_synthetic_crops_and_retains_missing_answer(self):
        out = self.run_cropper(images_first=True)
        data = json.loads((out / 'exam.json').read_text())
        self.assertEqual(data['extraction_state'], 'images_ready')
        self.assertEqual([q['answer'] for q in data['questions']], [0, '1', None])
        self.assertTrue(all(q['source_type'] == 'numerical' for q in data['questions']))
        self.assertEqual(len(list((out / 'crops').glob('*.png'))), 3)
        self.assertFalse((out / 'REFUSED.txt').exists())
        archive = self.base / 'synthetic.zip'
        w.package_output(out, archive)
        self.assertFalse(w.validate_export(archive)[1]['gradeable'])

    def test_default_cropper_retains_legacy_refusal(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run_cropper(images_first=False)
        self.assertEqual(stopped.exception.code, 2)
        self.assertFalse((self.base / 'Legacy output/crops').exists())

    def test_choice_basis_requires_explicit_source_type_and_actual_option_labels(self):
        out = self.run_cropper(images_first=True, kind='single_choice',
                               labels=['1', '2', '3', '4'], key={1: '1', 2: '2', 3: '3'})
        data = json.loads((out / 'exam.json').read_text())
        self.assertTrue(all(q['option_key_basis'] == 'one_based_options' for q in data['questions']))
        self.assertTrue(all(q['option_labels'] == ['1', '2', '3', '4'] for q in data['questions']))

    def test_numerical_answer_one_does_not_acquire_choice_basis(self):
        out = self.run_cropper(images_first=True, labels=['1', '2', '3', '4'],
                               key={1: '1', 2: '2', 3: 0})
        data = json.loads((out / 'exam.json').read_text())
        self.assertEqual(data['questions'][0]['answer'], '1')
        self.assertTrue(all(q['source_type'] == 'numerical' for q in data['questions']))
        self.assertTrue(all('option_key_basis' not in q for q in data['questions']))

    def test_image_only_job_is_cached_and_upload_retry_never_recrops(self):
        question = pdf(self.base / 'Original paper.pdf')
        root = self.base / 'Generated jobs'
        args = argparse.Namespace(source=question, key=None, solutions=None,
                                  root=root, paper_id='PAPER-01', profile='unspecified',
                                  offline=True, keep_color=False, no_review=True)
        commands = []

        def crop(command, **kwargs):
            commands.append(command)
            out = Path(command[command.index('--out') + 1])
            (out / 'crops').mkdir(parents=True)
            (out / 'crops/Q001.png').write_bytes(image())
            with zipfile.ZipFile(image_export(self.base / 'temporary.zip',
                                             [row(1, None, source_type='numerical')],
                                             image_only=True)) as archive:
                (out / 'exam.json').write_bytes(archive.read('exam.json'))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(w.subprocess, 'run', side_effect=crop):
            folder = w.process(args)
            first = json.loads((folder / 'status.json').read_text())
            export_path = folder / 'runs' / first['run_id'] / 'export.zip'
            before = export_path.read_bytes()
            w.process(args)
        self.assertEqual(len(commands), 1)
        self.assertIn('--images-first', commands[0])
        self.assertEqual(first['state'], 'images_ready')
        self.assertFalse(first['gradeable'])
        self.assertEqual(first['publication'], 'not_deployed')
        api = FakeAPI()
        with patch.object(api, 'upload', side_effect=w.WorkspaceError('Synthetic network outage')):
            with self.assertRaises(w.WorkspaceError):
                w.sync_job(folder, api)
        self.assertTrue((root / '.upload_queue/PAPER-01.json').is_file())
        results = w.drain_queue(root, api)
        self.assertEqual(results[0]['outcome'], 'synced')
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)
        self.assertEqual(export_path.read_bytes(), before)
        self.assertTrue(any(path.endswith('/export.zip') for path in api.files))
        w.sync_job(folder, api)
        self.assertEqual(len(api.commit_trees), 1)

    def test_completed_previous_source_reader_release_reuses_original_images(self):
        archive = image_export(self.base / 'complete.zip', [row(1, 0, source_type='numerical')])
        args = argparse.Namespace(source=archive, source_pdf=None, key=None, solutions=None,
                                  root=self.base / 'Local jobs', paper_id='PAPER-02',
                                  profile='unspecified', offline=True, keep_color=False)
        folder = w.attach(args)
        status = json.loads((folder / 'status.json').read_text())
        record = folder / 'runs' / status['run_id'] / 'run.json'
        data = json.loads(record.read_text())
        data['cropper_sha256'] = w.APPROVED_SOURCE_READER_CROPPER_SHA256
        record.write_text(json.dumps(data))
        original = archive.read_bytes()
        self.assertTrue(w.cached_run(folder))
        with patch.object(w.subprocess, 'run', side_effect=AssertionError('No new crop run')):
            w.attach(args)
        self.assertEqual(len(list((folder / 'runs').iterdir())), 1)
        self.assertEqual((record.parent / 'export.zip').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
