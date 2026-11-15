/** Check the real project page in an isolated browser at desktop/phone widths. */
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, writeFile, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const [oldComparison, pacingComparison, repeat, equalFrames] = await Promise.all([
  'pion-prague-vs-gcc-25m-20261115T172333Z',
  'pion-prague-pacing-v2-25m-20261115T190910Z',
  'new-pion-v2-repeat-25m-20261115T193712Z',
  'pion-equalframes-25m-20261115T212125Z'
].map(async name => JSON.parse(await readFile(join(root, 'results/preview', name+'.json'), 'utf8'))));

/** Format paired-capture metrics consistently with the visible results table. */
function resultValues(run) {
  return {
    'steady-mbps': run.steady_delivered_vp8_frame_mbps.toFixed(3),
    'p50-ms': run.transit_p50_ms.toFixed(3),
    'p99-ms': run.transit_p99_ms.toFixed(3),
    'missing-percent': run.missing_media_percent.toFixed(3)
  };
}

/** Select cohort means without pooling the separately captured repeat. */
function cohortValues(statistics) {
  return resultValues(Object.fromEntries(Object.entries(statistics).map(([key, value]) => [key, value.mean])));
}

const oldPrague = oldComparison.runs.find(run => run.controller==='prague');
const expectedResults = {
  'old-prague': {...resultValues(oldPrague),
    'capture-ce': oldPrague.new_ce_between_vantages.toLocaleString('en-US'),
    'sender-ce': oldPrague.sender_processed_ce_last_feedback.toLocaleString('en-US')},
  'gcc-baseline': cohortValues(pacingComparison.statistics.saved_gcc),
  'new-prague': {...cohortValues(pacingComparison.statistics.new_prague),
    ...Object.fromEntries(pacingComparison.runs.map((run, index) => ['ce-trial-'+(index+1),
      run.new_ce_between_vantages.toLocaleString('en-US')+' / '+run.sender_processed_ce_last_feedback.toLocaleString('en-US')]))},
  'new-prague-repeat': {...resultValues(repeat),
    'capture-ce': repeat.new_ce_between_vantages.toLocaleString('en-US'),
    'sender-ce': repeat.sender_processed_ce_last_feedback.toLocaleString('en-US')},
  ...Object.fromEntries(equalFrames.runs.map(run => ['equal-frame-'+run.repetition, {
    ...resultValues(run),
    'capture-ce': run.new_ce_between_vantages.toLocaleString('en-US'),
    'sender-ce': run.sender_processed_ce_last_feedback.toLocaleString('en-US')
  }]))
};
const output = join(root, 'results/raw/preview-qa');
await mkdir(output, {recursive:true});
const profile = await mkdtemp(join(tmpdir(), 'pion-preview-qa-'));
const browser = spawn(process.env.PION_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
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
    ['desktop-experimental-results', 1440, '#results'],
    ['phone-experimental-results', 390, '#results'],
    ['desktop-results-evidence', 1440, '#results-method-note'],
    ['phone-results-evidence', 390, '#results-method-note'],
    ['desktop-equal-frame-results', 1440, '#equal-frame-results'],
    ['phone-equal-frame-results', 390, '#equal-frame-results'],
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
    ['desktop-sender-tools', 1440, '#repository-sender-tools'],
    ['phone-sender-tools', 390, '#repository-sender-tools'],
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
        resultRows:document.querySelectorAll('#results [data-result-id]').length,
        pocMetrics:document.querySelectorAll('#results [data-poc-metric]').length,
        resultMetrics:Object.fromEntries([...document.querySelectorAll('#results [data-result-id]')].map(row => [row.dataset.resultId,
          Object.fromEntries([...row.querySelectorAll('[data-result-metric]')].map(cell => [cell.dataset.resultMetric,cell.textContent.trim()]))]))})
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
    if (metrics.bodyWidth > width || metrics.rows !== 2 || metrics.resultRows !== Object.keys(expectedResults).length || metrics.pocMetrics !== 6) throw new Error(JSON.stringify(metrics));
    for (const [row, values] of Object.entries(expectedResults)) {
      for (const [metric, expected] of Object.entries(values)) {
        if (metrics.resultMetrics[row]?.[metric] !== expected) throw new Error(`Results mismatch: ${row}/${metric}: expected ${expected}`);
      }
    }
    delete metrics.resultMetrics;
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
    await writeFile(join(output, label+'.png'), Buffer.from(screenshot.data, 'base64'));
    console.log(JSON.stringify({label, ...metrics}));
  }
} finally {
  if (websocket) websocket.close();
  browser.kill('SIGTERM');
}
