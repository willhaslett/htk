// Development-only DOM regression check; see libexec/mdview/README.md.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../libexec/mdview') + path.sep;
const dom = new JSDOM(fs.readFileSync(root + 'index.html', 'utf8'), {
  url: 'http://127.0.0.1:8765/token/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
w.matchMedia = () => ({ matches: false });
let scrollCalls = [];
w.scrollTo = (...args) => { scrollCalls.push(args); };
// jsdom has no layout engine; approximate SVG text metrics for Mermaid's layout.
w.SVGElement.prototype.getBBox = function () {
  return { x: 0, y: 0, width: (this.textContent || '').length * 8 || 10, height: 16 };
};
w.SVGElement.prototype.getComputedTextLength = function () { return this.getBBox().width; };
w.fetch = async () => ({ status: 304 });
for (const name of ['vendor/marked.js', 'vendor/purify.js', 'vendor/mermaid.js', 'viewer.js']) {
  vm.runInContext(fs.readFileSync(root + name, 'utf8'), dom.getInternalVMContext(), { filename: name });
}
const article = w.document.getElementById('document');
const md = label => `# Notes\n\n\`\`\`mermaid\nflowchart LR\nA[Start] --> B[${label}]\n\`\`\`\n\n| One | Two |\n| --- | --- |\n| A | B |\n\nOrdinary text.\n\n\`\`\`python\nprint('ok')\n\`\`\`\n`;
(async () => {
  // Run the actual page lifetime script with browser primitives, without
  // opening another copy of a user's document or requiring automation access.
  const pageEvents = new Map();
  const streams = [];
  const beacons = [];
  let focusCount = 0;
  let refreshCount = 0;
  let nextPage = 0;
  const lifetime = {
    URL, location: { href: 'http://127.0.0.1:8765/token/' },
    crypto: { randomUUID: () => `page-${++nextPage}` },
    EventSource: class {
      constructor(url) { this.url = String(url); this.listeners = new Map(); streams.push(this); }
      addEventListener(name, handler) { this.listeners.set(name, handler); }
      close() { this.closed = true; }
    },
    navigator: { sendBeacon: url => { beacons.push(String(url)); return true; } },
    window: { addEventListener: (name, handler) => pageEvents.set(name, handler), focus: () => focusCount++, refresh: () => refreshCount++ },
  };
  vm.createContext(lifetime);
  vm.runInContext(fs.readFileSync(root + 'lifetime.js', 'utf8'), lifetime);
  assert.equal(streams[0].url, 'http://127.0.0.1:8765/token/events?page=page-1');
  streams[0].listeners.get('focus')();
  assert.equal(focusCount, 1);
  const nativeMessages = [];
  lifetime.window.webkit = { messageHandlers: { htkPreview: { postMessage: message => nativeMessages.push(message) } } };
  streams[0].listeners.get('focus')();
  assert.deepEqual(nativeMessages, ['focus']);
  assert.equal(focusCount, 1, 'native focus uses its host instead of browser window.focus');
  assert.equal(refreshCount, 2, 'both browser and native reuse request a refresh even without a focus event');
  pageEvents.get('pageshow')({ persisted: false });
  assert.equal(streams.length, 1, 'ordinary pageshow does not duplicate the connection');
  pageEvents.get('pagehide')();
  assert.equal(streams[0].closed, true);
  assert.equal(beacons[0], 'http://127.0.0.1:8765/token/leave?page=page-1');
  pageEvents.get('pageshow')({ persisted: true });
  assert.equal(streams[1].url, 'http://127.0.0.1:8765/token/events?page=page-2');

  // Exercise the actual macOS opener without controlling a real browser.
  for (const focus of [false, true]) {
    const task = { terminationStatus: 0 };
    const context = {
      ObjC: { import() {}, unwrap: value => value },
      $: {
        NSWorkspace: { sharedWorkspace: { frontmostApplication: null,
          URLForApplicationToOpenURL: () => ({ path: '/Applications/Google Chrome.app' }) } },
        NSURL: { URLWithString: value => value },
        NSBundle: { bundleWithURL: () => ({ bundleIdentifier: 'com.google.Chrome' }) },
        NSTask: { alloc: { init: task } },
      },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(root + 'open-background.js', 'utf8'), context);
    context.run(['http://127.0.0.1/document/', ...(focus ? ['--focus'] : [])]);
    assert.deepEqual(Array.from(task.arguments), [...(focus ? [] : ['-g']), '-n', '-a',
      '/Applications/Google Chrome.app', '--args', '--new-window', 'http://127.0.0.1/document/']);
    let activated = false;
    context.$.NSRunningApplication = { runningApplicationsWithBundleIdentifier: () => ({ count: 1,
      objectAtIndex: () => ({ activateWithOptions: () => { activated = true; } }) }) };
    delete task.arguments;
    context.run(['http://127.0.0.1/document/', '--reuse', ...(focus ? ['--focus'] : [])]);
    assert.equal(task.arguments, undefined, 'reuse never opens another browser URL');
    assert.equal(activated, focus, 'reuse only activates the existing app when requested');
  }
  await w.render(md('Finish'));
  assert.equal(article.querySelectorAll('.mermaid-diagram svg').length, 1);
  assert.match(article.querySelector('svg').textContent, /Finish/);
  assert.ok(article.querySelector('svg style'), 'SVG stylesheet survives sanitization');
  assert.equal(article.querySelectorAll('code.language-python').length, 1);
  assert.equal(article.querySelectorAll('.table-scroll table').length, 1);
  assert.ok(article.textContent.includes('Ordinary text.'));
  const diagramKey = article.querySelector('.mermaid-diagram').dataset.key;
  article.querySelector('.table-scroll').scrollLeft = 124;
  article.querySelector('.mermaid-diagram').scrollLeft = 50;
  w.scrollX = 0; w.scrollY = 400;
  await w.render(md('Finish') + '\nNew paragraph.');
  assert.equal(article.querySelector('.mermaid-diagram').dataset.key, diagramKey);
  assert.equal(article.querySelector('.table-scroll').scrollLeft, 124);
  assert.equal(article.querySelector('.mermaid-diagram').scrollLeft, 50);
  assert.equal(scrollCalls.at(-1)[1], 400);
  await w.render(md('Updated'));
  assert.match(article.querySelector('svg').textContent, /Updated/);
  assert.ok(!article.querySelector('svg').textContent.includes('Finish'));
  await w.render('Before.\n\n```mermaid\nflowchart LR\nA[broken\n```\n\nAfter.\n\n```mermaid\nsequenceDiagram\nAlice->>Bob: Hello\n```');
  assert.ok(article.querySelector('.mermaid-error'));
  assert.match(article.querySelector('.mermaid-error + pre').textContent, /A\[broken/);
  assert.ok(article.textContent.includes('Before.') && article.textContent.includes('After.'));
  assert.equal(article.querySelectorAll('svg').length, 1, 'valid neighbor survives');
  assert.equal(w.document.querySelectorAll('body > div[id^="dmdview-mermaid"]').length, 0);
  await w.render(md('Recovered'));
  assert.equal(article.querySelector('.mermaid-error'), null);
  assert.match(article.querySelector('svg').textContent, /Recovered/);
  await w.render('<style>body { display: none }</style><p style="display:none">Visible</p><script>alert(1)</script>\n\n```mermaid\nflowchart LR\nA["<img src=x onerror=alert(1)>"]\n```');
  assert.equal(article.querySelector('script, [onerror], [onclick], foreignObject'), null);
  assert.equal(article.querySelector('p[style], article > style'), null);
  assert.equal(w.mermaid.mermaidAPI.getConfig().securityLevel, 'strict');
  let request;
  w.fetch = async (url, options) => {
    request = { url, options };
    return { status: 200, ok: true, json: async () => ({name: 'edited.md', path: '/tmp/edited.md', markdown: md('Watched edit')}), headers: new Map([['ETag', 'new-version']]) };
  };
  await w.refresh();
  assert.match(article.querySelector('svg').textContent, /Watched edit/);
  assert.equal(w.document.getElementById('status').textContent, 'Live');
  assert.equal(w.document.getElementById('error').hidden, true);
  const existing = article.querySelector('svg');
  w.fetch = async (_url, options) => { request = options; return { status: 304 }; };
  await w.refresh();
  assert.equal(request.headers['If-None-Match'], 'new-version');
  assert.equal(article.querySelector('svg'), existing, '304 does not redraw');
  await w.render('![Local diagram](diagram.svg)\n\n![Remote](https://example.com/image.png)');
  const image = article.querySelector('img');
  let version = 'image-v1';
  const imageRequests = [];
  w.fetch = async (url, options) => {
    if (String(url).endsWith('/content')) return { status: 304 };
    assert.equal(String(url), 'http://127.0.0.1:8765/token/local/diagram.svg');
    imageRequests.push(options.headers['If-None-Match']);
    return options.headers['If-None-Match'] === version ? { status: 304 } :
      { status: 200, ok: true, headers: new Map([['ETag', version]]) };
  };
  await w.refresh();
  const firstSource = image.src;
  assert.equal(new URL(firstSource).searchParams.get('_mdview'), 'image-v1');
  await w.refresh();
  assert.equal(image.src, firstSource, 'unchanged image is not reloaded');
  version = 'image-v2';
  await w.refresh();
  assert.equal(article.querySelector('img'), image, 'image updates without redrawing the document');
  assert.equal(new URL(image.src).searchParams.get('_mdview'), 'image-v2');
  assert.equal(imageRequests.at(-1), 'image-v1');
  await w.render('No images now.');
  const previousRequests = imageRequests.length;
  await w.refresh();
  assert.equal(imageRequests.length, previousRequests, 'removed images stop being watched');
  console.log('PASS: document lifetime, reuse/focus, Mermaid, Markdown/image watching, scroll, recovery and sanitization verified.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => w.close());
