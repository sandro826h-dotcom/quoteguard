"""Find characters present in the .docx body text but missing from the PDF text layer (e.g. line-final
punctuation dropped by the PDF export). Usage: python check_pdf_text.py file.docx file.pdf"""
import re, subprocess, sys, zipfile, difflib

docx, pdf = sys.argv[1], sys.argv[2]
xml = zipfile.ZipFile(docx).read('word/document.xml').decode('utf-8')
paras = [''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', p)) for p in re.findall(r'<w:p[ >].*?</w:p>', xml)]
src = re.sub(r'\s+', '', ''.join(paras))
txt = subprocess.run(['pdftotext', pdf, '-'], capture_output=True, text=True).stdout
dst = re.sub(r'\s+', '', txt)
sm = difflib.SequenceMatcher(None, src, dst, autojunk=False)
miss = []
for op, a0, a1, b0, b1 in sm.get_opcodes():
    if op in ('delete', 'replace') and a1 - a0 <= 3:
        miss.append((src[a0:a1], dst[b0:b1], src[max(0, a0 - 12):a1 + 8]))
for m in miss:
    print(repr(m[0]), '->', repr(m[1]), '|', m[2])
print('missing spots:', len(miss))
