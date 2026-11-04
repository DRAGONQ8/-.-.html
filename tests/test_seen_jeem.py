#!/usr/bin/env python3
"""Browser integration against an explicitly supplied Seen Jeem HEAD ZIP.

Usage: python3 tests/test_seen_jeem.py --artifact ZIP_Launcher_V4_Fixed.html --zip /path/to/seen-jeem-head.zip
A local ephemeral HTTP server serves the launcher. Tests mutate only the in-memory
history passed to the game's normal match creation, to make target questions
reproducibly selectable; the Seen Jeem archive and its source are never modified.
"""
import argparse
import functools
import http.server
import json
import socketserver
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact', default='ZIP_Launcher_V4_Fixed.html')
    parser.add_argument('--zip', required=True)
    parser.add_argument('--offline', action='store_true', help='Disable network after launcher document load')
    parser.add_argument('--mobile', action='store_true', help='Chromium mobile emulation (not a physical device)')
    parser.add_argument('--screenshot-dir', default='/tmp/seen-jeem-browser')
    args = parser.parse_args()
    artifact = (ROOT / args.artifact).resolve()
    zip_path = Path(args.zip).resolve()
    assert artifact.is_file() and zip_path.is_file()
    screenshots = Path(args.screenshot_dir)
    screenshots.mkdir(parents=True, exist_ok=True)
    server = socketserver.TCPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(artifact.parent)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_address[1]}/{artifact.name}'
    results=[]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        for target, category, size in [('arab_capitals_q12','arab_capitals',(960,640)),('kw_landmarks_q13','kw_landmarks',(675,900))]:
            context=browser.new_context(**(dict(p.devices['Pixel 7']) if args.mobile else {'viewport':{'width':1280,'height':900}}))
            page=context.new_page()
            page.goto(url)
            if args.offline:
                context.set_offline(True)
            page.locator('#zipFileInput').set_input_files(str(zip_path))
            page.locator('#viewer').wait_for(timeout=120000)
            page.locator('#skipNamingBtn').click(timeout=120000)
            frame=page.frame_locator('#viewer')
            frame.locator('#btn-home-start').wait_for(timeout=120000)
            # Wait for app startup, not the original static HTML shell.
            frame.locator('#hero-total-categories').wait_for(timeout=120000)
            page.wait_for_function("document.querySelector('#statusBar').textContent.includes('قيد التشغيل')",timeout=120000)
            page.frames[-1].wait_for_function("window.SeenJeemEngine && document.querySelector('#hero-total-categories').textContent === '207'", timeout=120000)
            frame.locator('#btn-home-start').click(timeout=30000)
            frame.locator('[data-cat-id="arab_capitals"]').wait_for(timeout=30000)
            for cid in ['arab_capitals','kw_landmarks']:
                img=frame.locator(f'[data-cat-id="{cid}"] img').first
                img.scroll_into_view_if_needed()
                img.evaluate("e => new Promise((resolve,reject) => { if(e.complete && e.naturalWidth) return resolve(); e.addEventListener('load',resolve,{once:true}); e.addEventListener('error',reject,{once:true}); setTimeout(()=>reject(Error('image timeout')),30000) })")
                dims=img.evaluate('e => [e.naturalWidth,e.naturalHeight]')
                assert dims[0]>0 and dims[1]>0, (cid,dims)
                results.append({'type':'category','id':cid,'dimensions':dims})
            # Bias only selection history, so actual engine, renderer, registry, and
            # launcher continue to process the original question and image.
            game=page.frames[-1]
            game.evaluate('''target => {
              const engine=window.SeenJeemEngine, original=engine.createMatch;
              engine.createMatch=function(cfg) {
                const q=cfg.questions.find(q=>q.id===target);
                if (!q) throw Error('target missing: '+target);
                cfg.exposedHistory=Object.assign({},cfg.exposedHistory);
                cfg.questions.filter(x=>x.categoryId===q.categoryId && x.points===q.points && x.id!==q.id).forEach(x=>cfg.exposedHistory[x.id]=true);
                delete cfg.exposedHistory[q.id];
                const match=original(cfg); window.__qaMatch=match; return match;
              };
            }''',target)
            ids=game.locator('.cat-card').evaluate_all("els => els.map(e=>e.dataset.catId)")
            chosen=['arab_capitals','kw_landmarks']+[x for x in ids if x not in ('arab_capitals','kw_landmarks')][:4]
            for cid in chosen:
                frame.locator(f'[data-cat-id="{cid}"]').click()
            frame.locator('#btn-proceed-setup').click()
            frame.locator('#btn-launch-match').click()
            frame.locator('#trivia-board-grid .board-cell-btn').first.wait_for(timeout=30000)
            cell=game.evaluate("id => Object.entries(window.__qaMatch.questionsByCell).find(([k,q])=>q.id===id)?.[0]",target)
            assert cell, f'{target} not selected'
            frame.locator(f'[data-cell-id="{cell}"]').click()
            img=frame.locator('#q-modal-visual-img')
            img.wait_for(state='visible',timeout=30000)
            img.evaluate("e => new Promise((resolve,reject)=> { if(e.complete && e.naturalWidth) return resolve(); e.addEventListener('load',resolve,{once:true}); e.addEventListener('error',reject,{once:true}); setTimeout(()=>reject(Error('image timeout')),30000) })")
            dims=img.evaluate('e => [e.naturalWidth,e.naturalHeight]')
            assert dims==list(size),(target,dims,size)
            expected_hash={'arab_capitals_q12':'fcd66f6274a636590611d1cb398878812c52a959cddce047dbcb26da6ca35e79','kw_landmarks_q13':'4766fd6aeefee61f3167684754f8b6f3de0b8ee77eb0048f111d59ef671a3ade'}[target]
            digest=img.evaluate('''async e => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await (await fetch(e.src)).arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('')''')
            assert digest==expected_hash, (target,digest)
            page.screenshot(path=str(screenshots / f'{target}.png'))
            results.append({'type':'question','id':target,'dimensions':dims,'screenshot':str(screenshots / f'{target}.png')})
            # Close the launcher project, reopen it, and resume the same question.
            page.locator('#closeSiteBtn').click()
            page.locator('#confirmCloseSiteBtn').click()
            page.locator('#postCloseReopenBtn').click(timeout=30000)
            page.locator('#viewer').wait_for(timeout=120000)
            frame=page.frame_locator('#viewer')
            frame.locator('#btn-resume-match').click(timeout=120000)
            try:
                frame.locator('#btn-banner-resume-question').click(timeout=10000)
            except Exception:
                game=page.frames[-1]
                print('REOPEN DEBUG', target, game.locator('#screen-board').inner_text()[:2500], flush=True)
                page.screenshot(path=str(screenshots / f'{target}-resume-failure.png'))
                raise
            img=frame.locator('#q-modal-visual-img')
            img.wait_for(state='visible',timeout=30000)
            img.evaluate("e => new Promise((resolve,reject)=> { if(e.complete && e.naturalWidth) return resolve(); e.addEventListener('load',resolve,{once:true}); e.addEventListener('error',reject,{once:true}); setTimeout(()=>reject(Error('image timeout')),30000) })")
            dims=img.evaluate('e => [e.naturalWidth,e.naturalHeight]')
            assert dims==list(size),(target,'after reopen',dims)
            digest=img.evaluate('''async e => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await (await fetch(e.src)).arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('')''')
            assert digest==expected_hash,(target,'after reopen',digest)
            results.append({'type':'question_after_reopen','id':target,'dimensions':dims})
            context.close()
        browser.close()
    server.shutdown()
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
