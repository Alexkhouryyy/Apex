# Word, PowerPoint, Excel and PDF as sources

Apex reads these files as text wherever it reads files:
- the agent's **read_file** tool: "read C:\work\kickoff.docx" gives the words, not zip bytes;
- the **knowledge base**, so they're found by search;
- the **demonstration** (`scripts\apex_demo.py --source kickoff.docx`).

They all go through one converter, `agent/doc_convert.py`, which uses
Microsoft's [markitdown](https://github.com/microsoft/markitdown) (MIT). Why
that one, and why not the others, is in
[OPEN_SOURCE_REGISTER.md](OPEN_SOURCE_REGISTER.md).

| File | What comes out |
| --- | --- |
| `.docx` | Headings, paragraphs and tables (as markdown tables) |
| `.pptx` | Each slide's title and text |
| `.xlsx` | Every sheet, as a table |
| `.pdf` with text | The text. If markitdown can't read it, pypdf tries, as before |
| Scanned `.pdf` | Nothing, and it says **needs OCR** instead of pretending it read an empty page |

## Install

It's in `requirements.txt`, so `Apex.bat` installs it. By hand:

```cmd
.venv\Scripts\pip install "markitdown[docx,pptx,xlsx,pdf]==0.1.8"
```

Without it, PDFs still work through pypdf, and office files say what to install.

## Limits, on purpose

- **Local files only.** markitdown can also fetch web pages, YouTube and
  Azure services. Apex never passes it an address, and its plugins are off.
- **Up to 25 MB per file.** At most 2 million characters are kept; when it
  cuts, it says so.
- **No OCR.** Scanned PDFs need a heavier tool (docling, in the register).
  It isn't installed yet, so a scan is reported as one.
- **Spreadsheet formulas** show their saved value. A sheet that was never
  opened in Excel since its formulas were written may show them as empty.
- **Loading time:** loading the converter takes about a second, once per
  Apex start. After that a normal file takes hundredths of a second.

## Check it on your own files

This is the register's acceptance test. Put about 10 of your real files in a
folder: Word, PowerPoint, Excel, a couple of PDFs with tables and one scan.
Add a `facts.json` that lists a few phrases from each file that must come
through:

```json
{
  "budget.xlsx": ["Shelving", "120"],
  "kickoff.docx": ["second power strip"],
  "scan.pdf": []
}
```

Then:

```cmd
.venv\Scripts\python scripts\doc_acceptance.py C:\path\to\folder
```

Each file says **PASS** or **FAIL**, with its time and any missing phrase.
Send back the summary line. To see it work first, this writes a sample set and
checks it:

```cmd
.venv\Scripts\python scripts\doc_acceptance.py --make-sample %TEMP%\apex-docs
```
