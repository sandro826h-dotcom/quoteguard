"""Export the paper .docx to PDF through ODT with hanging punctuation off.

LibreOffice ignores w:overflowPunct on .docx import and, with hanging punctuation on, drops some
line-final Chinese punctuation in justified text. This converts to .odt, sets the default style to
style:punctuation-wrap="simple", and exports a tagged PDF with bookmarks.
Usage: python export_paper_pdf.py paper.docx out.pdf
"""
import shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
tmp = Path(tempfile.mkdtemp())
shutil.copy(src, tmp / 'paper.docx')
subprocess.run(['soffice', '--headless', '--convert-to', 'odt', 'paper.docx'], cwd=tmp, check=True, capture_output=True, timeout=180)
odt, odt2 = tmp / 'paper.odt', tmp / 'paper2.odt'
with zipfile.ZipFile(odt) as zin, zipfile.ZipFile(odt2, 'w') as zout:
    for info in zin.infolist():
        data = zin.read(info.filename)
        if info.filename in ('styles.xml', 'content.xml'):
            data = data.decode('utf-8').replace('punctuation-wrap="hanging"', 'punctuation-wrap="simple"').encode('utf-8')
        zout.writestr(info, data, compress_type=zipfile.ZIP_STORED if info.filename == 'mimetype' else zipfile.ZIP_DEFLATED)
opts = 'pdf:writer_pdf_Export:{"UseTaggedPDF":{"type":"boolean","value":"true"},"ExportBookmarks":{"type":"boolean","value":"true"}}'
subprocess.run(['soffice', '--headless', '--convert-to', opts, 'paper2.odt'], cwd=tmp, check=True, capture_output=True, timeout=180)
shutil.copy(tmp / 'paper2.pdf', out)
print('wrote', out)
