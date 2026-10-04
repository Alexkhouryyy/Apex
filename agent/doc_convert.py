"""Office files and PDFs as text: one door for every reader in Apex.

Projects start from "a brief and supporting documents", and supporting
documents are usually Word, PowerPoint, Excel or PDF. Apex read PDFs with
pypdf and everything else as plain text, so a .docx came back as zip bytes.
This converts them with Microsoft's markitdown (MIT; see
docs/OPEN_SOURCE_REGISTER.md), kept deliberately narrow:

- **Local files only.** markitdown can also fetch URLs and YouTube pages, and
  has Azure and LLM features. None of that is reachable from here: the input
  must be an existing local file with an allowed extension, and only
  `convert_local` is called, with plugins off.
- **Bounded.** Inputs over MAX_BYTES are refused; output is cut at MAX_CHARS.
- **Honest about failure.** A PDF with no text layer (a scan) is reported as
  `needs_ocr`, never as an empty success.
- **Optional.** Without markitdown installed, PDFs still go through pypdf as
  before, and office files say what to install.
"""
from __future__ import annotations

from pathlib import Path

OFFICE = frozenset({'.docx', '.pptx', '.xlsx'})
CONVERTIBLE = OFFICE | {'.pdf'}
MAX_BYTES = 25_000_000
MAX_CHARS = 2_000_000
INSTALL = 'pip install "markitdown[docx,pptx,xlsx,pdf]==0.1.8"'


class ConvertError(ValueError):
    pass


def available() -> bool:
    try:
        import markitdown  # noqa: F401
        return True
    except Exception:
        return False


def warm_up() -> None:
    """Load the converter and its file-type model now rather than on the
    first document (under a second to about 2 s, once per process)."""
    if available():
        import io
        from markitdown import MarkItDown
        MarkItDown(enable_plugins=False).convert_stream(io.BytesIO(b'warm'), file_extension='.txt')


def _markitdown(path: Path) -> str:
    from markitdown import MarkItDown
    return MarkItDown(enable_plugins=False).convert_local(str(path)).markdown or ''


def _pypdf(path: Path) -> str:
    from pypdf import PdfReader
    return '\n'.join(page.extract_text() or '' for page in PdfReader(str(path)).pages)


def convert(path) -> dict:
    """{text, method, status, note}. `status` is `ok`, `needs_ocr` (a PDF
    with no text layer) or `truncated`. Raises ConvertError for anything that
    isn't a convertible local file."""
    if not isinstance(path, (str, Path)) or '://' in str(path):
        raise ConvertError('Only local files can be converted, not addresses.')
    path = Path(path).expanduser()
    suffix = path.suffix.lower()
    if suffix not in CONVERTIBLE:
        raise ConvertError(f'{suffix or "This file"} is not a document type Apex converts.')
    if not path.is_file():
        raise ConvertError(f'{path.name} does not exist.')
    if path.stat().st_size > MAX_BYTES:
        raise ConvertError(f'{path.name} is over {MAX_BYTES // 1_000_000} MB.')

    method, text, problems = None, '', []
    if available():
        try:
            text, method = _markitdown(path), 'markitdown'
        except Exception as exc:          # a malformed file: say so, and try the old reader for PDFs
            problems.append(f'markitdown could not read it ({type(exc).__name__}).')
    if not text.strip() and suffix == '.pdf':
        try:
            text, method = _pypdf(path), 'pypdf'
        except Exception as exc:
            problems.append(f'pypdf could not read it ({type(exc).__name__}).')
    if method is None:
        if suffix in OFFICE and not available():
            raise ConvertError(f'Reading {suffix} files needs markitdown: {INSTALL}')
        raise ConvertError(f'{path.name} could not be read. ' + ' '.join(problems))

    status, note = 'ok', ' '.join(problems)
    if not text.strip():
        if suffix == '.pdf':
            status, note = 'needs_ocr', 'This PDF has no text layer (probably a scan). It needs OCR, which is not installed.'
        else:
            note = (note + ' The file has no text.').strip()
    elif len(text) > MAX_CHARS:
        text, status = text[:MAX_CHARS], 'truncated'
        note = (note + f' Only the first {MAX_CHARS:,} characters were kept.').strip()
    return dict(text=text, method=method, status=status, note=note)


def read_text(path) -> str:
    """For readers that want a string: the text, or a bracketed reason."""
    try:
        result = convert(path)
    except ConvertError as exc:
        return f'[Could not read {Path(str(path)).name}: {exc}]'
    if result['status'] == 'needs_ocr':
        return f'[{Path(str(path)).name}: {result["note"]}]'
    return result['text'] + (f'\n\n[{result["note"]}]' if result['note'] else '')
