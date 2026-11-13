"""Synthetic standard PDF structures; no real papers or committed binary fixtures."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

import pdfplumber
from pdfminer.pdftypes import PDFObjRef
from PIL import Image

from test_workspace import image, w


def compatible_pdf(path, *, mode='array_multi', compressed=False, image_only=False,
                   malformed=False):
    """Write proper xrefs and indirect stream arrays, common in overlaid PDFs."""
    def stream(data, extra=b''):
        if compressed:
            data = zlib.compress(data)
            extra += b' /Filter /FlateDecode'
        return b'<< /Length ' + str(len(data)).encode() + extra + b' >>\nstream\n' + data + b'\nendstream'

    first = b'BT /F1 12 Tf 15 70 Td (Synthetic question) Tj ET'
    second = b'BT /F1 10 Tf 15 40 Td (Synthetic overlay) Tj ET'
    resources = b'/Font << /F1 4 0 R >>'
    if image_only:
        first = b'q 100 0 0 80 10 10 cm /Im0 Do Q'
        second = b'q Q'
        resources += b' /XObject << /Im0 7 0 R >>'
    contents = {'single_stream': b'5 0 R', 'array_single': b'[5 0 R]',
                'array_multi': b'[5 0 R 6 0 R]', 'referenced_array': b'8 0 R',
                'blank': b'[]'}[mode]
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 120 100] /Resources << ' + resources +
            b' >> /Contents ' + contents + b' >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        stream(first),
        b'123' if malformed else stream(second),
        stream(bytes([255, 255, 255, 0, 0, 0, 0, 0, 0, 255, 255, 255]),
               b' /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8'),
        b'[5 0 R 6 0 R]'
    ]
    body = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += str(number).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    startxref = len(body)
    body += b'xref\n0 ' + str(len(objects) + 1).encode() + b'\n0000000000 65535 f \n'
    body += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    body += b'trailer\n<< /Size ' + str(len(objects) + 1).encode() + b' /Root 1 0 R >>\nstartxref\n'
    body += str(startxref).encode() + b'\n%%EOF\n'
    path.write_bytes(body)
    return path


class PDFCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='PDF preflight spaces ')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_valid_indirect_contents_array_is_readable_by_pdfplumber(self):
        source = compatible_pdf(self.base / 'Original overlay paper.pdf')
        with pdfplumber.open(source) as doc:
            page = doc.pages[0]
            self.assertIsInstance(page.page_obj.contents[0], PDFObjRef)
            self.assertIn('Synthetic question', page.extract_text())
            self.assertIn('Synthetic overlay', page.extract_text())

    def test_preflight_accepts_single_and_multiple_indirect_content_streams(self):
        for mode in ('single_stream', 'array_single', 'array_multi', 'referenced_array'):
            for compressed in (False, True):
                with self.subTest(mode=mode, compressed=compressed):
                    source = compatible_pdf(self.base / (mode + str(compressed) + '.pdf'),
                                            mode=mode, compressed=compressed)
                    original = source.read_bytes()
                    self.assertEqual(w.strict_pdf(source), source)
                    self.assertEqual(source.read_bytes(), original)

    def test_preflight_accepts_image_only_pdf_with_indirect_stream_array(self):
        source = compatible_pdf(self.base / 'Original scan.pdf', image_only=True, compressed=True)
        with pdfplumber.open(source) as doc:
            self.assertEqual(doc.pages[0].extract_text(), '')
            self.assertEqual(len(doc.pages[0].images), 1)
        original = source.read_bytes()
        self.assertEqual(w.strict_pdf(source), source)
        self.assertEqual(source.read_bytes(), original)

    def test_blank_valid_pdf_is_not_falsely_labelled_corrupt(self):
        # Question detection can refuse a blank source later; PDF validation is separate.
        source = compatible_pdf(self.base / 'Blank valid PDF.pdf', mode='blank')
        self.assertEqual(w.strict_pdf(source), source)

    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftoppm'), 'Poppler unavailable')
    def test_actual_poppler_reads_and_renders_same_valid_array_fixture(self):
        source = compatible_pdf(self.base / 'Original compressed overlay.pdf', compressed=True)
        before = source.read_bytes()
        info = subprocess.run([shutil.which('pdfinfo'), str(source)], capture_output=True,
                              text=True, timeout=10)
        self.assertEqual(info.returncode, 0, info.stderr)
        output = self.base / 'Rendered first page'
        rendered = subprocess.run([shutil.which('pdftoppm'), '-f', '1', '-singlefile', '-r', '72',
                                   '-png', str(source), str(output)], capture_output=True,
                                  text=True, timeout=10)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        with Image.open(output.with_suffix('.png')) as page:
            page.load()
            self.assertEqual(page.size, (120, 100))
        self.assertEqual(source.read_bytes(), before)

    def test_malformed_nonstream_contents_produces_a_controlled_preflight_error(self):
        source = compatible_pdf(self.base / 'Malformed content member.pdf', malformed=True)
        with self.assertRaises(w.WorkspaceError) as failure:
            w.strict_pdf(source)
        # The diagnostic should identify content/parser failure rather than assert a password.
        self.assertNotIn('password-protected', str(failure.exception).lower())

    def test_corrupt_pdf_header_only_is_still_rejected(self):
        source = self.base / 'Header but no document.pdf'
        source.write_bytes(b'%PDF-1.7\nnot an actual PDF document')
        with self.assertRaises(w.WorkspaceError) as failure:
            w.strict_pdf(source)
        self.assertNotIn('password-protected', str(failure.exception).lower())

    @unittest.skipUnless(importlib.util.find_spec('pypdf'), 'Optional encrypted-fixture writer unavailable')
    def test_password_required_pdf_is_distinguished_from_parser_failure(self):
        from pypdf import PdfWriter
        source = self.base / 'Encrypted.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=120, height=100)
        writer.encrypt('synthetic-password')
        with source.open('wb') as stream:
            writer.write(stream)
        before = source.read_bytes()
        with self.assertRaises(w.WorkspaceError) as failure:
            w.strict_pdf(source)
        self.assertEqual(str(failure.exception),
                         'PDF requires a password before processing: Encrypted.pdf')
        self.assertEqual(source.read_bytes(), before)

    def test_direct_and_known_wrapped_password_errors_are_classified(self):
        from pdfminer.pdfdocument import PDFPasswordIncorrect
        from pdfplumber.utils.exceptions import PdfminerException
        source = compatible_pdf(self.base / 'Encrypted.pdf')
        original = source.read_bytes()
        in_args = PdfminerException(PDFPasswordIncorrect())
        in_cause = PdfminerException('PDF document initialization failed')
        in_cause.__cause__ = PDFPasswordIncorrect()
        in_context = PdfminerException('PDF document initialization failed')
        in_context.__context__ = PDFPasswordIncorrect()
        for failure in (PDFPasswordIncorrect(), in_args, in_cause, in_context):
            with self.subTest(wrapper=type(failure).__name__), \
                 patch.object(pdfplumber, 'open', side_effect=failure):
                with self.assertRaises(w.WorkspaceError) as error:
                    w.strict_pdf(source)
                self.assertEqual(str(error.exception),
                                 'PDF requires a password before processing: Encrypted.pdf')
        self.assertEqual(source.read_bytes(), original)

    def test_other_wrapped_parser_failures_are_not_called_password_errors(self):
        from pdfplumber.utils.exceptions import PdfminerException
        source = compatible_pdf(self.base / 'Parser error.pdf')
        failure = PdfminerException(ValueError('Synthetic parser failure'))
        with patch.object(pdfplumber, 'open', side_effect=failure):
            with self.assertRaises(w.WorkspaceError) as error:
                w.strict_pdf(source)
        self.assertEqual(str(error.exception),
                         'PDF preflight check failed (PdfminerException): Parser error.pdf. '
                         'Original file kept unchanged.')

    @unittest.skipUnless(importlib.util.find_spec('pypdf'), 'Optional encrypted-fixture writer unavailable')
    def test_viewable_copy_restricted_pdf_does_not_require_a_password(self):
        from pypdf import PdfWriter
        from pypdf.constants import UserAccessPermissions
        source = self.base / 'Viewable copy restricted.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=120, height=100)
        writer.encrypt('', owner_password='synthetic-owner-password',
                       permissions_flag=UserAccessPermissions.PRINT)
        with source.open('wb') as stream:
            writer.write(stream)
        before = source.read_bytes()
        self.assertEqual(w.strict_pdf(source), source)
        self.assertEqual(source.read_bytes(), before)

    def test_oversized_input_still_stops_before_pdf_parsing(self):
        source = self.base / 'Oversized input.pdf'
        with source.open('wb') as stream:
            stream.write(b'%PDF-1.7\n')
            stream.truncate(w.MAX_FILE + 1)
        with patch.object(pdfplumber, 'open', side_effect=AssertionError('No parsing oversized input')):
            with self.assertRaisesRegex(w.WorkspaceError, '24 MiB'):
                w.strict_pdf(source)

    def test_companion_batch_accepts_valid_array_sources_without_changing_their_bytes(self):
        source = self.base / 'Batch input'
        source.mkdir()
        questions = compatible_pdf(source / 'GTM-28_QP.PDF', compressed=True)
        key = compatible_pdf(source / 'GTM-28_FINAL-KEY.PDF', mode='array_single')
        original = {path.name: path.read_bytes() for path in source.iterdir()}
        root = self.base / 'Output jobs'
        entries = w.discover_batch(source, root)
        self.assertEqual([item['paper_id'] for item in entries], ['GTM-28'])
        self.assertIsNone(entries[0]['reason'])
        self.assertFalse(root.exists())
        calls = []
        def crop(command, **kwargs):
            calls.append(command)
            out = Path(command[command.index('--out') + 1])
            (out / 'crops').mkdir(parents=True)
            (out / 'crops/Q001.png').write_bytes(image())
            (out / 'exam.json').write_text(json.dumps({'count': 1, 'questions': [
                {'number': 1, 'image': 'crops/Q001.png', 'answer': 0}]}))
            return subprocess.CompletedProcess(command, 0)
        args = argparse.Namespace(source=source, root=root, profile='unspecified', offline=True,
                                  keep_color=False, paper_profile=[])
        with redirect_stdout(io.StringIO()), patch.object(w.subprocess, 'run', side_effect=crop):
            rows = w.run_batch(args)
        self.assertEqual(rows[0]['outcome'], 'upload failed/local-only')
        self.assertEqual(len(calls), 1)
        selected_key = Path(calls[0][calls[0].index('--key') + 1])
        self.assertEqual(selected_key.read_bytes(), key.read_bytes())
        self.assertEqual((root / 'GTM-28/input/questions.pdf').read_bytes(), questions.read_bytes())
        self.assertEqual(original, {path.name: path.read_bytes() for path in source.iterdir()})

    def test_symlink_array_source_is_held_before_reading_outside_the_batch(self):
        source = self.base / 'Unsafe batch'
        source.mkdir()
        outside = compatible_pdf(self.base / 'Outside source.pdf')
        (source / 'GTM-28_QP.PDF').symlink_to(outside)
        rows = w.discover_batch(source, self.base / 'Output jobs')
        self.assertEqual(rows[0]['paper_id'], 'GTM-28')
        self.assertIn('Symlink', rows[0]['reason'])
        self.assertFalse((self.base / 'Output jobs').exists())


if __name__ == '__main__':
    unittest.main()
