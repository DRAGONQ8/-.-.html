#!/usr/bin/env python3
"""Exercise downloading and reopening the launcher with Chromium and Playwright."""
import functools
import base64
import http.server
import os
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / 'ZIP_Launcher_V4_Fixed.html'


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class SaveLauncherTest(unittest.TestCase):
    def test_saved_launcher_reopens_and_imports_zip_without_runtime_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            archive = tmp / 'fixture.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('index.html', '<h1 id="test-content">ZIP works</h1><img id="picture" src="picture.png">')
                z.writestr('picture.png', base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1sAAAAASUVORK5CYII='))
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(tmp)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                (tmp / 'launcher.html').write_bytes(ARTIFACT.read_bytes())
                with sync_playwright() as p:
                    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM', '/usr/bin/chromium'), args=['--no-sandbox'])
                    page = browser.new_page(accept_downloads=True, viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
                    page.goto(f'http://127.0.0.1:{server.server_port}/launcher.html', wait_until='domcontentloaded')
                    button = page.locator('#saveLauncherHtmlBtn')
                    self.assertEqual(button.count(), 1, 'A save launcher button should be available on the home screen')
                    page.evaluate("document.body.dataset.privateRuntimeMarker = 'DO_NOT_EXPORT_RUNTIME_DATA'")
                    with page.expect_download() as event:
                        button.click()
                    download = event.value
                    self.assertEqual(download.suggested_filename, 'ZIP_Launcher_V4_Fixed.html')
                    saved = tmp / 'saved.html'
                    download.save_as(saved)
                    self.assertNotIn('DO_NOT_EXPORT_RUNTIME_DATA', saved.read_text(encoding='utf-8'))
                    page.evaluate("localStorage.setItem('zip_launcher_v4_prompt_naming', '0')")
                    page.goto(f'http://127.0.0.1:{server.server_port}/saved.html', wait_until='domcontentloaded')
                    self.assertTrue(page.locator('#saveLauncherHtmlBtn').is_visible())
                    page.locator('#zipFileInput').set_input_files(str(archive))
                    page.frame_locator('#viewer').locator('#test-content').wait_for(timeout=30000)
                    self.assertEqual(page.frame_locator('#viewer').locator('#test-content').inner_text(), 'ZIP works')
                    page.frame_locator('#viewer').locator('#picture').evaluate("img => new Promise((resolve, reject) => {if (img.complete) return img.naturalWidth ? resolve() : reject(Error('broken image')); img.onload=resolve; img.onerror=reject;})")
                    page.locator('#moreMenuBtn').click()
                    with page.expect_download() as second_event:
                        page.locator('#menuSaveLauncherHtmlBtn').click()
                    second = tmp / 'saved-again.html'
                    second_event.value.save_as(second)
                    self.assertNotIn('ZIP works', second.read_text(encoding='utf-8'))
                    page.goto(f'http://127.0.0.1:{server.server_port}/saved-again.html', wait_until='domcontentloaded')
                    page.context.set_offline(True)
                    page.locator('#zipFileInput').set_input_files(str(archive))
                    self.assertEqual(page.frame_locator('#viewer').locator('#test-content').inner_text(timeout=30000), 'ZIP works')
                    page.frame_locator('#viewer').locator('#picture').evaluate("img => new Promise((resolve, reject) => {if (img.complete) return img.naturalWidth ? resolve() : reject(Error('broken image')); img.onload=resolve; img.onerror=reject;})")
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
