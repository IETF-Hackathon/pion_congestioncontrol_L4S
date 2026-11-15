/** Check the real project page in an isolated browser at desktop/phone widths. */
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../../..');
const profile = await mkdtemp(join(tmpdir(), 'pion-preview-qa-'));
const browser = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless', '--disable-gpu', '--disable-extensions', '--disable-background-networking',
  '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
  '--remote-debugging-address=127.0.0.1', '--user-data-dir='+profile, 'about:blank'
], {stdio: ['ignore', 'ignore', 'pipe']});
let websocket;
const pending = new Map();
let nextId = 0;

/** Send one Chrome DevTools request and enforce a bounded response time. */
function rpc(method, params = {}, sessionId) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error('Timed out: '+method)); }, 10000);
    pending.set(id, {resolve, reject, timeout});
    websocket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
  });
}

try {
  const endpoint = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Browser startup timeout')), 15000);
    let output = '';
    browser.stderr.on('data', chunk => {
      output += chunk.toString();
      const match = output.match(/DevTools listening on (ws:\S+)/);
      if (match) { clearTimeout(timeout); resolve(match[1]); }
    });
    browser.once('error', reject);
  });
  websocket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => { websocket.onopen = resolve; websocket.onerror = reject; });
  websocket.onmessage = event => {
    const response = JSON.parse(event.data);
    const waiter = pending.get(response.id);
    if (!waiter) return;
    pending.delete(response.id);
    clearTimeout(waiter.timeout);
    if (response.error) waiter.reject(new Error(JSON.stringify(response.error)));
    else waiter.resolve(response.result);
  };
  const {targetId} = await rpc('Target.createTarget', {url: 'about:blank'});
  const {sessionId} = await rpc('Target.attachToTarget', {targetId, flatten: true});
  await rpc('Page.enable', {}, sessionId);
  await rpc('Page.navigate', {url: pathToFileURL(join(root, 'index.html')).href}, sessionId);
  for (let attempt=0; attempt<100; attempt++) {
    const response = await rpc('Runtime.evaluate', {expression: 'document.readyState', returnByValue: true}, sessionId);
    if (response.result.value === 'complete') break;
    if (attempt === 99) throw new Error('Page did not finish loading');
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  for (const [label, width, selector] of [
    ['desktop-preview', 1440, '#preview'],
    ['desktop-testbed', 1440, '#preview details:last-of-type'],
    ['phone-preview', 390, '#preview'],
    ['desktop-sender-roles', 1440, '#controller-vs-pacer'],
    ['phone-sender-roles', 390, '#controller-vs-pacer'],
    ['desktop-scheduler-design', 1440, '#pacing-scheduler-proposal'],
    ['phone-scheduler-design', 390, '#pacing-scheduler-proposal'],
    ['desktop-pacing-results', 1440, '#pacing-results'],
    ['phone-pacing-results', 390, '#pacing-results'],
    ['desktop-throughput-rca', 1440, '#pacing-throughput-rca'],
    ['phone-throughput-rca', 390, '#pacing-throughput-rca'],
    ['phone-throughput-rca-model', 390, '#pacing-throughput-rca > details:first-of-type'],
    ['desktop-throughput-rca-evidence', 1440, '#pacing-throughput-rca > details:last-of-type'],
    ['phone-throughput-rca-evidence', 390, '#pacing-throughput-rca > details:last-of-type'],
    ['desktop-ecn-loop', 1440, '#prague-feedback .ecn-loop-figure'],
    ['desktop-physical-setup', 1440, '#test-network .network-figure'],
    ['phone-ecn-loop', 390, '#prague-feedback .ecn-loop-figure'],
    ['phone-physical-setup', 390, '#test-network .network-figure']
  ]) {
    await rpc('Emulation.setDeviceMetricsOverride', {width, height: 1800, deviceScaleFactor: 1, mobile: false}, sessionId);
    const response = await rpc('Runtime.evaluate', {expression: `
      document.documentElement.style.scrollBehavior='auto';
      var target=document.querySelector(${JSON.stringify(selector)});
      if (target.tagName==='DETAILS') target.open=true;
      target.scrollIntoView({behavior:'instant',block:'start'});
      JSON.stringify({viewport:innerWidth, bodyWidth:document.body.scrollWidth,
        heading:document.querySelector('#preview-title').textContent,
        rows:document.querySelectorAll('#preview tbody tr[data-run-id]').length,
        labResultsCells:document.querySelectorAll('#results td').length,
        labResultsEmpty:[...document.querySelectorAll('#results td')].every(cell=>!cell.textContent.trim())})
    `, returnByValue: true}, sessionId);
    const metrics = JSON.parse(response.result.value);
    if (metrics.bodyWidth > width) {
      // Identify visible content that escapes the viewport rather than a scroll container.
      const overflow = await rpc('Runtime.evaluate', {expression: `JSON.stringify([...document.body.querySelectorAll('*')].filter(element => {
        const rect=element.getBoundingClientRect();
        if (rect.right <= innerWidth+1 && element.scrollWidth <= element.clientWidth+1) return false;
        for (let parent=element;parent && parent!==document.body;parent=parent.parentElement) {
          if (getComputedStyle(parent).overflowX!=='visible' && parent.getBoundingClientRect().right<=innerWidth+1) return false;
        }
        return true;
      }).map(element=>({tag:element.tagName,id:element.id,class:element.className?.baseVal??element.className,right:element.getBoundingClientRect().right,clientWidth:element.clientWidth,scrollWidth:element.scrollWidth,text:element.textContent.slice(0,100)})).slice(0,20))`, returnByValue: true}, sessionId);
      console.error(overflow.result.value);
    }
    if (metrics.bodyWidth > width || metrics.rows !== 2 || !metrics.labResultsEmpty) throw new Error(JSON.stringify(metrics));
    // Check the actual diagram asset and its existing overlay/zoom controls.
    if (label === 'desktop-physical-setup') {
      const diagram = await rpc('Runtime.evaluate', {expression: `(async () => {
        const source=document.querySelector('#network-stage .experiment-layer').getAttribute('href');
        const image=new Image(); image.src=source; await image.decode();
        const stage=document.querySelector('#network-stage');
        const overlay=document.querySelector('#network-overlay-toggle');
        overlay.click(); const hidden=stage.classList.contains('overlay-hidden'); overlay.click();
        const zoom=document.querySelector('#network-zoom');
        zoom.click(); const enlarged=stage.classList.contains('zoomed'); zoom.click();
        return {imageWidth:image.naturalWidth,imageHeight:image.naturalHeight,overlayToggle:hidden,zoomToggle:enlarged};
      })()`, awaitPromise: true, returnByValue: true}, sessionId);
      if (diagram.exceptionDetails) throw new Error(JSON.stringify(diagram.exceptionDetails));
      if (!diagram.result.value.overlayToggle || !diagram.result.value.zoomToggle || !diagram.result.value.imageWidth) throw new Error(JSON.stringify(diagram.result.value));
      console.log(JSON.stringify(diagram.result.value));
    }
    const screenshot = await rpc('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false}, sessionId);
    await writeFile(join(here, label+'.png'), Buffer.from(screenshot.data, 'base64'));
    console.log(JSON.stringify({label, ...metrics}));
  }
} finally {
  if (websocket) websocket.close();
  browser.kill('SIGTERM');
}
