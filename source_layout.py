"""Conservative, opt-in source layout hints for the existing cropper.

This module supplies existing anchors; it does not draw, crop, clean or infer
missing questions. Unsupported or ambiguous sources return None, leaving the
existing detector in charge.
"""
import math
import re
from statistics import mean


_FAMILY = re.compile(
    r'\bJ\.?\s*E\.?\s*E\.?\s*[-:]?\s*'
    r'(?:\(\s*(Main|Advanced)\s*\)|(Main|Advanced)\b)', re.I)
_COUNTS = (
    re.compile(r'\b(?:test\s+booklet|question\s+paper|test|paper)\s+'
               r'(?:consists\s+of|contains)\s+(\d{1,3})\s+questions\b', re.I),
    re.compile(r'\btotal\s+(?:number\s+of\s+)?questions\s*[:=\-]?\s*'
               r'(\d{1,3})\b', re.I),
)
_INSTRUCTIONS = re.compile(r'\b(?:important|general)\s+instructions?\b', re.I)
_QUESTION_SECTION = re.compile(
    r'\bsingle\s+correct\s+answer\s+type\b|\bmultiple\s+choice\s+questions\b', re.I)
_UNRELATED_PAGE = re.compile(
    r'^\s*(?:(?:space\s+for\s+)?rough\s*work|blank\s+page|'
    r'(?:final\s+)?answer\s*key|(?:worked\s+)?solutions?|hints?)\s*[:\-]?\s*$',
    re.I | re.M)
_SECTION = re.compile(r'^\s*SECTION\s*[-:\u2013]?\s*[IVX\d]+\s*$', re.I | re.M)
_ISOLATED_OPTION = re.compile(r'^\(?([1-4A-Da-d])[).]$|^\(([1-4A-Da-d])$')
_GLUED_OPTION = re.compile(r'^\(?([1-4A-Da-d])\)(?=\S)')
_KEY_VALUE = re.compile(r'^\(?[A-Da-d]\)?$|^-?\d+(?:\.\d+)?$|^-?\d+/\d+$')
_KEY_WORDS = {'q', 'qno', 'no', 'ans', 'answer', 'key', 'option', 'question',
              'number', 'value', 'correct', 'response'}


def option_prefix_label(text):
    """Read an explicit glued ')' label, solely for option-presence checks.

    Numeric answers, decimal numbers and ordinary text are not option labels.
    No words, boxes, anchors or question types are manufactured or changed.
    """
    if not isinstance(text, str):
        return None
    match = _GLUED_OPTION.match(text.strip())
    return match.group(1).upper() if match else None


def _page_text(page):
    text = page.get('text', '')
    return text if isinstance(text, str) else ''


def _declared_source(pages):
    # Only actual first-page headings supply the family, never a filename or
    # a marketing page elsewhere in the document.
    title = '\n'.join(line for line in _page_text(pages[0]).splitlines()
                      if line.strip()).splitlines()[:12]
    normalized = '\n'.join(title).replace('_', ' ')
    families = {(m.group(1) or m.group(2)).lower()
                for m in _FAMILY.finditer(normalized)}
    if families != {'main'}:
        return None
    declarations = []
    for pi, page in enumerate(pages[:3]):
        for line in _page_text(page).splitlines():
            for pattern in _COUNTS:
                for match in pattern.finditer(line):
                    declarations.append({'page_index': pi, 'text': line,
                                         'questions': int(match.group(1))})
    totals = {item['questions'] for item in declarations}
    # Very small sequences can themselves be option labels. The hint is not
    # intended to resolve short worksheets; they retain the existing detector.
    if len(totals) != 1 or not 10 <= next(iter(totals)) <= 300:
        return None
    return next(iter(totals)), declarations


def _continuation_evidence(page):
    """Require positive option evidence for an otherwise anchorless body page.

    Image-only or unlabelled continuations are deliberately unsupported here.
    Their absence must not be converted into a claim of complete coverage.
    """
    text = _page_text(page)
    if _UNRELATED_PAGE.search(text) or _SECTION.search(text):
        return None
    labels = set()
    for word in page.get('words', []):
        value = word.get('text', '')
        if not isinstance(value, str):
            continue
        match = _ISOLATED_OPTION.match(value.strip())
        if match:
            labels.add((match.group(1) or match.group(2)).upper())
        else:
            label = option_prefix_label(value)
            if label:
                labels.add(label)
    if len(labels) < 3:
        return None
    return {'kind': 'explicit_option_labels_between_question_pages',
            'option_labels': sorted(labels)}


def _question_content(page, anchor, next_y):
    """Distinguish a question span from a bare vertical number/answer grid.

    Prose is not mandatory: explicit equation operators/variables are positive
    content too. A page containing only labels and key values is not evidence
    for this optional recovery path. Image-only spans retain the old detector.
    """
    for word in page.get('words', []):
        try:
            x, y = float(word['x0']), float(word['top'])
        except (KeyError, TypeError, ValueError):
            return False
        text = word.get('text', '')
        if not isinstance(text, str) or not text.strip():
            continue
        if x <= anchor['x'] or not anchor['y'] <= y < next_y:
            continue
        value = text.strip()
        if (_KEY_VALUE.fullmatch(value) or _ISOLATED_OPTION.fullmatch(value) or
                option_prefix_label(value) or value.lower().startswith('(cid:')):
            continue
        if any(symbol in value for symbol in ('=', '+', '\u00d7', '\u00f7', '\u222b',
                                               '\u221a', '<', '>', '\u2264', '\u2265',
                                               '\u2211', '\u220f', '\u2192', '\u2202',
                                               '\u221e')):
            return True
        letters = re.findall(r'[A-Za-z]+', value)
        if any(len(token) >= 3 and token.lower() not in _KEY_WORDS for token in letters):
            return True
        if any(token.lower() in {'x', 'y', 'z', 'f', 'g', 'r', 't', 'n'} for token in letters):
            return True
    return False


def calibrate_single_column(pages, candidates_fn):
    """Prove a unique single margin containing every declared source question.

    Each page is {width, height, words, text}; words and candidates use the same
    coordinate system as width/height. candidates_fn is the existing cropper's
    candidates function, called on the ORIGINAL words. Returned anchors keep
    the original candidate fields and coordinates. Any uncertainty returns None.
    """
    if not isinstance(pages, (list, tuple)) or not pages:
        return None
    source = _declared_source(pages)
    if source is None:
        return None
    expected, declarations = source
    declaration_pages = {item['page_index'] for item in declarations}
    instruction_pages = {pi for pi in declaration_pages
                         if _INSTRUCTIONS.search(_page_text(pages[pi]))}
    tolerance = 0.005
    anchors = []
    for pi, page in enumerate(pages):
        try:
            width, height = float(page['width']), float(page['height'])
        except (KeyError, TypeError, ValueError):
            return None
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            return None
        if pi in instruction_pages:
            continue
        words = page.get('words')
        if not isinstance(words, list):
            return None
        for original in candidates_fn(words):
            try:
                n, x, y = original['n'], float(original['x']), float(original['y'])
            except (KeyError, TypeError, ValueError):
                return None
            if (type(n) is not int or not 1 <= n <= 300 or
                    not math.isfinite(x) or not math.isfinite(y) or
                    not 0 <= x < width or not 0 <= y < height):
                return None
            anchors.append({'page_index': pi, 'ratio': x / width,
                            'anchor': dict(original)})
    if not anchors:
        return None
    # Stable narrow margins, rather than per-page option-label populations.
    groups = []
    for item in sorted(anchors, key=lambda a: a['ratio']):
        if groups and item['ratio'] - groups[-1][0]['ratio'] <= tolerance:
            groups[-1].append(item)
        else:
            groups.append([item])
    proven = []
    expected_numbers = list(range(1, expected + 1))
    complete = [sorted(group, key=lambda a: (a['page_index'], a['anchor']['y']))
                for group in groups
                if [item['anchor']['n'] for item in sorted(
                    group, key=lambda a: (a['page_index'], a['anchor']['y']))]
                == expected_numbers]
    # A second complete numbered margin is ambiguity even if it might be a
    # key table. Do not choose between separate full sequences by prose density.
    if len(complete) != 1:
        return None
    for ordered in complete:
        first, last = ordered[0]['page_index'], ordered[-1]['page_index']
        first_text = _page_text(pages[first])
        if not _QUESTION_SECTION.search(first_text) or _UNRELATED_PAGE.search(first_text):
            continue
        widths = [float(pages[pi]['width']) for pi in range(first, last + 1)]
        heights = [float(pages[pi]['height']) for pi in range(first, last + 1)]
        if max(widths) / min(widths) > 1.02 or max(heights) / min(heights) > 1.02:
            continue
        by_page = [[] for _ in pages]
        for item in ordered:
            by_page[item['page_index']].append(item['anchor'])
        # Same-row table cells are not a vertical question margin.
        if any(any(b['y'] <= a['y'] for a, b in zip(items, items[1:]))
               for items in by_page):
            continue
        if any(not _question_content(pages[pi], anchor,
                                     items[i + 1]['y'] if i + 1 < len(items)
                                     else float(pages[pi]['height']))
               for pi, items in enumerate(by_page)
               for i, anchor in enumerate(items)):
            continue
        continuations, continuation_proof = [], {}
        valid = True
        for pi in range(first, last + 1):
            text = _page_text(pages[pi])
            if _UNRELATED_PAGE.search(text):
                valid = False
                break
            if not by_page[pi]:
                proof = _continuation_evidence(pages[pi])
                if proof is None:
                    valid = False
                    break
                continuations.append(pi)
                continuation_proof[pi] = proof
        if valid:
            proven.append({'expected_count': expected,
                           'exam_family': 'jee-main',
                           'margin_ratio': mean(item['ratio'] for item in ordered),
                           'tolerance_ratio': tolerance,
                           'anchors_by_page': by_page,
                           'first_page_index': first,
                           'last_page_index': last,
                           'continuation_page_indexes': continuations,
                           'continuation_evidence': continuation_proof,
                           'declaration_evidence': declarations,
                           'basis': 'unique_complete_printed_single_column_sequence',
                           'tail_pages_verified': False})
    return proven[0] if len(proven) == 1 else None


def trusted_candidates(layout, page_index):
    """Return copies of proved original anchors; never synthesize a number."""
    if not isinstance(layout, dict) or type(page_index) is not int:
        return []
    pages = layout.get('anchors_by_page', [])
    if not 0 <= page_index < len(pages):
        return []
    return [dict(anchor) for anchor in pages[page_index]]


def source_question_types(pages, layout):
    """Read response types from explicit printed section headings, never keys.

    Only existing calibrated anchors receive metadata. Unknown section types
    stay unassigned, and this evidence does not route or approve an exam.
    """
    if not isinstance(layout, dict) or not pages:
        return {}
    by_page = layout.get('anchors_by_page', [])
    if len(by_page) != len(pages):
        return {}
    headings = {'SINGLE CORRECT ANSWER TYPE': 'single_choice',
                'NUMERICAL VALUE TYPE': 'numerical'}
    normalize = lambda value: re.sub(r'[^A-Za-z0-9]+', ' ', value).strip().upper()
    result, current, current_subject = {}, None, None
    subjects = {'MATHEMATICS': 'Mathematics', 'MATHS': 'Mathematics',
                'PHYSICS': 'Physics', 'CHEMISTRY': 'Chemistry',
                'BIOLOGY': 'Biology', 'BOTANY': 'Botany', 'ZOOLOGY': 'Zoology'}
    first, last = layout['first_page_index'], layout['last_page_index']
    for pi in range(first, last + 1):
        rows = []
        for word in sorted(pages[pi].get('words', []), key=lambda w: (w['top'], w['x0'])):
            text = word.get('text', '')
            if not isinstance(text, str) or not text.strip():
                continue
            height = max(1, word['bottom'] - word['top'])
            if rows and abs(word['top'] - rows[-1]['top']) <= max(1, min(height, rows[-1]['height']) * 0.35):
                rows[-1]['words'].append(word)
                rows[-1]['bottom'] = max(rows[-1]['bottom'], word['bottom'])
            else:
                rows.append({'top': word['top'], 'bottom': word['bottom'],
                             'height': height, 'words': [word]})
        for row in rows:
            row['raw'] = ' '.join(w['text'] for w in sorted(row['words'], key=lambda w: w['x0']))
            row['normalized'] = normalize(row['raw'])
        events, used = [], set()
        for i, row in enumerate(rows):
            if i in used:
                continue
            raw, normalized, end = row['raw'], row['normalized'], row['bottom']
            subject_label = re.fullmatch(r'(MATHEMATICS|MATHS|PHYSICS|CHEMISTRY|BIOLOGY|BOTANY|ZOOLOGY)(?: MAX(?:IMUM)? MARKS? \d+)?', normalized)
            if subject_label:
                events.append((end, 2, {'subject': subjects[subject_label.group(1)],
                                       'subject_source': {'page': pi + 1, 'text': raw,
                                                          'basis': 'explicit_printed_subject_heading'}}))
            if normalized not in headings and i + 1 < len(rows):
                following = rows[i + 1]
                joined = normalized + ' ' + following['normalized']
                if (joined in headings and 0 <= following['top'] - row['bottom']
                        <= max(row['height'], following['height']) * 1.5):
                    normalized, raw, end = joined, raw + '\n' + following['raw'], following['bottom']
                    used.add(i + 1)
            if normalized in headings:
                evidence = {'source_type': headings[normalized],
                            'source_section': {'page': pi + 1, 'text': raw,
                                               'basis': 'explicit_printed_section_heading'}}
                events.append((end, 0, evidence))
            elif (re.fullmatch(r'SECTION (?:[IVX]+|\d+)', normalized) or
                  (normalized.endswith(' TYPE') and len(normalized.split()) <= 7
                   and any(term in normalized.split() for term in
                           ('ANSWER', 'VALUE', 'MATCH', 'MATRIX', 'INTEGER', 'COMPREHENSION')))):
                events.append((end, 0, None))
        events.extend((anchor['y'], 1, anchor['n']) for anchor in by_page[pi])
        for _, kind, value in sorted(events, key=lambda item: (item[0], item[1] == 1)):
            if kind == 0:
                current = value
            elif kind == 2:
                current_subject = value
                current = None
            elif current is not None:
                result[value] = {'source_type': current['source_type'],
                                 'source_section': dict(current['source_section'])}
                if current_subject is not None:
                    result[value].update(current_subject)
    return result
