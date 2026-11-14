const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../libexec/mdview/viewer.js'), 'utf8');
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

function page(fetch) {
  let now = 0;
  let nextTimer = 0;
  const timers = new Map();
  const listeners = new Map();
  const documentListeners = new Map();
  const scrolls = [];
  const article = { children: [], innerHTML: '', querySelectorAll: () => [] };
  const elements = { document: article, status: {}, error: { hidden: true }, filename: {}, filepath: {} };
  const selection = { isCollapsed: false, marker: 'human selection' };
  const document = {
    hidden: false,
    activeElement: { marker: 'human focus' },
    getElementById: id => elements[id],
    addEventListener: (name, callback) => documentListeners.set(name, callback),
  };
  const context = {
    URL, AbortController, Promise, Map, Error, TypeError,
    location: { href: 'http://127.0.0.1:8765/private/', hash: '' },
    document, scrollX: 20, scrollY: 430,
    marked: { setOptions() {}, parse: text => text },
    DOMPurify: { sanitize: text => text },
    mermaid: { initialize() {} },
    fetch: (...args) => fetch(...args),
    setTimeout(callback, delay) {
      const id = ++nextTimer;
      timers.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    matchMedia: () => ({ matches: false }),
    scrollTo: (...args) => scrolls.push(args),
    getSelection: () => selection,
    addEventListener: (name, callback) => listeners.set(name, callback),
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'viewer.js' });
  async function settle() {
    for (let count = 0; count < 30; count++) await Promise.resolve();
  }
  async function advance(ms) {
    const end = now + ms;
    while (true) {
      const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at;
      timers.delete(next[0]);
      next[1].callback();
      await settle();
    }
    now = end;
    await settle();
  }
  return { context, document, article, elements, scrolls, selection, listeners, documentListeners, settle, advance };
}

function response(markdown, etag = markdown) {
  return { status: 200, ok: true, headers: new Map([['ETag', etag]]),
    json: async () => ({ name: 'notes.md', path: '/private/notes.md', markdown }) };
}

test('a hung content request cannot freeze newer source forever', async () => {
  const held = deferred();
  const requests = [];
  let current = 'Original document';
  const p = page((_url, options) => {
    requests.push(options);
    return requests.length === 2 ? held.promise : Promise.resolve(response(current));
  });
  await p.settle();
  assert.equal(p.article.innerHTML, current);
  await p.advance(750);
  assert.equal(requests.length, 2);
  current = 'Latest document from the unchanged server';
  const focus = p.document.activeElement;
  const scrollCount = p.scrolls.length;
  await p.advance(5000);
  assert.equal(p.article.innerHTML, 'Original document', 'timeout retains working content');
  assert.equal(p.document.activeElement, focus);
  assert.equal(p.context.getSelection(), p.selection);
  assert.equal(p.scrolls.length, scrollCount, 'failure does not restore or move scroll');
  assert.equal(p.elements.status.textContent, 'Reconnecting…');
  assert.equal(requests[1].signal.aborted, true);
  await p.advance(750);
  assert.equal(p.article.innerHTML, current);
  assert.equal(p.elements.error.hidden, true);
  held.resolve(response('Late stale response'));
  await p.settle();
  assert.equal(p.article.innerHTML, current, 'a timed-out response cannot overwrite a later version');
});

test('the deadline includes a body that stalls after headers arrive', async () => {
  const held = deferred();
  const requests = [];
  let current = 'Original';
  const p = page((_url, options) => {
    requests.push(options);
    if (requests.length === 2) return Promise.resolve({
      status: 200, ok: true, headers: new Map([['ETag', 'stalled']]), json: () => held.promise,
    });
    return Promise.resolve(response(current));
  });
  await p.settle();
  await p.advance(750);
  current = 'Newer complete response';
  await p.advance(5000);
  assert.equal(p.article.innerHTML, 'Original');
  assert.equal(p.elements.status.textContent, 'Reconnecting…');
  await p.advance(750);
  assert.equal(requests[2].headers['If-None-Match'], 'Original', 'a stalled version never advances the ETag');
  assert.equal(p.article.innerHTML, current);
  held.resolve({ name: 'stale.md', path: '/stale.md', markdown: 'Late body' });
  await p.settle();
  assert.equal(p.article.innerHTML, current);
  assert.equal(p.elements.filename.textContent, 'notes.md');
});

test('a hung local image check cannot prevent subsequent document refresh', async () => {
  const held = deferred();
  let imageRequest;
  let current = null;
  const p = page((url, options) => {
    if (String(url).endsWith('/content')) return Promise.resolve(current ? response(current) : { status: 304 });
    imageRequest = options;
    return held.promise;
  });
  await p.settle();
  const image = { src: '', getAttribute: () => 'diagram.svg' };
  let images = [image];
  p.article.querySelectorAll = selector => selector === 'img' ? images : [];
  await p.context.render('An existing local diagram');
  const imageSource = image.src;
  p.context.refresh();
  await p.settle();
  assert.ok(imageRequest);
  current = 'Edited Markdown without that image';
  images = [];
  await p.advance(5000);
  assert.equal(imageRequest.signal.aborted, true);
  await p.advance(750);
  assert.equal(p.article.innerHTML, current);
  held.resolve({ status: 200, ok: true, headers: new Map([['ETag', 'late-image']]) });
  await p.settle();
  assert.equal(image.src, imageSource, 'a timed-out image check cannot mutate an obsolete image');
});

test('visibility, focus and bfcache restoration refresh without waiting for a throttled poll', async () => {
  let current = 'Original';
  let reads = 0;
  const p = page(() => { reads++; return Promise.resolve(response(current)); });
  await p.settle();
  p.document.hidden = true;
  await p.context.refresh();
  const before = reads;
  current = 'Edited while hidden';
  p.documentListeners.get('visibilitychange')();
  await p.settle();
  assert.equal(reads, before, 'hiding alone does not request another read');
  p.document.hidden = false;
  p.documentListeners.get('visibilitychange')();
  await p.settle();
  assert.equal(reads, before + 1);
  assert.equal(p.article.innerHTML, current);
  current = 'Edited before focus';
  p.listeners.get('focus')();
  await p.settle();
  assert.equal(p.article.innerHTML, current);
  current = 'Edited in back-forward cache';
  p.listeners.get('pageshow')({ persisted: false });
  await p.settle();
  assert.notEqual(p.article.innerHTML, current);
  p.listeners.get('pageshow')({ persisted: true });
  await p.settle();
  assert.equal(p.article.innerHTML, current);
});

test('foreground requests coalesce behind one in-flight read instead of racing renders', async () => {
  const held = deferred();
  let reads = 0;
  const p = page(() => ++reads === 1 ? held.promise : Promise.resolve(response('Newest')));
  p.listeners.get('focus')();
  p.listeners.get('focus')();
  p.documentListeners.get('visibilitychange')();
  p.listeners.get('pageshow')({ persisted: true });
  await p.settle();
  assert.equal(reads, 1);
  held.resolve(response('First response'));
  await p.settle();
  await p.advance(0);
  assert.equal(reads, 2, 'one fresh read follows all coalesced requests');
  assert.equal(p.article.innerHTML, 'Newest');
  await p.advance(749);
  assert.equal(reads, 2, 'old poll timers were cancelled');
  await p.advance(1);
  assert.equal(reads, 3, 'ordinary periodic refresh continues');
});

test('a transient server failure preserves the document, selection, focus, scroll and conditional read', async () => {
  const requests = [];
  let fail = false;
  const p = page((_url, options) => {
    requests.push(options);
    return Promise.resolve(fail ? { status: 503, ok: false, text: async () => 'Source temporarily unavailable' } : response('Working document'));
  });
  await p.settle();
  const focus = p.document.activeElement;
  const scrollCount = p.scrolls.length;
  fail = true;
  await p.advance(750);
  assert.equal(p.article.innerHTML, 'Working document');
  assert.equal(p.document.activeElement, focus);
  assert.equal(p.context.getSelection(), p.selection);
  assert.equal(p.scrolls.length, scrollCount);
  assert.equal(p.elements.error.textContent, 'Source temporarily unavailable');
  fail = false;
  await p.advance(750);
  assert.equal(requests.at(-1).headers['If-None-Match'], 'Working document');
  assert.equal(p.elements.status.textContent, 'Live');
  assert.equal(p.elements.error.hidden, true);
});
