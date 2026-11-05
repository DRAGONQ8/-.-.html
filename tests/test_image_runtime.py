#!/usr/bin/env python3
"""Browser regressions for ZIP image delivery. Requires Python Playwright and Chromium.

Run: python tests/test_image_runtime.py [path/to/launcher.html]
The archive is generated in a temporary directory; no network access is required.
"""
import base64
import functools
import http.server
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
import zlib
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 else next(ROOT.glob('*Ultimate*.html'))

def png(w, h, color):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    pixels = b''.join(b'\0' + bytes(color) * w for _ in range(h))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB',w,h,8,2,0,0,0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass

class ImageRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.zip = Path(cls.tmp.name) / 'fixture.zip'
        with zipfile.ZipFile(cls.zip, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('root/index.html', '<!doctype html><meta charset="utf-8"><body><div id="fixture"></div></body>')
            for i in range(100): z.writestr(f'root/assets/pic{i}.png', png(2+i%5,3+i%7,(i,50,100)))
            z.writestr('root/assets/صورة one.png', png(11,13,(1,200,3)))
        cls.corrupt_zip = Path(cls.tmp.name) / 'corrupt-crc.zip'
        data = bytearray(cls.zip.read_bytes())
        offset = 0
        while True:
            offset = data.find(b'PK\x01\x02', offset)
            if offset < 0: raise AssertionError('central directory entry not found')
            name_len, extra_len, comment_len = struct.unpack_from('<HHH', data, offset + 28)
            name = bytes(data[offset + 46:offset + 46 + name_len])
            if name == b'root/assets/pic0.png':
                crc = struct.unpack_from('<I', data, offset + 16)[0]
                struct.pack_into('<I', data, offset + 16, crc ^ 1)
                break
            offset += 46 + name_len + extra_len + comment_len
        cls.corrupt_zip.write_bytes(data)
        # Inject only the deterministic message interceptor into a temporary copy.
        # All launcher code under test is otherwise the exact artifact supplied above.
        source = LAUNCHER.read_text(encoding='utf-8')
        marker = 'function iframeVirtualRuntimeBootstrap(cfg) {'
        if source.count(marker) != 1: raise ValueError('Expected one iframe bootstrap in artifact')
        instrumented = source.replace(marker, marker + "\n          window.__holdResponses = true; window.__responses = [];\n          addEventListener('message', e => {\n            if (window.__holdResponses && e.data && e.data.type === '__ZIP_RES_BLOB__') {\n              e.stopImmediatePropagation(); window.__responses.push(e.data);\n            }\n          }, true);\n          window.__releaseResponse = (response) => { const held = window.__holdResponses; window.__holdResponses = false; try { dispatchEvent(new MessageEvent('message', {data: response, source: parent})); } finally { window.__holdResponses = held; } }; \n        ", 1)
        # Shorten only this test's request timeout; production still uses 25s.
        instrumented = instrumented.replace('}, 25000);', '}, window.__testRequestTimeoutMs || 25000);', 1)
        cls.instrumented = Path(cls.tmp.name) / LAUNCHER.name
        cls.instrumented.write_text(instrumented, encoding='utf-8')
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=cls.tmp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(executable_path=os.environ.get('CHROMIUM', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.p.stop(); cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def setUp(self):
        self.page = self.browser.new_page()
        from urllib.parse import quote
        self.page.goto(f'http://127.0.0.1:{self.server.server_port}/' + quote(LAUNCHER.name), wait_until='domcontentloaded')
        self.page.locator('#zipFileInput').set_input_files(str(self.corrupt_zip if self._testMethodName == 'test_corrupt_crc_reports_structured_error' else self.zip))
        self.page.wait_for_selector('#viewer', timeout=30000)
        self.frame = self.page.locator('#viewer').content_frame
        self.frame.locator('#fixture').wait_for(state='attached', timeout=30000)
        self.frame.locator('#fixture').evaluate("""() => new Promise((resolve,reject) => {
          let until=Date.now()+10000,t=setInterval(()=>{
            if(window.__VFS_BLOBS__ && window.__VFS_KNOWN_SET__){clearInterval(t);resolve()}
            else if(Date.now()>until){clearInterval(t);reject(new Error('VFS mount did not complete'))}
          },10)
        })""")

    def tearDown(self): self.page.close()

    def wait_responses(self, count=1):
        self.frame.locator('#fixture').evaluate(f'''() => new Promise((resolve, reject) => {{
          let deadline = Date.now() + 10000; let timer = setInterval(() => {{
            if (window.__responses.length >= {count}) {{ clearInterval(timer); resolve(); }}
            else if (Date.now() > deadline) {{ clearInterval(timer); reject(new Error('No on-demand response')); }}
          }}, 20);
        }})''')

    def test_concurrent_images_and_encoded_path(self):
        self.frame.locator('#fixture').evaluate('''el => {
          for (let i=0; i<100; i++) { const img = new Image(); img.id='i'+i; img.src='assets/pic'+i+'.png'; el.appendChild(img); }
          for (let i=0; i<20; i++) { const img = new Image(); img.className='repeat'; img.src='assets/pic1.png'; el.appendChild(img); }
          const img = new Image(); img.id='encoded'; img.src='assets/%D8%B5%D9%88%D8%B1%D8%A9%20one.png'; el.appendChild(img);
        }''')
        self.wait_responses(101)
        self.frame.locator('#fixture').evaluate('''() => {window.__holdResponses=false; for (const r of window.__responses) window.__releaseResponse(r)}''')
        self.frame.locator('#fixture').evaluate('''() => new Promise((resolve,reject) => {let until=Date.now()+10000,t=setInterval(()=>{if([...document.images].every(i=>i.complete && i.naturalWidth>0)){clearInterval(t);resolve()}else if(Date.now()>until){clearInterval(t);reject(new Error('Image decode failure'))}},20)})''')
        dims = self.frame.locator('#fixture').evaluate('''() => [...document.images].map(i => [i.naturalWidth, i.naturalHeight])''')
        self.assertEqual(dims[:100], [[2+i%5,3+i%7] for i in range(100)])
        self.assertEqual(dims[-1], [11,13])

    def test_stale_response_id_must_not_complete_new_request(self):
        self.frame.locator('#fixture').evaluate("el => {const i=new Image();i.id='target';i.src='assets/pic0.png';el.appendChild(i)}")
        self.wait_responses()
        before = self.frame.locator('#target').evaluate("i => ({width:i.naturalWidth,src:i.src,orig:i.getAttribute('data-vfs-orig'), queue:window.__responses.map(r => [r.path,r.reqId]), cache:[...window.__VFS_BLOBS__.keys()].filter(k=>k.includes('pic0'))})")
        self.assertEqual(before['width'], 0, f'Image loaded before held response was released: {before}')
        self.frame.locator('#fixture').evaluate("() => {const r=window.__responses[0];window.__releaseResponse({...r,reqId:'stale-request-id'})}")
        self.page.wait_for_timeout(250)
        self.assertEqual(self.frame.locator('#target').evaluate('(i) => i.naturalWidth'), 0, 'A response with the wrong request ID was accepted')
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.frame.locator('#target').evaluate("i => new Promise((resolve,reject) => {let until=Date.now()+5000,t=setInterval(()=>{if(i.naturalWidth){clearInterval(t);resolve()}else if(Date.now()>until){clearInterval(t);reject(new Error('valid response did not render'))}},20)})")
        self.assertEqual(self.frame.locator('#target').evaluate('i => i.naturalWidth'), 2)

    def test_lost_response_is_retried_and_image_loads(self):
        self.frame.locator('#fixture').evaluate("el => {window.__testRequestTimeoutMs=300; const i=new Image();i.id='target';i.src='assets/pic0.png';el.appendChild(i)}")
        # Hold the first response to simulate a delayed or lost message. A retry
        # must preserve the original pending image and eventually render it.
        self.wait_responses(2)
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[1])")
        self.frame.locator('#target').evaluate("i => new Promise((resolve,reject) => {let until=Date.now()+5000,t=setInterval(()=>{if(i.naturalWidth){clearInterval(t);resolve()}else if(Date.now()>until){clearInterval(t);reject(new Error('retry did not render image'))}},20)})")
        self.assertEqual(self.frame.locator('#target').evaluate('i => i.naturalWidth'), 2)

    def test_late_original_response_remains_valid_during_retry(self):
        self.frame.locator('#fixture').evaluate("el => {window.__testRequestTimeoutMs=300; const i=new Image();i.id='target';i.src='assets/pic0.png';el.appendChild(i)}")
        self.wait_responses(2)
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.frame.locator('#target').evaluate("i => new Promise((resolve,reject) => {let until=Date.now()+5000,t=setInterval(()=>{if(i.naturalWidth){clearInterval(t);resolve()}else if(Date.now()>until){clearInterval(t);reject(new Error('late response did not render image'))}},20)})")
        self.assertEqual(self.frame.locator('#target').evaluate('i => i.naturalWidth'), 2)

    def test_delayed_source_does_not_override_new_data_url(self):
        new_data = 'data:image/png;base64,' + base64.b64encode(png(9, 8, (220,2,3))).decode()
        self.frame.locator('#fixture').evaluate('''(el, src) => {const i=new Image();i.id='target';i.src='assets/pic0.png';el.appendChild(i);i.src=src}''', new_data)
        self.wait_responses()
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        self.assertEqual(self.frame.locator('#target').evaluate('i => [i.naturalWidth,i.naturalHeight]'), [9,8], 'Old delayed source replaced a newer data URL')

    def test_corrupt_crc_reports_structured_error(self):
        self.frame.locator('#fixture').evaluate("el => {const i=new Image();i.id='target';i.src='assets/pic0.png';el.appendChild(i)}")
        self.wait_responses()
        result = self.frame.locator('#fixture').evaluate("() => {const r=window.__responses[0];return {blob: !!r.blob, error: r.error}}")
        self.assertFalse(result['blob'])
        self.assertEqual((result.get('error') or {}).get('code'), 'CRC32_MISMATCH')
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        self.assertIn('CRC32', self.page.locator('#diagItemsList').inner_text())

    def test_delayed_css_background_property_does_not_override_new_value(self):
        self._check_css_reassignment('property')

    def test_delayed_css_setproperty_does_not_override_new_value(self):
        self._check_css_reassignment('setProperty')

    def _check_css_reassignment(self, method):
        self.frame.locator('#fixture').evaluate("(el, method) => {if(method==='property'){el.style.backgroundImage='url(assets/pic0.png)'}else{el.style.setProperty('background-image','url(assets/pic0.png)')}}", method)
        self.wait_responses()
        self.frame.locator('#fixture').evaluate("(el, method) => {if(method==='property'){el.style.backgroundImage='none'}else{el.style.setProperty('background-image','none')}}", method)
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        self.assertEqual(self.frame.locator('#fixture').evaluate('el => el.style.backgroundImage'), 'none')

    def test_stylesheet_unrelated_property_keeps_pending_image(self):
        self.frame.locator('#fixture').evaluate("""() => {
          const style = document.createElement('style'); document.head.appendChild(style);
          style.sheet.insertRule('#fixture {}'); window.__rule = style.sheet.cssRules[0];
          window.__rule.style.setProperty('background-image', 'url(assets/pic0.png)');
          window.__rule.style.setProperty('color', 'red');
        }""")
        self.wait_responses()
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        value = self.frame.locator('#fixture').evaluate("() => window.__rule.style.backgroundImage")
        self.assertIn('blob:', value, 'Unrelated style change canceled pending stylesheet image')

    def test_stylesheet_remove_property_cancels_pending_image(self):
        self.frame.locator('#fixture').evaluate("""() => {
          const style = document.createElement('style'); document.head.appendChild(style);
          style.sheet.insertRule('#fixture {}'); window.__rule = style.sheet.cssRules[0];
          window.__rule.style.setProperty('background-image', 'url(assets/pic0.png)');
        }""")
        self.wait_responses()
        self.frame.locator('#fixture').evaluate("() => window.__rule.style.removeProperty('background-image')")
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        value = self.frame.locator('#fixture').evaluate("() => window.__rule.style.backgroundImage")
        self.assertEqual(value, '', 'Removed stylesheet property was restored by an old image response')

    def test_dynamic_stylesheet_css_text_loads_image(self):
        self.frame.locator('#fixture').evaluate("""() => {
          const style = document.createElement('style'); document.head.appendChild(style);
          style.sheet.insertRule('#fixture {}'); window.__rule = style.sheet.cssRules[0];
          window.__rule.style.cssText = 'background-image:url(assets/pic0.png)';
        }""")
        self.wait_responses()
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        value = self.frame.locator('#fixture').evaluate("() => window.__rule.style.backgroundImage")
        self.assertIn('blob:', value)
        self.assertTrue(self.frame.locator('#fixture').evaluate("() => getComputedStyle(document.querySelector('#fixture')).backgroundImage.includes('blob:')"))

    def test_mixed_data_and_local_css_image(self):
        data = 'data:image/png;base64,' + base64.b64encode(png(2, 2, (1,2,3))).decode()
        self.frame.locator('#fixture').evaluate("(el,data) => {el.style.setProperty('background-image', `url(${data}), url(assets/pic0.png)`)}", data)
        self.wait_responses()
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        value = self.frame.locator('#fixture').evaluate('el => el.style.backgroundImage')
        self.assertIn('data:image/png', value)
        self.assertIn('blob:', value)

    def test_mixed_blob_and_local_srcset(self):
        self.frame.locator('#fixture').evaluate("""el => {
          const i = new Image(); i.id='target'; el.appendChild(i);
          window.__originalBlob = URL.createObjectURL(new Blob(['x'], {type:'image/png'}));
        }""")
        self.page.wait_for_timeout(100)
        self.frame.locator('#target').evaluate("i => i.srcset = window.__originalBlob + ' 1x, assets/pic0.png 2x'")
        self.wait_responses()
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        value = self.frame.locator('#target').evaluate('i => i.srcset')
        self.assertEqual(value.count('blob:'), 2)
        self.assertNotIn('assets/pic0.png', value)

    def test_dynamic_image_srcset_property(self):
        self.frame.locator('#fixture').evaluate("el => {let i=new Image();i.id='target';el.appendChild(i)}")
        self.page.wait_for_timeout(100)
        self.frame.locator('#target').evaluate("i => i.srcset='assets/pic0.png 1x'")
        self.wait_responses()
        self.frame.locator('#fixture').evaluate("() => window.__releaseResponse(window.__responses[0])")
        self.page.wait_for_timeout(250)
        self.assertEqual(self.frame.locator('#target').evaluate('i => [i.naturalWidth,i.naturalHeight]'), [2,3])

if __name__ == '__main__': unittest.main(verbosity=2)
