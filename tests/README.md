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

For integration, create a ZIP of Seen Jeem commit `ba408fad868df4996caa7efb8552a88ae367f75c` with prefix `S.V.html-main/`, then run:

```sh
python tests/test_seen_jeem.py --artifact ZIP_Launcher_V4_Fixed.html --zip /path/to/seen-jeem.zip --offline
python tests/test_seen_jeem.py --artifact ZIP_Launcher_V4_Fixed.html --zip /path/to/seen-jeem.zip --mobile
```

The integration test drives the game and biases its in-memory selection history to select the two known question IDs; it does not change game files. The expected image hashes correspond to the pinned commit. The original failing ZIP was not available. A game resume banner was intermittently absent in one mobile-emulated run. These tests do not replace physical Android or long-running memory-pressure tests. The iframe retains requested Blob resources until the runtime closes.
