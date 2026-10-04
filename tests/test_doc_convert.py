"""Office files and PDFs as text (agent/doc_convert.py).

The sample set from scripts/doc_acceptance.py runs through the same door the
knowledge base, the read_file tool and the demo use. The guard rails are
checked as well: local files only, size limits, a scan named as a scan
(never empty success), and the old PDF reader still working without
markitdown.
"""
import pytest

from agent import doc_convert, knowledge
from scripts import doc_acceptance
from tools import files


@pytest.fixture(scope='module')
def sample(tmp_path_factory):
    return doc_acceptance.make_sample(tmp_path_factory.mktemp('docs'))


def test_sample_set_passes_the_acceptance_check(sample):
    rows = doc_acceptance.check(sample)
    assert [r['file'] for r in rows if r['verdict'] != 'pass'] == []
    assert {r['file']: r['method'] for r in rows}['budget.xlsx'] == 'markitdown'


@pytest.mark.parametrize('name,fact', [
    ('kickoff.docx', 'second power strip'), ('kickoff.docx', 'Shed'),      # a table cell in Word
    ('plan.pptx', 'Shelving budget 120'), ('budget.xlsx', 'Hardware store'),  # the second sheet too
    ('notes.pdf', 'Electrician visit Friday 10:00'),
])
def test_facts_survive_conversion(sample, name, fact):
    assert fact in doc_convert.convert(sample / name)['text']


def test_a_scan_is_named_as_a_scan(sample):
    result = doc_convert.convert(sample / 'scan.pdf')
    assert result['status'] == 'needs_ocr' and 'OCR' in result['note'] and not result['text'].strip()
    assert 'no text layer' in files.read(str(sample / 'scan.pdf'))


@pytest.mark.parametrize('bad,message', [
    ('https://example.com/report.docx', 'Only local files'),
    ('C:/notes/plan.exe', 'not a document type'),
])
def test_only_local_documents(bad, message):
    with pytest.raises(doc_convert.ConvertError, match=message):
        doc_convert.convert(bad)


def test_missing_oversized_and_broken_files(tmp_path, sample, monkeypatch):
    with pytest.raises(doc_convert.ConvertError, match='does not exist'):
        doc_convert.convert(tmp_path / 'gone.docx')
    good = (sample / 'kickoff.docx').read_bytes()
    (tmp_path / 'cut.docx').write_bytes(good[:len(good) // 2])          # a download that stopped halfway
    (tmp_path / 'noise.pdf').write_bytes(bytes(range(256)) * 16)
    for broken in ('cut.docx', 'noise.pdf'):
        with pytest.raises(doc_convert.ConvertError, match='could not be read'):
            doc_convert.convert(tmp_path / broken)
    monkeypatch.setattr(doc_convert, 'MAX_BYTES', 10)
    with pytest.raises(doc_convert.ConvertError, match='over'):
        doc_convert.convert(sample / 'kickoff.docx')


def test_long_output_is_cut_and_says_so(sample, monkeypatch):
    monkeypatch.setattr(doc_convert, 'MAX_CHARS', 20)
    result = doc_convert.convert(sample / 'kickoff.docx')
    assert result['status'] == 'truncated' and len(result['text']) == 20 and 'first 20' in result['note']


def test_without_markitdown_pdfs_still_read_and_office_files_say_what_to_install(sample, monkeypatch):
    monkeypatch.setattr(doc_convert, 'available', lambda: False)
    result = doc_convert.convert(sample / 'notes.pdf')
    assert result['method'] == 'pypdf' and 'Outlet circuit check' in result['text']
    with pytest.raises(doc_convert.ConvertError, match='markitdown'):
        doc_convert.convert(sample / 'kickoff.docx')


def test_markitdown_is_never_given_a_url_or_plugins(sample, monkeypatch):
    seen = {}

    class Spy:
        def __init__(self, **kwargs):
            seen['kwargs'] = kwargs

        def convert_local(self, path):
            seen['path'] = path
            return type('R', (), {'markdown': 'ok'})()

        def convert(self, *a, **k):              # the URL-capable entry point
            raise AssertionError('convert() must not be used')
    import markitdown
    monkeypatch.setattr(markitdown, 'MarkItDown', Spy)
    doc_convert.convert(sample / 'kickoff.docx')
    assert seen['kwargs'] == {'enable_plugins': False} and seen['path'].endswith('kickoff.docx')


def test_read_file_tool_returns_text_not_zip_bytes(sample):
    text = files.read(str(sample / 'budget.xlsx'))
    assert 'Shelving' in text and 'PK' not in text[:2]


def test_knowledge_base_indexes_office_files(sample):
    assert '.docx' in knowledge.ALLOWED_EXTS and '.xlsx' in knowledge.ALLOWED_EXTS
    assert 'Move the 3D printer' in knowledge._read_file(sample / 'plan.pptx')
    assert knowledge._read_file(sample / 'scan.pdf') is None        # nothing to index, rather than an empty chunk
    assert knowledge._size_ok(sample / 'kickoff.docx')
