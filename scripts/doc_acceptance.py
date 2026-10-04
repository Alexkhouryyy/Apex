"""The document acceptance check from docs/OPEN_SOURCE_REGISTER.md.

    python scripts/doc_acceptance.py <folder>               # your files + facts.json
    python scripts/doc_acceptance.py --make-sample <folder>  # write a sample set first

Each file in the folder goes through agent/doc_convert.py, the same door the
knowledge base, the read_file tool and the demo use. `facts.json` lists, per
file name, short phrases that must appear in the converted text:

    {"budget.xlsx": ["Shelving", "120"], "kickoff.docx": ["power strip"]}

Passing means every listed fact is found and the file converted in under
5 s; a scanned PDF passes only if it is reported as needing OCR, never as
empty text. Facts are matched ignoring case and spacing.
"""
import argparse
import json
import re
import sys
import time
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SECONDS = 5.0


def _norm(text):
    return re.sub(r'\s+', ' ', text).strip().lower()


# ---------------------------------------------------------------- sample set

def make_docx(path, heading, paragraphs, table):
    """A minimal valid .docx: one heading, paragraphs and a table."""
    def run(text):
        return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
    body = f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>{run(heading)}</w:p>'
    body += ''.join(f'<w:p>{run(p)}</w:p>' for p in paragraphs)
    rows = ''.join('<w:tr>' + ''.join(f'<w:tc><w:p>{run(c)}</w:p></w:tc>' for c in row) + '</w:tr>' for row in table)
    body += f'<w:tbl>{rows}</w:tbl>'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body>{body}</w:body></w:document>')


def make_pptx(path, slides):
    from pptx import Presentation
    deck = Presentation()
    for title, bullets in slides:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = '\n'.join(bullets)
    deck.save(path)


def make_xlsx(path, sheets):
    from openpyxl import Workbook
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    book.save(path)


def _pdf(objects):
    out, offsets = bytearray(b'%PDF-1.4\n'), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    out += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    out += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return bytes(out)


def _stream(data, extra=b''):
    return b'<< /Length %d %s >>\nstream\n' % (len(data), extra) + data + b'\nendstream'


def make_text_pdf(path, lines):
    """One page of real text (a text layer), lines top to bottom."""
    ops = b'BT /F1 12 Tf 14 TL 72 760 Td ' + b' '.join(
        b'(' + line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)').encode('latin-1') + b') Tj T*' for line in lines) + b' ET'
    Path(path).write_bytes(_pdf([
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
        _stream(ops),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']))


def make_scanned_pdf(path):
    """One page that is only a picture: what a scanner produces."""
    w = h = 64
    pixels = bytes((x * 4) % 256 for y in range(h) for x in range(w))
    image = zlib.compress(pixels)
    Path(path).write_bytes(_pdf([
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> >> >>',
        _stream(b'q 400 0 0 400 100 300 cm /Im1 Do Q'),
        _stream(image, b'/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode' % (w, h))]))


SAMPLE_FACTS = {
    'kickoff.docx': ['Garage workshop kickoff', 'second power strip', 'Compressor', 'Shed'],
    'plan.pptx': ['Workshop plan', 'Move the 3D printer away from the window', 'Shelving budget 120'],
    'budget.xlsx': ['Shelving', '120', 'Power strip', '35', 'Suppliers'],
    'notes.pdf': ['Outlet circuit check', 'Electrician visit Friday 10:00'],
    'scan.pdf': [],
}


def make_sample(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    make_docx(folder / 'kickoff.docx', 'Garage workshop kickoff',
              ['The workbench needs a second power strip before the drill press arrives.'],
              [['Item', 'Where'], ['Compressor', 'Shed'], ['3D printer', 'Away from the window']])
    make_pptx(folder / 'plan.pptx', [('Workshop plan', ['Move the 3D printer away from the window', 'Shelving budget 120'])])
    make_xlsx(folder / 'budget.xlsx', {'Budget': [['Item', 'Cost'], ['Shelving', 120], ['Power strip', 35]],
                                       'Suppliers': [['Name', 'Phone'], ['Hardware store', '01 234 567']]})
    make_text_pdf(folder / 'notes.pdf', ['Outlet circuit check', 'Electrician visit Friday 10:00'])
    make_scanned_pdf(folder / 'scan.pdf')
    (folder / 'facts.json').write_text(json.dumps(SAMPLE_FACTS, indent=1))
    return folder


# ---------------------------------------------------------------- the check

def check(folder):
    from agent import doc_convert
    folder = Path(folder)
    facts = json.loads((folder / 'facts.json').read_text(encoding='utf-8'))
    rows = []
    started = time.perf_counter()
    doc_convert.warm_up()                      # once per Apex start; reported, not charged to the first file
    check.loading_seconds = round(time.perf_counter() - started, 2)
    for name, wanted in facts.items():
        path = folder / name
        started = time.perf_counter()
        try:
            result = doc_convert.convert(path)
        except doc_convert.ConvertError as exc:
            rows.append(dict(file=name, verdict='fail', seconds=None, method=None, status='error', missing=wanted, note=str(exc)))
            continue
        seconds = round(time.perf_counter() - started, 2)
        text = _norm(result['text'])
        missing = [f for f in wanted if _norm(f) not in text]
        if result['status'] == 'needs_ocr':
            verdict = 'pass' if not wanted else 'fail'    # a scan must be named as one; facts in it can't be read yet
        else:
            verdict = 'pass' if not missing and seconds <= SECONDS else 'fail'
        rows.append(dict(file=name, verdict=verdict, seconds=seconds, method=result['method'],
                         status=result['status'], missing=missing, note=result['note']))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('folder')
    parser.add_argument('--make-sample', action='store_true', help='write the sample set into the folder first')
    args = parser.parse_args(argv)
    if args.make_sample:
        make_sample(args.folder)
    if not (Path(args.folder) / 'facts.json').exists():
        print('No facts.json in that folder. See the top of this script for its format.')
        return 2
    rows = check(args.folder)
    print(f'Loading the converter took {check.loading_seconds:.2f}s (once per Apex start).\n')
    for r in rows:
        extra = f"missing: {', '.join(r['missing'])}" if r['missing'] else r['note'] or ''
        took = f"{r['seconds']:.2f}s" if r['seconds'] is not None else '  -  '
        print(f"[{r['verdict'].upper():4}] {r['file']:<28} {took:>7}  {r['method'] or '-':<10} {r['status']:<10} {extra}")
    failed = sum(r['verdict'] == 'fail' for r in rows)
    print(f'\n{len(rows) - failed}/{len(rows)} files pass (every fact found, under {SECONDS:.0f} s each; scans named as scans).')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
