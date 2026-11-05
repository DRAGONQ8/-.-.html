# Image delivery regression tests

`ZIP_Launcher_V4_Fixed.html` is the standalone release copy of the Arabic-named source; JSZip is bundled in the document.

Run the extraction and diagnostics checks with Node.js:

```sh
node --test tests/test_extraction_race.js tests/test_extraction_diagnostics.js
```

Install Python Playwright and Chromium for the browser tests. Set `CHROMIUM` if the executable is not `/usr/bin/chromium`:

```sh
python tests/test_image_runtime.py ZIP_Launcher_V4_Fixed.html
python tests/test_save_launcher.py
```

The controlled browser suite creates temporary ZIP fixtures and an instrumented copy of the launcher to control message timing. The Seen Jeem integration test uses the unmodified artifact.
The save test downloads the launcher twice, checks that live project content is not included, and opens a ZIP and its image in the saved launcher while offline.

For integration, create a ZIP of the game with prefix `S.V.html-main/`, then run:

```sh
python tests/test_seen_jeem.py --artifact ZIP_Launcher_V4_Fixed.html --zip /path/to/seen-jeem.zip --offline
python tests/test_seen_jeem.py --artifact ZIP_Launcher_V4_Fixed.html --zip /path/to/seen-jeem.zip --mobile
```

The integration test drives the game and biases its in-memory selection history to select known questions; it does not change game files. It verifies sushi, koshary, harira and, when present, the newer mansaf image against the actual archive bytes before and after reopening. It also checks two earlier question images. The original failing ZIP was not available. These tests do not replace physical Android or long-running memory-pressure tests. The iframe retains requested Blob resources until the runtime closes.
