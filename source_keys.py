"""Read source answer keys and explicit correction sheets without inventing rules.

The existing cropper parser remains the reader for complete key grids. Sparse
correction tables are handled separately so their serial numbers cannot become
question answers. Unknown finalized markers remain visible and unresolved.
"""
from copy import deepcopy
from datetime import date
import hashlib
from pathlib import Path
import re


_DASHES = str.maketrans({c: '-' for c in '\u2010\u2011\u2012\u2013\u2014\u2212'})
_VALUE = re.compile(r'^-?\d+(?:\.\d+)?$|^[A-Da-d]$|^\([A-Da-d1-4]\)$')
_NUMBER = re.compile(r'^(\d{1,3})[.)]?$')


def _rows(words):
    rows, current, top, height = [], [], None, 0
    for word in sorted(words, key=lambda w: (w['top'], w['x0'])):
        if current and word['top'] - top > max(3, 0.6 * height):
            rows.append(sorted(current, key=lambda w: w['x0']))
            current = []
        if not current:
            top, height = word['top'], max(1, word['bottom'] - word['top'])
        current.append(word)
    if current:
        rows.append(sorted(current, key=lambda w: w['x0']))
    return rows


def _label(text):
    return re.sub(r'[^A-Z0-9]', '', text.upper())


def _span(row, names):
    """Recognise an exact column label, including split PDF word tokens."""
    for start in range(len(row)):
        for count in (1, 2, 3):
            part = row[start:start + count]
            if len(part) == count and ''.join(_label(w['text']) for w in part) in names:
                return min(w['x0'] for w in part), max(w['x1'] for w in part)
    return None


def _correction_header(row):
    number = _span(row, {'QNO', 'QUESTIONNO', 'QUESTIONNUMBER'})
    given = _span(row, {'GIVENKEY', 'ORIGINALKEY'})
    final = _span(row, {'FINALIZEDKEY', 'FINALISEDKEY', 'FINALKEY'})
    if not all((number, given, final)):
        return None
    centers = [(span[0] + span[1]) / 2 for span in (number, given, final)]
    if not centers[0] < centers[1] < centers[2]:
        return None
    # Key cells are centred under their labels. The next explanation heading
    # can be centred over a very wide cell, so its centre is not a cell boundary.
    gaps = [centers[1] - centers[0], centers[2] - centers[1]]
    radii = [gaps[0] / 2, min(gaps) / 2, gaps[1] / 2]
    return [(center - radius, center + radius) for center, radius in zip(centers, radii)]


def _cell(row, bounds):
    left, right = bounds
    return ' '.join(w['text'].strip() for w in row
                    if left <= (w['x0'] + w['x1']) / 2 < right).strip()


def _signature(value):
    value = str(value).strip()
    if re.fullmatch(r'\([A-Da-d1-4]\)', value):
        value = value[1:-1]
    return value.upper() if re.fullmatch(r'[A-Da-d]', value) else value


def identity_from_pages(pages):
    """Read printed identity labels only; filenames are never evidence."""
    ids, dates, families, titles = set(), set(), set(), []
    code = r'([A-Za-z][A-Za-z0-9]{1,11}-\d{1,5})'
    by_role = re.compile(r'(?<![A-Za-z0-9])' + code +
                         r'(?=[ _-]*(?:(?:Final(?:ized)?[ _-]*)?Key|Q\.?\s*P(?:\.|aper)?|Questions|Solutions|Max\.?\s*Marks)\b)', re.I)
    explicit = re.compile(r'\bPaper\s*(?:ID|Code)\s*[:#]\s*([A-Za-z0-9_-]+)', re.I)
    family = re.compile(r'\bJ\.?\s*E\.?\s*E\.?\s*[-:]?\s*\(?\s*(Main|Advanced)\b', re.I)
    for page in pages:
        text = page.get('text') or '\n'.join(' '.join(w['text'] for w in row)
                                               for row in _rows(page.get('words', [])))
        for line in [line for line in text.splitlines() if line.strip()][:12]:
            normalized = line.translate(_DASHES)
            found = by_role.findall(normalized) + explicit.findall(normalized)
            ids.update(value.upper() for value in found)
            labels = family.findall(normalized.replace('_', ' '))
            families.update('jee-' + value.lower() for value in labels)
            if found or labels or re.search(r'\bDate\s*:', normalized, re.I):
                titles.append(line)
                for day, month, year in re.findall(r'(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](\d{4}|\d{2})(?!\d)', normalized):
                    try:
                        yr = int(year) + (2000 if len(year) == 2 else 0)
                        dates.add(date(yr, int(month), int(day)).isoformat())
                    except ValueError:
                        pass
    return {'paper_ids': sorted(ids), 'dates': sorted(dates),
            'exam_families': sorted(families), 'titles': titles}


def read_key_pages(pages, legacy_parser=None, *, source_sha256=None, source_name=None):
    """Read point-coordinate PDF words; preserve source strings and provenance.

    Each page is a dict with ``words``, optional ``text`` and optional 1-based
    ``page``. Complete grid parsing uses the cropper's unchanged parser at its
    usual 200dpi word scale and retains its ten-entry-per-page safety threshold.
    """
    if legacy_parser is None:
        from question_boundaries import parse_key
        legacy_parser = parse_key
    pages = list(pages)
    result = {'source_sha256': source_sha256, 'source_name': source_name,
              'page_count': len(pages), 'identity': identity_from_pages(pages),
              'key': {}, 'key_evidence': {}, 'correction_sheet': False,
              'corrections': [], 'unresolved': [], 'invalid_numbers': [],
              'textless_pages': []}
    invalid = set()
    for index, page in enumerate(pages, 1):
        page_number = page.get('page', index)
        words = page.get('words', [])
        if len(words) < 25:
            result['textless_pages'].append(page_number)
        rows = _rows(words)
        headers = [(i, _correction_header(row)) for i, row in enumerate(rows)]
        headers = [(i, bounds) for i, bounds in headers if bounds]
        if headers:
            result['correction_sheet'] = True
            for hi, (start, bounds) in enumerate(headers):
                stop = headers[hi + 1][0] if hi + 1 < len(headers) else len(rows)
                for row in rows[start + 1:stop]:
                    number_text = _cell(row, bounds[0])
                    number = _NUMBER.fullmatch(number_text)
                    if not number:
                        continue
                    given, finalized = _cell(row, bounds[1]), _cell(row, bounds[2])
                    # A number later in an unrelated table is not a correction.
                    # Two populated answer cells are required to recognise a row.
                    if not given and not finalized:
                        continue
                    n = int(number.group(1))
                    record = {'page': page_number, 'number': n, 'given': given,
                              'finalized': finalized, 'basis': 'explicit_given_and_finalized_key_columns',
                              'source_sha256': source_sha256,
                              'source_line': ' '.join(w['text'] for w in row)}
                    result['corrections'].append(record)
                    if n < 1 or not given or not finalized:
                        invalid.add(n)
                        result['unresolved'].append(f'Q{n}: incomplete correction row.')
            # Never run the legacy alternating-cell parser over a correction
            # page: serial/given/final numbers are not a complete answer grid.
            continue
        scaled = [{key: word[key] * (200 / 72) for key in ('x0', 'x1', 'top', 'bottom')}
                  | {'text': word['text']} for word in words]
        parsed = legacy_parser(scaled)
        if len(parsed) < 10:
            continue
        for n, value in parsed.items():
            if type(n) is not int or n < 1 or not isinstance(value, (str, int, float)) or isinstance(value, bool):
                result['unresolved'].append('Unsupported base key entry.')
                continue
            value = str(value)
            if not _VALUE.fullmatch(value):
                invalid.add(n)
                result['unresolved'].append(f'Q{n}: unsupported base key value.')
                continue
            if n in result['key']:
                invalid.add(n)
                result['unresolved'].append(f'Q{n}: duplicate base key entry across pages.')
            else:
                result['key'][n] = value
                result['key_evidence'][n] = {'page': page_number, 'source_sha256': source_sha256}
    seen = set()
    for row in result['corrections']:
        n = row['number']
        if n in seen:
            invalid.add(n)
            result['unresolved'].append(f'Q{n}: duplicate correction row.')
        seen.add(n)
    for n in invalid:
        result['key'].pop(n, None)
    result['invalid_numbers'] = sorted(invalid)
    if result['correction_sheet'] and not result['corrections']:
        result['unresolved'].append('Correction column headers found but no readable correction rows.')
    if result['correction_sheet'] and result['key']:
        result['unresolved'].append('Document mixes a complete key grid and correction sheets; explicit source selection required.')
    return result


def read_key_pdf(path, legacy_parser=None):
    """Read an unchanged source PDF without rendering or OCR."""
    import pdfplumber
    path = Path(path)
    body = path.read_bytes()
    pages = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            pages.append({'page': number, 'text': page.extract_text() or '',
                          'words': page.extract_words(keep_blank_chars=False, use_text_flow=False)})
    return read_key_pages(pages, legacy_parser, source_sha256=hashlib.sha256(body).hexdigest(),
                          source_name=path.name)


def read_pdf_identity(path):
    """Read printed question/key identity without using a local job ID/version."""
    import pdfplumber
    with pdfplumber.open(Path(path)) as pdf:
        return identity_from_pages([{'text': page.extract_text() or ''} for page in pdf.pages])


def _identity_issues(documents, expected_paper_id=None):
    issues = []
    for field, label in (('paper_ids', 'paper ID'), ('dates', 'printed exam date'),
                         ('exam_families', 'exam family')):
        declarations = [set(doc.get('identity', {}).get(field, [])) for doc in documents]
        declarations = [values for values in declarations if values]
        if any(len(values) != 1 for values in declarations):
            issues.append('Ambiguous ' + label + ' declarations in source headers.')
        if declarations and any(values != declarations[0] for values in declarations[1:]):
            issues.append('Source ' + label + ' mismatch.')
    if expected_paper_id:
        expected = str(expected_paper_id).translate(_DASHES).upper()
        for doc in documents:
            ids = doc.get('identity', {}).get('paper_ids', [])
            if ids and ids != [expected]:
                issues.append('Printed source paper ID does not match selected paper ID.')
    return list(dict.fromkeys(issues))


def combine_keys(base=None, final=None, *, expected_paper_id=None, expected_numbers=None,
                 expected_identity=None, add_policy='unresolved'):
    """Prefer a complete final key, or apply explicit sparse final corrections.

    Unrecognised finalized strings remain in ``key`` with an unresolved reason.
    Only an explicitly selected ``add_policy='original'`` uses the matching base
    answer for an exact Add marker. Conflicting or invalid correction rows remove
    that question's answer entirely.
    """
    if add_policy not in ('unresolved', 'original'):
        raise ValueError('add_policy must be unresolved or original')
    docs = [doc for doc in (base, final) if doc is not None]
    result = {'key': {}, 'corrections': [], 'unresolved': [], 'correction_sheet': False,
              'legacy_fallback_allowed': False, 'identity_issues': [],
              'adjudication_policy': {'add': add_policy,
                                      'basis': 'owner_selected_original_for_add' if add_policy == 'original'
                                      else 'unresolved_source_marker'},
              'sources': [{'sha256': doc.get('source_sha256'), 'name': doc.get('source_name'),
                           'identity': deepcopy(doc.get('identity', {}))} for doc in docs]}
    identity_docs = ([{'identity': expected_identity}] if expected_identity is not None else []) + docs
    identity_issues = _identity_issues(identity_docs, expected_paper_id)
    result['identity_issues'] = identity_issues
    result['unresolved'].extend(identity_issues)
    selected = final if final is not None else base
    if selected is None:
        result['unresolved'].append('No source key document supplied.')
        return result
    result['correction_sheet'] = bool(selected.get('correction_sheet'))
    if not result['correction_sheet']:
        result['key'] = deepcopy(selected.get('key', {}))
        result['unresolved'].extend(selected.get('unresolved', []))
        if not result['key']:
            result['unresolved'].append('No complete source key was parsed.')
            # This authorises only the unchanged reader on this same selected
            # PDF (including its local OCR); it never selects a different base.
            result['legacy_fallback_allowed'] = not identity_issues and not selected.get('unresolved')
    else:
        result['corrections'] = deepcopy(selected.get('corrections', []))
        result['unresolved'].extend(selected.get('unresolved', []))
        if base is None or base is selected or base.get('correction_sheet') or not base.get('key'):
            result['unresolved'].append('A sparse correction sheet requires its complete base key.')
            return result
        result['key'] = deepcopy(base['key'])
        result['unresolved'].extend(base.get('unresolved', []))
        invalid = set(selected.get('invalid_numbers', []))
        seen = set()
        for row in result['corrections']:
            n, given, finalized = row['number'], row['given'], row['finalized']
            if n in seen:
                invalid.add(n)
            seen.add(n)
            if n not in base['key']:
                invalid.add(n)
                result['unresolved'].append(f'Q{n}: correction references a question absent from the base key.')
            elif _signature(given) != _signature(base['key'][n]):
                invalid.add(n)
                result['unresolved'].append(f'Q{n}: given key does not match the selected base key.')
            if n in invalid:
                continue
            row['base_answer'] = base['key'][n]
            if finalized.strip().casefold() == 'add' and add_policy == 'original':
                result['key'][n] = base['key'][n]
                row['effective_answer'] = base['key'][n]
                row['adjudication_basis'] = 'owner_selected_original_for_add'
            else:
                result['key'][n] = finalized
                row['effective_answer'] = finalized if _VALUE.fullmatch(finalized) else None
                row['adjudication_basis'] = ('explicit_finalized_source_value' if _VALUE.fullmatch(finalized)
                                             else 'unresolved_source_marker')
            if row['effective_answer'] is None:
                result['unresolved'].append(f'Q{n}: unresolved finalized source marker {finalized!r}; scoring interpretation required.')
        for n in invalid:
            result['key'].pop(n, None)
    if identity_issues:
        # No answer from a mismatched document is safe to associate with the QP.
        result['key'].clear()
    if expected_numbers is not None and set(result['key']) != set(expected_numbers):
        result['unresolved'].append('Source key numbering does not match the expected question set.')
    result['unresolved'] = list(dict.fromkeys(result['unresolved']))
    return result


def read_key_sources(base_path=None, final_path=None, *, expected_paper_id=None,
                     expected_numbers=None, expected_identity=None, legacy_parser=None,
                     add_policy='unresolved'):
    base = read_key_pdf(base_path, legacy_parser) if base_path else None
    final = read_key_pdf(final_path, legacy_parser) if final_path else None
    return combine_keys(base, final, expected_paper_id=expected_paper_id,
                        expected_numbers=expected_numbers, expected_identity=expected_identity,
                        add_policy=add_policy)
