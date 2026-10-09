"""Set the East Asian theme fonts of a .pptx to the serif face (pptxgenjs/applyTheme only set Latin).

Usage: python fix_theme_fonts.py deck.pptx "Noto Serif CJK SC"
Rewrites ppt/theme/theme*.xml in place: <a:ea typeface> and the Hans script font of the major and
minor font schemes. Other parts are copied byte for byte in their original order.
"""
import re, shutil, sys, tempfile, zipfile

path, face = sys.argv[1], sys.argv[2]
tmp = tempfile.mktemp(suffix=".pptx")
with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    for info in zin.infolist():
        data = zin.read(info.filename)
        if re.fullmatch(r"ppt/theme/theme\d+\.xml", info.filename):
            xml = data.decode("utf-8")
            xml = xml.replace('<a:ea typeface=""/>', f'<a:ea typeface="{face}"/>')
            xml = re.sub(r'<a:font script="Hans" typeface="[^"]*"/>', f'<a:font script="Hans" typeface="{face}"/>', xml)
            data = xml.encode("utf-8")
        zout.writestr(info, data)
shutil.move(tmp, path)
print("theme EA font ->", face)
