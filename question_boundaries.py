#!/usr/bin/env python3
"""Auto-mark question boundaries on a question-paper PDF (or page images), then crop.

Step 1 - detect and mark (nothing is cropped yet except a preview):
    python3 tools/question_boundaries.py paper.pdf --out work/paper1

  Writes into --out:
    pages/p01.png ...          rendered pages
    preview/p01.png ...        pages with a coloured box + label on every question
    boundaries.json           the boxes (edit this file to fix any box by hand)
    review.html               open in a browser: flagged questions first, then all crops
    REPORT.txt                plain-text list of problems to check

Step 2 - after fixing boundaries.json (only if the report flagged something):
    python3 tools/question_boundaries.py --recrop work/paper1

How it finds questions
  * Question numbers ("1.", "12)", "Q5", "Q.5.") are read from the PDF text layer,
    or by OCR (tesseract) when the PDF is a scan.
  * Only numbers that line up on the left edge of a column and follow the sequence
    1,2,3... are accepted, so option labels and figure numbers are ignored.
  * One or two columns per page are detected automatically.
  * A question ends where the next one starts. A question that runs on into the
    next column or page is stitched together in its crop.
"""
import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DPI = 200
ANCHOR_RE = re.compile(r'^(?:[Qq]\.?\s*(\d{1,3})\.?|(\d{1,3})[.)])$')
COLORS = [(220, 38, 38), (22, 101, 52), (30, 64, 175), (180, 83, 9), (126, 34, 206), (14, 116, 144)]


# ----------------------------------------------------------------------------- rendering
def render_pdf(pdf, pages_dir, dpi=DPI):
    pages_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(['pdftoppm', '-r', str(dpi), '-png', str(pdf), str(pages_dir / 'tmp')], check=True)
    out = []
    for i, f in enumerate(sorted(pages_dir.glob('tmp-*.png')), 1):
        dest = pages_dir / f'p{i:02d}.png'
        f.rename(dest)
        out.append(dest)
    return out


def load_images(folder, pages_dir):
    pages_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in ('.png', '.jpg', '.jpeg'))
    out = []
    for i, f in enumerate(files, 1):
        dest = pages_dir / f'p{i:02d}.png'
        Image.open(f).convert('RGB').save(dest)
        out.append(dest)
    return out


# ----------------------------------------------------------------------------- cleaning
def clean_image(img, remove_color=False):
    """Remove watermarks and pen marks from a page image.
    * Light grey / faint-tint marks (typical watermarks, logos, stamps) are whitened, but never
      anything touching the dark text, so characters keep their shape.
    * With remove_color=True, strongly coloured strokes (red/blue pen, highlighter) are removed too.
    Returns (clean image, percentage of pixels changed)."""
    import cv2
    arr = np.array(img.convert('RGB'))
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    sat, val = hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    dark = (val < 140).astype('uint8')
    halo = cv2.dilate(dark, np.ones((5, 5), 'uint8')).astype(bool)
    kill = (~halo) & (val >= 150) & (sat < 120)           # light grey / faint tint
    if remove_color:
        strong = (sat >= 120) & (val >= 50)
        strong = cv2.dilate(strong.astype('uint8'), np.ones((5, 5), 'uint8')).astype(bool)
        kill |= strong & ~(dark.astype(bool) & (sat < 80))  # keep black text, drop coloured strokes
    # page border: long vertical lines hugging the left/right edge are decoration, not content
    H, W = val.shape
    dk = (val < 170).astype('uint8')
    vlines = cv2.morphologyEx(dk, cv2.MORPH_OPEN, np.ones((int(H * 0.4), 1), 'uint8'))
    edge = np.zeros_like(vlines)
    edge[:, :int(W * 0.08)] = 1
    edge[:, int(W * 0.92):] = 1
    border = cv2.dilate(vlines & edge, np.ones((9, 9), 'uint8')).astype(bool)
    kill |= border
    before = arr.copy()
    arr[kill] = 255
    changed = float((before != arr).any(axis=2).mean() * 100)
    return Image.fromarray(arr), changed


# ----------------------------------------------------------------------------- anchors
def text_layer_words(pdf, page_index, dpi):
    """Words from the PDF text layer, in rendered-pixel coordinates. [] if none."""
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf)) as doc:
            page = doc.pages[page_index]
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            k = dpi / 72.0
            return [dict(text=w['text'], x0=w['x0'] * k, x1=w['x1'] * k,
                         top=w['top'] * k, bottom=w['bottom'] * k) for w in words]
    except Exception:
        return []


def ocr_words(img):
    """OCR with several page-layout modes and merge them: each mode misses different
    isolated question numbers, so the union is far more reliable than any single mode."""
    import pytesseract
    words, seen = [], set()
    for psm in (6, 4, 3):
        d = pytesseract.image_to_data(img, config=f'--psm {psm}', output_type=pytesseract.Output.DICT)
        for i, t in enumerate(d['text']):
            t = t.strip()
            if not t or float(d['conf'][i]) <= 30:
                continue
            key = (t, d['left'][i] // 12, d['top'][i] // 12)
            if key in seen:
                continue
            seen.add(key)
            words.append(dict(text=t, x0=d['left'][i], x1=d['left'][i] + d['width'][i],
                              top=d['top'][i], bottom=d['top'][i] + d['height'][i]))
    return words


def candidates(words):
    """Words that look like a question number."""
    out = []
    for i, w in enumerate(words):
        t = w['text']
        m = ANCHOR_RE.match(t)
        n = None
        x0 = w['x0']
        if m:
            n = int(m.group(1) or m.group(2))
        elif re.fullmatch(r'[Qq]\.?', t):  # "Q." then "12" as separate words
            for v in words[i + 1:i + 4]:
                if abs(v['top'] - w['top']) < (w['bottom'] - w['top']) * 0.6 and re.fullmatch(r'\d{1,3}\.?', v['text']):
                    n = int(v['text'].rstrip('.'))
                    break
        if n is not None and 1 <= n <= 300:
            out.append(dict(n=n, x=x0, y=w['top'], h=w['bottom'] - w['top']))
    uniq = []
    for c in sorted(out, key=lambda c: (c['n'], c['y'])):
        if not any(u['n'] == c['n'] and abs(u['y'] - c['y']) < 25 and abs(u['x'] - c['x']) < 40 for u in uniq):
            uniq.append(c)
    return uniq


def recover_margin_numbers(gray, mask, cols, cands, W):
    """Second chance for scans: find small ink blobs sitting exactly on the column's left
    edge (a question number looks like this) and OCR each one, so a number that full-page
    OCR dropped is still found."""
    import cv2
    import pytesseract
    found = []
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, int(W * 0.006)), 3))
    for cl in cols:
        xs = int(np.mean([k['x'] for k in cl]))
        x0, x1 = max(0, xs - int(W * 0.02)), min(W, xs + int(W * 0.12))
        strip = (mask[:, x0:x1] * 255).astype('uint8')
        dil = cv2.dilate(strip, kernel)
        n, _, stats, _ = cv2.connectedComponentsWithStats(dil, connectivity=8)
        for i in range(1, n):
            bx, by, bw, bh, _ = stats[i]
            if bw > W * 0.08 or not 12 <= bh <= 90 or abs(x0 + bx - xs) > W * 0.025:
                continue
            if any(abs(c['y'] - by) < 45 and abs(c['x'] - (x0 + bx)) < W * 0.04 for c in cands):
                continue
            tok = gray[max(0, by - 6):by + bh + 7, max(0, x0 + bx - 6):x0 + bx + bw + 7]
            im = Image.fromarray(tok).resize((tok.shape[1] * 3, tok.shape[0] * 3))
            txt = pytesseract.image_to_string(
                im, config='--psm 7 -c tessedit_char_whitelist=0123456789.)Qq').strip()
            m = ANCHOR_RE.match(txt)
            if m:
                found.append(dict(n=int(m.group(1) or m.group(2)), x=float(x0 + bx), y=float(by), h=float(bh)))
    return found


def is_answer_grid(cands):
    """An answer-key table ("1) 2  2) 3  3) 2 ...") puts many numbers on the same text line."""
    rows = {}
    for c in cands:
        rows.setdefault(round(c['y'] / max(c['h'], 1)), []).append(c)
    return sum(1 for r in rows.values() if len(r) >= 5) >= 2


def _runs_up(cluster):
    """Share of neighbours (top to bottom) whose numbers go up by 1-3, like 6,7,8,9. Question
    numbers do this; option labels (1,1,1,2,3,4) do not."""
    ns = [k['n'] for k in sorted(cluster, key=lambda k: k['y'])]
    if len(ns) < 2:
        return 0.0
    return sum(1 for a, b in zip(ns, ns[1:]) if 1 <= b - a <= 3) / (len(ns) - 1)


def cluster_columns(cands, page_w):
    """Group candidates by left edge -> one cluster per column."""
    if not cands:
        return []
    tol = page_w * 0.03
    cands = sorted(cands, key=lambda c: c['x'])
    clusters = [[cands[0]]]
    for c in cands[1:]:
        if c['x'] - np.mean([k['x'] for k in clusters[-1]]) <= tol:
            clusters[-1].append(c)
        else:
            clusters.append([c])
    # a question column counts upward 6,7,8..; clusters of option labels do not
    good = [c for c in clusters if len(c) >= 2 and _runs_up(c) >= 0.75]
    if not good:
        good = [max(clusters, key=lambda c: len({k['n'] for k in c}))]
    biggest = max(len(c) for c in good)
    keep = [c for c in good if len(c) >= max(2, biggest * 0.3)] or [max(good, key=len)]
    # real columns sit far apart; closer clusters are stray numbers (options, sub-points)
    keep.sort(key=lambda c: np.mean([k['x'] for k in c]))
    spaced = []
    for c in keep:
        cx = np.mean([k['x'] for k in c])
        if spaced and cx - np.mean([k['x'] for k in spaced[-1]]) < page_w * 0.25:
            continue   # too close: it is an indented option/sub-point list, keep the left-most (margin) one
        spaced.append(c)
    return spaced[:3]


OPT_RE = re.compile(r'^\(?([1-4A-Da-d])[).]$|^\(([1-4A-Da-d])$')
MARK_RE = re.compile(r'^(?:SECTION|Section)(?:[-\u2013:]\s*[IVXivx\d]+)?:?$')


def find_markers(words):
    """Section banners ("SECTION-II"): a question must stop where one begins."""
    return [dict(y=w['top'], x=w['x0']) for w in words if MARK_RE.match(w['text'])]


def detect_bands(masks):
    """Find the repeating page header and footer (title strip, page number, adverts) by comparing
    pages with each other: rows that look the same on most pages are not question content.
    Returns (first_content_row, last_content_row) or None."""
    n = len(masks)
    if n < 4:
        return None
    H = masks[0].shape[0]
    diff = np.zeros(H)
    pairs = 0
    for i in range(1, n - 1):
        A, B = masks[i], masks[i + 1]
        if A.shape != B.shape:
            continue
        inter, union = (A & B).sum(axis=1), (A | B).sum(axis=1)
        has = union > 2
        diff += (has & (inter < 0.55 * union))
        pairs += 1
    if pairs < 3:
        return None
    ys = np.where(diff > 0.5 * pairs)[0]
    if len(ys) == 0:
        return None
    top, bottom = int(ys[0]), int(ys[-1])
    if top > H * 0.35 or bottom < H * 0.6:
        return None
    return max(0, top - 12), min(H, bottom + 3)


NUMP_RE = re.compile(r'^(\d{1,3})[).]$')
ANS_RE = re.compile(r'^\(?[A-Da-d1-4]\)?$|^-?\d{1,5}(?:\.\d+)?$')
PLAIN_NUM_RE = re.compile(r'^\d{1,3}$')


def _rows(words):
    rows, cur, top, h = [], [], None, 0
    for w in sorted(words, key=lambda w: (w['top'], w['x0'])):
        if cur and w['top'] - top > max(4, 0.6 * h):
            rows.append(sorted(cur, key=lambda w: w['x0'])); cur = []
        if not cur:
            top, h = w['top'], max(1, w['bottom'] - w['top'])
        cur.append(w)
    if cur:
        rows.append(sorted(cur, key=lambda w: w['x0']))
    return rows


def _consecutive(ns):
    return len(ns) >= 3 and all(b == a + 1 for a, b in zip(ns, ns[1:]))


def parse_key(words):
    """Answer-key entries from a page -> {12: '3'}. Understands two layouts, read row by row:
       "1) 2   2) 3   3) 1 ..."  (numbers with a bracket)   and   a table  | 1 | 2 | 2 | 3 | ...
       A row needs at least 5 consecutive numbers (so option lines "(1) 2 (2) 3 ..." and ordinary
       text are never mistaken for a key); the short last row of a key is accepted when it simply
       continues the numbering."""
    key, partial = {}, []
    for row in _rows(words):
        texts = [w['text'].strip() for w in row]
        pairs, i = [], 0
        while i < len(texts) - 1:           # layout A: "N)" then answer
            m = NUMP_RE.match(texts[i])
            if m and ANS_RE.match(texts[i + 1]):
                pairs.append((int(m.group(1)), texts[i + 1].strip('()').upper())); i += 2
            else:
                m2 = re.match(r'^(\d{1,3})[).]([A-Da-d1-4])$', texts[i])
                if m2:
                    pairs.append((int(m2.group(1)), m2.group(2).upper()))
                i += 1
        if len(pairs) < 2 and len(texts) >= 4 and len(texts) % 2 == 0 and \
                all(PLAIN_NUM_RE.match(t) for t in texts[0::2]) and all(ANS_RE.match(t) for t in texts[1::2]):
            pairs = [(int(n), a.strip('()').upper()) for n, a in zip(texts[0::2], texts[1::2])]   # layout B: table cells
        if len(pairs) >= 2 and _consecutive([p[0] for p in pairs]) if len(pairs) >= 3 else (len(pairs) == 2 and pairs[1][0] == pairs[0][0] + 1):
            (key.update(dict(pairs)) if len(pairs) >= 5 else partial.append(pairs))
    for pairs in partial:                    # short last row continuing the numbering
        if key and pairs[0][0] == max(key) + 1:
            key.update(dict(pairs))
    return key


def option_labels(words, box):
    x0, y0, x1, y1 = box
    labs = set()
    for w in words:
        cx, cy = (w['x0'] + w['x1']) / 2, (w['top'] + w['bottom']) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            m = OPT_RE.match(w['text'])
            if m:
                labs.add((m.group(1) or m.group(2)).upper())
    return labs


def key_from_file(path, dpi):
    """Read an answer key from a separate PDF."""
    import tempfile
    key = {}
    with tempfile.TemporaryDirectory() as tmp:
        files = render_pdf(Path(path), Path(tmp), dpi)
        for i, f in enumerate(files):
            words = text_layer_words(path, i, dpi)
            if len(words) < 25:
                words = ocr_words(Image.open(f).convert('RGB'))
            kp = parse_key(words)
            if len(kp) >= 10:      # a page of the key, not a page of solutions
                key.update(kp)
    return key


def gate(pages, questions, key):
    """Return the reasons this upload is NOT accepted (empty list = accepted)."""
    why = []
    nums = [q['number'] for q in questions]
    missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
    if missing:
        why.append(f'question numbers missing: {missing[:15]}')
    with_opts = sum(1 for q in questions if q.get('options_found', 0) >= 3)
    if with_opts < 0.65 * len(questions):
        why.append(f'only {with_opts} of {len(questions)} items have answer options (1)-(4)/(A)-(D); '
                   'this looks like a solutions/hints file, not a question paper')
    if not key:
        why.append('no answer key found (needs a key table in the PDF, or pass --key key.pdf)')
    else:
        kn, qn = set(key), set(nums)
        if kn != qn:
            why.append(f'answer key has {len(kn)} answers but the paper has {len(qn)} questions'
                       + (f'; key lacks {sorted(qn - kn)[:10]}' if qn - kn else '')
                       + (f'; key has extra {sorted(kn - qn)[:10]}' if kn - qn else ''))
    return why


# ----------------------------------------------------------------------------- geometry helpers
def ink_mask(gray):
    """Dark pixels, minus long straight lines (page borders, frames) that are not content."""
    import cv2
    m = (gray < 170).astype('uint8')
    H, W = m.shape
    v = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((int(H * 0.4), 1), 'uint8'))
    h = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((1, int(W * 0.4)), 'uint8'))
    lines = cv2.dilate(cv2.bitwise_or(v, h), np.ones((7, 7), 'uint8'))
    return (m & (1 - lines)).astype(bool)


def ink_rows(mask, x0, x1, y0, y1):
    sub = mask[y0:y1, x0:x1]
    thresh = max(2, int((x1 - x0) * 0.002))
    return np.where(sub.sum(axis=1) > thresh)[0] + y0


def trim_box(mask, x0, y0, x1, y1, pad):
    """Shrink a box to the ink inside it, then add padding (never past the box)."""
    sub = mask[y0:y1, x0:x1]
    if sub.size == 0:
        return None
    rows = np.where(sub.sum(axis=1) > max(2, (x1 - x0) * 0.002))[0]
    cols = np.where(sub.sum(axis=0) > 0)[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    return (max(x0, x0 + cols[0] - pad), max(y0, y0 + rows[0] - pad),
            min(x1, x0 + cols[-1] + pad), min(y1, y0 + rows[-1] + pad))


# ----------------------------------------------------------------------------- detection
def detect(pdf, page_files, args):
    # Compatibility is opt-in. The accepted default cropper, cleaning and
    # geometry remain available unchanged. Trust a shared question margin only
    # when the source itself proves one complete, unambiguous sequence.
    layout, source_pages = None, None
    if pdf and getattr(args, 'source_compat', False):
        import pdfplumber
        from source_layout import calibrate_single_column, option_prefix_label
        try:
            with pdfplumber.open(str(pdf)) as document:
                k = args.dpi / 72.0
                source_pages = [dict(width=page.width * k, height=page.height * k,
                                     text=page.extract_text() or '',
                                     words=[dict(text=w['text'], x0=w['x0'] * k, x1=w['x1'] * k,
                                                 top=w['top'] * k, bottom=w['bottom'] * k)
                                            for w in page.extract_words(keep_blank_chars=False, use_text_flow=False)])
                                for page in document.pages]
            layout = calibrate_single_column(source_pages, candidates)
            if layout:
                from source_layout import source_question_types
                args._source_types = source_question_types(source_pages, layout)
            from source_keys import identity_from_pages
            args._source_identity = identity_from_pages(source_pages)
        except Exception as exc:
            # An optional text-layout hint must not remove the existing local
            # text-reader/OCR fallback on renderable sources.
            source_pages = None
            layout = None
            args._source_inspection_error = type(exc).__name__
        args._source_layout = layout
    pages = []          # per-page info
    page_key = {}       # answer key found on key pages of this PDF
    page_words, page_markers = [], []
    all_anchors = []    # (page, column, anchor)

    # pass 0: read every page once
    imgs, masks = [], []
    for pi, pf in enumerate(page_files):
        img = Image.open(pf).convert('RGB')
        imgs.append(img)
        masks.append(ink_mask(np.array(img.convert('L'))))
    bands = detect_bands(masks)
    if bands and layout:
        top, bottom = bands
        anchors_outside = any(not top <= a['y'] <= bottom
                              for anchors in layout['anchors_by_page'] for a in anchors)
        continuation_options_outside = any(
            not (top <= word['top'] and word['bottom'] <= bottom)
            for pi in layout['continuation_page_indexes'] for word in source_pages[pi]['words']
            if OPT_RE.match(word['text']) or option_prefix_label(word['text']))
        if anchors_outside or continuation_options_outside:
            # Repeated body rows can resemble a header/footer. Source-proven
            # question numbers take precedence over that optional inference.
            bands = None
            layout['band_mode'] = 'fixed_margins_inferred_bands_conflict_with_source'

    for pi, pf in enumerate(page_files):
        img = imgs[pi]
        gray = np.array(img.convert('L'))
        H, W = gray.shape
        words = (source_pages[pi]['words'] if source_pages is not None else
                 text_layer_words(pdf, pi, args.dpi) if pdf else [])
        source = 'text'
        if len(words) < 25 and not layout:
            words, source = ocr_words(img), 'ocr'
        if bands:
            top_lim, bot_lim = bands
        else:
            top_lim, bot_lim = int(H * args.skip_top), int(H * (1 - args.skip_bottom))
        kp = parse_key(words)
        is_key_page = len(kp) >= 10
        if is_key_page:
            page_key.update(kp)
        cands = [] if is_key_page else [c for c in candidates(words) if top_lim <= c['y'] <= bot_lim]
        cols = cluster_columns(cands, W)
        cols.sort(key=lambda c: np.mean([k['x'] for k in c]))
        if layout:
            # These are existing source number anchors, not guessed gaps or
            # option labels promoted into questions. Keep singleton columns.
            trusted = [a for a in layout['anchors_by_page'][pi] if top_lim <= a['y'] <= bot_lim]
            cols = [trusted] if trusted or pi in layout['continuation_page_indexes'] else []
        if source == 'ocr' and cols and not layout:
            extra = recover_margin_numbers(gray, masks[pi], cols, cands, W)
            for e in extra:
                best = min(cols, key=lambda cl: abs(np.mean([k['x'] for k in cl]) - e['x']))
                if abs(np.mean([k['x'] for k in best]) - e['x']) < W * 0.03:
                    best.append(e)
        page_words.append(words)
        page_markers.append(find_markers(words))
        pages.append(dict(file=pf.name, w=W, h=H, source=source, ncols=len(cols),
                          skipped='answer_key' if is_key_page else '',
                          content_rows=[int(top_lim), int(bot_lim)]))
        for ci, cl in enumerate(cols):
            for a in sorted(cl, key=lambda k: k['y']):
                all_anchors.append(dict(page=pi, col=ci, **a))
        pages[-1]['_cols'] = cols

    # keep the longest 1,2,3... chain (an instruction list "1. 2. 3." elsewhere in the file is a shorter chain)
    chains = []
    for a in all_anchors:  # already in page, column, y order
        n = a['n']
        fits = [c for c in chains if n == c['exp'] or c['exp'] < n <= c['exp'] + 3]
        if fits:
            # continue the chain whose last number is nearest (most recent) in the document
            c = max(fits, key=lambda c: (c['items'][-1]['page'], c['items'][-1]['col'], c['items'][-1]['y']))
            if n > c['exp']:
                a = dict(a, gap_before=list(range(c['exp'], n)))
            c['items'].append(a); c['exp'] = n + 1
        elif n <= 3:
            chains.append(dict(items=[a], exp=n + 1))
    chosen = max(chains, key=lambda c: len(c['items']))['items'] if chains else []
    if not chosen:
        sys.exit('No question numbers found. Check the PDF, or try --skip-top/--skip-bottom, or --dpi 250.')

    # ---- turn anchors into boxes
    questions = []
    by_page_col = {}
    for a in chosen:
        by_page_col.setdefault((a['page'], a['col']), []).append(a)
    prev_q = None
    for pi, pf in enumerate(page_files):
        mask = masks[pi]
        H, W = mask.shape
        cols = pages[pi]['_cols']
        xs = [np.mean([k['x'] for k in c]) if c else layout['margin_ratio'] * W for c in cols]
        top_lim, bot_lim = pages[pi]['content_rows']
        for ci in range(len(cols)):
            anchors = by_page_col.get((pi, ci), [])
            cx0 = int(max(0, xs[ci] - W * 0.03))
            cx1 = int(xs[ci + 1] - W * 0.015) if ci + 1 < len(cols) else W
            if not anchors:
                if layout and pi in layout['continuation_page_indexes'] and prev_q is not None:
                    # A source-proven interior page can contain only the end
                    # of the preceding question. Apply the existing trim and
                    # section-stop rules to that full-width continuation.
                    stops = [m['y'] for m in page_markers[pi]
                             if cx0 - W * 0.05 <= m['x'] <= cx1 and top_lim <= m['y'] < bot_lim]
                    end = int(min(stops)) - 3 if stops else bot_lim
                    box = trim_box(mask, cx0, top_lim, cx1, end, args.pad)
                    if box and box[3] - box[1] > H * 0.01:
                        prev_q['parts'].append(dict(page=pi, box=list(map(int, box)), continuation=True))
                continue
            pad_up = int(args.pad * 0.8)
            starts = []
            reach = int(H * 0.03)   # how far above the number a tall symbol (fraction, sqrt) may reach
            for k, a in enumerate(anchors):
                floor = max(top_lim, (int(anchors[k - 1]['y']) + int(anchors[k - 1]['h']) if k else top_lim))
                y = int(a['y'])
                limit = max(floor, y - reach)
                col_ink = np.zeros(H, bool)
                rr = ink_rows(mask, cx0, cx1, limit, y + 1)
                col_ink[rr] = True
                while y > limit and col_ink[y - 1]:
                    y -= 1
                # walked all the way up still in ink: fall back to a small fixed margin
                starts.append(max(top_lim, (y if y > limit else int(a['y']) - pad_up) - 2))
            # continuation of the previous question at the top of this column
            mk = sorted(m['y'] for m in page_markers[pi] if cx0 - W * 0.05 <= m['x'] <= cx1)
            cut0 = [y for y in mk if top_lim <= y < starts[0]]
            end0 = int(min(cut0)) - 3 if cut0 else starts[0]
            if end0 - top_lim > 12:
                box = trim_box(mask, cx0, top_lim, cx1, end0, args.pad)
                if box and prev_q is not None and (box[3] - box[1]) > H * 0.01:
                    prev_q['parts'].append(dict(page=pi, box=list(map(int, box)), continuation=True))
            for k, a in enumerate(anchors):
                y_end = starts[k + 1] if k + 1 < len(anchors) else bot_lim
                stop = [y for y in mk if starts[k] < y < y_end]
                if stop:
                    y_end = int(min(stop)) - 3
                box = trim_box(mask, cx0, starts[k], cx1, y_end, args.pad)
                q = dict(number=a['n'], parts=[], flags=[])
                if a.get('gap_before'):
                    q['flags'].append('missing_numbers_before:' + ','.join(map(str, a['gap_before'])))
                if box:
                    q['parts'].append(dict(page=pi, box=list(map(int, box)), continuation=False))
                else:
                    q['flags'].append('empty_box')
                questions.append(q)
                prev_q = q

    # ---- sanity flags
    for q in questions:
        hs = sum(p['box'][3] - p['box'][1] for p in q['parts'])
        H = pages[q['parts'][0]['page']]['h'] if q['parts'] else 1
        if q['parts'] and hs < H * 0.012:
            q['flags'].append('very_short')
        if q['parts'] and hs > H * 0.6:
            q['flags'].append('very_tall_maybe_two_questions')
        if len(q['parts']) > 1:
            q['flags'].append('spans_columns_or_pages')
    for q in questions:
        labs = set()
        for part in q['parts']:
            labs |= option_labels(page_words[part['page']], part['box'])
            if layout:
                from source_layout import option_prefix_label
                x0, y0, x1, y1 = part['box']
                for word in page_words[part['page']]:
                    cx, cy = (word['x0'] + word['x1']) / 2, (word['top'] + word['bottom']) / 2
                    label = option_prefix_label(word['text'])
                    if label and x0 <= cx <= x1 and y0 <= cy <= y1:
                        labs.add(label)
        q['options_found'] = len(labs)
    for p in pages:
        p.pop('_cols', None)
    return pages, questions, page_key


# ----------------------------------------------------------------------------- outputs
def font(size):
    for f in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_previews(out, pages, questions):
    prev = out / 'preview'
    prev.mkdir(exist_ok=True)
    for pi, p in enumerate(pages):
        img = Image.open(out / 'pages' / p['file']).convert('RGB')
        d = ImageDraw.Draw(img, 'RGBA')
        f = font(max(18, img.width // 45))
        for q in questions:
            color = COLORS[q['number'] % len(COLORS)]
            for part in q['parts']:
                if part['page'] != pi:
                    continue
                x0, y0, x1, y1 = part['box']
                d.rectangle([x0, y0, x1, y1], outline=color + (255,), width=4, fill=color + (18,))
                label = f"Q{q['number']}" + (' (cont.)' if part['continuation'] else '')
                tw = d.textlength(label, font=f)
                d.rectangle([x0, y0, x0 + tw + 12, y0 + f.size + 8], fill=color + (255,))
                d.text((x0 + 6, y0 + 3), label, fill=(255, 255, 255), font=f)
        scale = 1100 / img.width
        img.resize((1100, int(img.height * scale))).save(prev / p['file'])


def make_crops(out, pages, questions):
    crops = out / 'crops'
    crops.mkdir(exist_ok=True)
    for q in questions:
        pieces = []
        for part in q['parts']:
            im = Image.open(out / 'pages' / pages[part['page']]['file']).convert('RGB')
            pieces.append(im.crop(tuple(part['box'])))
        if not pieces:
            continue
        w = max(p.width for p in pieces)
        h = sum(p.height for p in pieces) + 10 * (len(pieces) - 1)
        canvas = Image.new('RGB', (w, h), 'white')
        y = 0
        for p in pieces:
            canvas.paste(p, (0, y)); y += p.height + 10
        canvas.save(crops / f"Q{q['number']:03d}.png")


def write_review(out, pages, questions):
    flagged = [q for q in questions if q['flags'] and q['flags'] != ['spans_columns_or_pages']]
    lines = ['QUESTION BOUNDARY REPORT', f'Pages: {len(pages)}   Questions found: {len(questions)}']
    nums = [q['number'] for q in questions]
    if nums:
        missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
        lines.append(f'Numbers 1..{max(nums)} missing: {missing if missing else "none"}')
    pcts = sorted(p.get('cleaned_pct', 0) for p in pages)
    med = pcts[len(pcts) // 2] if pcts else 0
    heavy = [p['file'] for p in pages if p.get('cleaned_pct', 0) > max(12, med + 8)]
    if heavy:
        lines.append('WARNING: a lot was removed by cleaning on ' + ', '.join(heavy) + ' - check for coloured diagrams (originals are in pages_original/; use --keep-color to keep colour).')
    skipped = [p['file'] for p in pages if p.get('skipped')]
    if skipped:
        lines.append('Skipped (answer-key grid, not questions): ' + ', '.join(skipped))
    lines.append('')
    if flagged:
        lines.append('CHECK THESE:')
        for q in flagged:
            lines.append(f"  Q{q['number']}: {', '.join(q['flags'])}")
    else:
        lines.append('Nothing flagged.')
    lines.append('')
    lines.append('Open review.html to look at every crop; edit boundaries.json and run --recrop to fix any box.')
    (out / 'REPORT.txt').write_text('\n'.join(lines) + '\n')

    def card(q):
        fl = f"<div class=fl>{html.escape(', '.join(q['flags']))}</div>" if q['flags'] else ''
        return f"<div class=c><b>Q{q['number']}</b>{fl}<img loading=lazy src='crops/Q{q['number']:03d}.png'></div>"
    body = ['<h2>Check first</h2>' if flagged else '<h2>Nothing flagged</h2>']
    body += [card(q) for q in flagged]
    body += ['<h2>Page previews</h2>'] + [f"<img class=pv src='preview/{p['file']}'>" for p in pages]
    body += ['<h2>All crops</h2>'] + [card(q) for q in questions]
    (out / 'review.html').write_text(
        '<meta name=viewport content="width=device-width,initial-scale=1"><style>'
        'body{font-family:Arial;margin:12px}.c{border:1px solid #bbb;margin:10px 0;padding:8px}'
        '.c img{max-width:100%}.fl{color:#b91c1c;font-size:13px}.pv{max-width:100%;border:1px solid #888;margin:6px 0}'
        '</style>' + '\n'.join(body))
    return flagged


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', nargs='?', help='question paper PDF, or a folder of page images')
    ap.add_argument('--out', default='boundaries_out', help='output folder')
    ap.add_argument('--key', metavar='KEY_PDF', help='separate answer-key PDF (if the key is not inside the paper PDF)')
    ap.add_argument('--source-compat', action='store_true', help='use conservative source-proven layout/key compatibility; retain original cleaning and crop geometry')
    ap.add_argument('--key-base', metavar='KEY_PDF', help='complete source key accompanying an explicitly selected final correction sheet')
    ap.add_argument('--add-key-policy', choices=('unresolved', 'original'), default='unresolved',
                    help='explicit adjudication for an Add correction marker; original keeps its matching base answer')
    ap.add_argument('--no-clean', action='store_true', help='do NOT remove watermarks / faint marks from the page images')
    ap.add_argument('--keep-color', action='store_true', help='keep coloured ink (by default coloured pen marks / highlighter are removed, which also removes coloured diagrams)')
    ap.add_argument('--diagnose', action='store_true', help='(for debugging only) write previews even when the upload is refused; never used by the Mac launcher')
    ap.add_argument('--recrop', metavar='OUTDIR', help='re-crop from an edited boundaries.json')
    ap.add_argument('--dpi', type=int, default=DPI)
    ap.add_argument('--pad', type=int, default=14, help='white margin around each question (px)')
    ap.add_argument('--skip-top', type=float, default=0.04, help='ignore this fraction of page top (header)')
    ap.add_argument('--skip-bottom', type=float, default=0.04, help='ignore this fraction of page bottom (footer)')
    args = ap.parse_args()
    if args.key_base and (not args.source_compat or not args.key):
        ap.error('--key-base requires --source-compat and --key')

    if args.recrop:
        out = Path(args.recrop)
        data = json.loads((out / 'boundaries.json').read_text())
        make_crops(out, data['pages'], data['questions'])
        draw_previews(out, data['pages'], data['questions'])
        write_review(out, data['pages'], data['questions'])
        print(f'Re-cropped {len(data["questions"])} questions in {out}/crops')
        return

    if not args.source:
        ap.error('give a PDF or a folder of page images')
    src, out = Path(args.source), Path(args.out)
    pages_dir = out / 'pages'
    if src.is_dir():
        files, pdf = load_images(src, pages_dir), None
    else:
        files, pdf = render_pdf(src, pages_dir, args.dpi), src
    cleaned = {}
    if not args.no_clean:
        orig = out / 'pages_original'
        orig.mkdir(exist_ok=True)
        for f in files:
            im = Image.open(f).convert('RGB')
            im.save(orig / f.name)
            im2, pct = clean_image(im, remove_color=not args.keep_color)
            im2.save(f)
            cleaned[f.name] = round(pct, 2)
    pages, questions, key = detect(pdf, files, args)
    for pg in pages:
        pg['cleaned_pct'] = cleaned.get(pg['file'], 0)
    source_key = None
    if args.key and args.source_compat:
        from source_keys import read_key_sources
        source_key = read_key_sources(base_path=args.key_base, final_path=args.key,
                                      expected_identity=getattr(args, '_source_identity', None),
                                      expected_numbers=(range(1, args._source_layout['expected_count'] + 1)
                                                        if getattr(args, '_source_layout', None) else None),
                                      add_policy=args.add_key_policy,
                                      legacy_parser=parse_key)
        key = source_key['key']
        # Textless keys retain the original local OCR reader. A recognized
        # correction sheet must never fall back to stale base answers.
        if not key and source_key.get('legacy_fallback_allowed'):
            key = key_from_file(args.key, args.dpi)
            if key:
                source_key['key'] = key
                source_key['unresolved'] = [reason for reason in source_key['unresolved']
                                            if reason != 'No complete source key was parsed.']
                source_key['reader_fallback'] = 'original_same_document_local_reader'
        (out / 'source_key.json').write_text(json.dumps(source_key, indent=1))
    elif args.key:
        key = key_from_file(args.key, args.dpi)
    why = gate(pages, questions, key)
    layout = getattr(args, '_source_layout', None)
    if layout:
        evidence = {name: value for name, value in layout.items() if name != 'anchors_by_page'}
        evidence['question_count_detected'] = len(questions)
        (out / 'source_layout.json').write_text(json.dumps(evidence, indent=1))
        if len(questions) != layout['expected_count']:
            why.append('detected question count does not match the printed source declaration')
    if why:
        out.mkdir(parents=True, exist_ok=True)
        msg = 'REFUSED - this upload does not meet the required format:\n' + '\n'.join(f'  - {w}' for w in why) + \
              '\nRequired: a question paper (numbered questions with options) AND an answer key that matches it.\n'
        (out / 'REFUSED.txt').write_text(msg)
        print(msg)
        if args.diagnose:
            (out / 'boundaries.json').write_text(json.dumps(dict(dpi=args.dpi, pages=pages, questions=questions), indent=1))
            draw_previews(out, pages, questions)
        sys.exit(2)
    (out / 'boundaries.json').write_text(json.dumps(dict(dpi=args.dpi, pages=pages, questions=questions), indent=1))
    draw_previews(out, pages, questions)
    make_crops(out, pages, questions)
    flagged = write_review(out, pages, questions)
    exam = [dict(number=q['number'], image=f"crops/Q{q['number']:03d}.png", answer=key.get(q['number'])) for q in questions]
    for item in exam:
        item.update(getattr(args, '_source_types', {}).get(item['number'], {}))
    export = dict(source=src.name, count=len(exam), questions=exam)
    if source_key is not None:
        export['source_key'] = {name: value for name, value in source_key.items() if name != 'key'}
    if layout:
        export['source_layout'] = evidence
    (out / 'exam.json').write_text(json.dumps(export, indent=1))
    print((out / 'REPORT.txt').read_text())
    if source_key and source_key.get('unresolved'):
        print(f'EXTRACTED: {len(questions)} question images; source-key adjudication remains unresolved. Exam grading is held.')
    else:
        print(f'ACCEPTED: {len(questions)} questions, each with its answer. {len(flagged)} flagged. Open {out}/review.html')
    print('Crops in crops/ are clean page pixels: no boxes, labels or marks are ever drawn on them (boxes appear only in preview/).')


if __name__ == '__main__':
    main()
