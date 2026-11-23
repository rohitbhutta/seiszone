# SeismoVerse user manual - how it is built

The finished manual is in `docs/manual/` (`SeismoVerse_User_Manual.docx` and `.pdf`).
The text lives in `make_manual.js`. Both the Word file and the PDF are made from it, so they
always say the same.

| File | What it does |
|------|--------------|
| `shots.py` | Screenshots of every window, offscreen, with the test suite's synthetic data (no instrument, no network, a temporary settings folder) |
| `shots/` | The screenshots used in the manual |
| `make_manual.js` | The manual's text and layout -> `.docx`, plus the same content as `.json` for the PDF |
| `finalize_docx.py` | Gives every bookmark its own id and drops the ZIP folder entries (Word may report either as damage) |
| `make_pdf.py` | The PDF (reportlab) from the `.json`, with contents page numbers and PDF bookmarks |

Rebuild (from the project folder, into `build/manual`):

```
python tools/manual/shots.py . tools/manual/shots
cd build/manual && npm install docx@9.5.1 && cd ../..
node tools/manual/make_manual.js tools/manual/shots assets/seiszone.png build/manual/SeismoVerse_User_Manual.docx
python tools/manual/finalize_docx.py build/manual/SeismoVerse_User_Manual.docx
python -m pip install reportlab
python tools/manual/make_pdf.py build/manual/SeismoVerse_User_Manual.json tools/manual/shots assets/seiszone.png build/manual/SeismoVerse_User_Manual.pdf
```

Then check the PDF pages and copy both files to `docs/manual/`. `node` must find the `docx` package
(run it from `build/manual`, or set `NODE_PATH=build/manual/node_modules`).
