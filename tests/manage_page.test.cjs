// Run with: node --test tests/manage_page.test.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync(`${__dirname}/../pages/manage/app.js`, 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

class Element {
  constructor(tag) { this.tag = tag; this.textContent = ''; this.children = []; }
  setAttribute(name,value) { this[name]=value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
function page(readyState = 'loading') {
  const nodes = Object.fromEntries(['jobs', 'notice', 'refresh', 'guide-panel', 'guide-tabs', 'guide-content', 'guide-notice', 'guide-retry'].map(id => [id, new Element(id)]));
  const events = {};
  const window = {};
  const document = {
    readyState,
    getElementById: id => nodes[id],
    createElement: tag => new Element(tag),
    addEventListener: (name, callback) => { events[name] = callback; },
  };
  return {nodes, events, window, document, run: () => runInNewContext(source, {window, document})};
}
function sdk(jobs = []) {
  const calls = [];
  return {
    calls,
    ready: async () => {},
    apiGet: async endpoint => { calls.push(['get', endpoint]); return {jobs}; },
    apiPost: async (endpoint, body) => { calls.push(['post', endpoint, body.job_id]); },
  };
}

test('AstrBot injects SDK after app.js: wait for parsing and SDK readiness', async () => {
  const p = page();
  p.run(); // No bridge yet: same order as AstrBot-rendered HTML.
  const bridge = sdk();
  let ready;
  bridge.ready = () => new Promise(resolve => { ready = resolve; });
  p.window.AstrBotPluginPage = bridge;
  p.events.DOMContentLoaded();
  await settle();
  assert.equal(bridge.calls.length, 0);
  ready();
  await settle();
  assert.deepEqual(bridge.calls, [['get', 'jobs']]);
  assert.match(p.nodes.jobs.children[0].textContent, /暂无任务/);
  assert.equal(p.nodes.notice.textContent, '');
});

test('already parsed document renders records and refresh/stop uses the same SDK', async () => {
  const p = page('complete');
  const bridge = sdk([{id:'test-job',state:'running',created:100,calls:2,timeout:120,
    output:'synthetic output',files:[{name:'result.csv',bytes:20}],operations:['test']}]);
  p.window.AstrBotPluginPage = bridge;
  p.run();
  await settle();
  const card = p.nodes.jobs.children[0];
  assert.equal(card.children[0].textContent, '运行中');
  assert.ok(card.children.some(n => n.textContent === 'result.csv · 20 字节'));
  await card.children.find(n => n.tag === 'button').onclick();
  await p.nodes.refresh.onclick();
  assert.deepEqual(bridge.calls, [['get','jobs'],['post','stop','test-job'],['get','jobs'],['get','jobs']]);
});

test('request failure recovers using refresh', async () => {
  const p = page('interactive');
  const bridge = sdk();
  const get = bridge.apiGet;
  bridge.apiGet = async () => { throw new Error('synthetic network failure'); };
  p.window.AstrBotPluginPage = bridge;
  p.run();
  await settle();
  assert.match(p.nodes.notice.textContent, /读取失败/);
  bridge.apiGet = get;
  await p.nodes.refresh.onclick();
  assert.equal(p.nodes.notice.textContent, '');
  assert.match(p.nodes.jobs.children[0].textContent, /暂无任务/);
});

test('missing SDK reports initialization failure instead of unhandled rejection', async () => {
  const p = page();
  p.run();
  p.events.DOMContentLoaded();
  await settle();
  assert.match(p.nodes.notice.textContent, /页面初始化失败/);
});


test('guide loads on demand, switches sections safely and retries independently', async () => {
  const p=page('complete');const bridge=sdk();
  const original=bridge.apiGet;let fail=true;
  bridge.apiGet=async endpoint=>{
    if(endpoint!=='guide')return original(endpoint);
    if(fail)throw new Error('unavailable');
    return {sections:[{section:'overview',content:'<script>plain text</script>'},{section:'batch',content:'batch example'}]};
  };
  p.window.AstrBotPluginPage=bridge;p.run();await settle();
  assert.deepEqual(bridge.calls,[['get','jobs']]);
  p.nodes['guide-panel'].open=true;p.nodes['guide-panel'].ontoggle();await settle();
  assert.match(p.nodes['guide-notice'].textContent,/加载失败/);
  fail=false;await p.nodes['guide-retry'].onclick();
  assert.equal(p.nodes['guide-content'].textContent,'<script>plain text</script>');
  const tabs=p.nodes['guide-tabs'].children;
  assert.equal(tabs[0]['aria-pressed'],'true');tabs[1].onclick();
  assert.equal(p.nodes['guide-content'].textContent,'batch example');
  assert.equal(tabs[0]['aria-pressed'],'false');
  assert.equal(tabs[1]['aria-pressed'],'true');
  assert.equal(p.nodes['guide-notice'].textContent,'');
});
