"""Finish the generated .docx: give every bookmark its own w:id (docx-js writes w:id="1" for all,
which Word may report as damage) and drop the ZIP's folder entries. Usage: python finalize_docx.py <docx>"""
import re
import shutil
import sys
import zipfile

src = sys.argv[1]
tmp = src + ".tmp"
with zipfile.ZipFile(src) as zi:
    xml = zi.read("word/document.xml").decode("utf-8")
    counter = [0]
    open_ids = []

    def start(m):
        counter[0] += 1
        open_ids.append(counter[0])
        return re.sub(r'w:id="\d+"', f'w:id="{counter[0]}"', m.group(0))

    def end(m):
        return re.sub(r'w:id="\d+"', f'w:id="{open_ids.pop(0)}"', m.group(0))

    # each Bookmark is <w:bookmarkStart .../> ... <w:bookmarkEnd .../> in order, not nested
    out, pos = [], 0
    for m in re.finditer(r"<w:bookmark(Start|End)\b[^>]*/>", xml):
        out.append(xml[pos:m.start()])
        out.append(start(m) if m.group(1) == "Start" else end(m))
        pos = m.end()
    out.append(xml[pos:])
    xml = "".join(out)
    assert not open_ids, "unbalanced bookmarks"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in zi.infolist():
            if info.filename.endswith("/"):
                continue
            data = xml.encode("utf-8") if info.filename == "word/document.xml" else zi.read(info.filename)
            zo.writestr(info, data)
shutil.move(tmp, src)
print("bookmarks renumbered:", counter[0])
