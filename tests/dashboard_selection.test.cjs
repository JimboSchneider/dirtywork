const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const assets = path.join(__dirname, '../dirtywork/dashboard_assets');
const source = fs.readFileSync(path.join(assets, 'app.js'), 'utf8');
const presentation = require(path.join(assets, 'presentation.js'));

class Element {
  constructor(tag, className = '', content = '') {
    this.tagName = tag.toUpperCase(); this.className = className;
    this.children = []; this.parentNode = null; this.attributes = {};
    this.dataset = {}; this.listeners = {}; this.hidden = false;
    this._text = content ?? ''; this.value = ''; this.mutations = 0;
    this.classList = {toggle: (name, active) => {
      const classes = new Set(this.className.split(/\s+/).filter(Boolean));
      if (active) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' ');
    }};
  }
  get textContent() { return String(this._text) + this.children.map(node => node.textContent).join(''); }
  set textContent(value) {
    this._text = value ?? '';
    for (const child of this.children) child.parentNode = null;
    this.children = []; this.mutations++;
  }
  get firstChild() { return this.children[0] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    return this.parentNode.children[this.parentNode.children.indexOf(this) + 1] || null;
  }
  get options() { return this.children; }
  append(...nodes) {
    for (const node of nodes) {
      node.remove(); this.children.push(node); node.parentNode = this;
    }
    this.mutations++;
  }
  appendChild(node) { this.append(node); return node; }
  insertBefore(node, before) {
    if (node === before) return node;
    node.remove();
    const index = before ? this.children.indexOf(before) : this.children.length;
    assert.ok(index >= 0, 'insertBefore target must belong to the parent');
    this.children.splice(index, 0, node); node.parentNode = this; this.mutations++;
    return node;
  }
  remove() {
    if (!this.parentNode) return;
    const parent = this.parentNode;
    parent.children.splice(parent.children.indexOf(this), 1);
    this.parentNode = null; parent.mutations++;
  }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parentNode = null;
    this.children = []; this._text = ''; this.append(...nodes);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  set innerHTML(_) { throw new Error('Run evidence must remain literal text'); }
}

function fixtureRuns() {
  return [
    {slug: 'confirmed', task: 'Task: Fix the live renderer. Preserve existing output.\nFull instructions.',
      model: 'Local', status: 'running', activity_state: 'running', review_eligible: false},
    {slug: 'unconfirmed', task: 'Worker with unknown liveness', model: 'Local',
      status: 'running', activity_state: 'unconfirmed', review_eligible: false},
    {slug: 'pending', task: '### Review <script>literal</script> changes\nSecond line',
      model: 'Local', status: 'completed', activity_state: 'ended', review_eligible: true},
    {slug: 'reviewing', task: 'Inspect verification output', model: 'Other', status: 'verify_failed',
      activity_state: 'ended', review_eligible: true, review_in_progress: true},
    {slug: 'accepted', task: 'Saved decision', model: 'Local', status: 'completed',
      activity_state: 'ended', review_eligible: true, verdict: 'accept'},
    {slug: 'superseded', task: 'Previous attempt', model: 'Local', status: 'completed',
      activity_state: 'ended', review_eligible: true, resumed_by: 'pending'},
    {slug: 'unknown', task: 'Missing review projection', model: 'Local', status: 'completed',
      activity_state: 'ended'},
  ];
}

function harness(runs = fixtureRuns(), overrides = {}) {
  const start = source.indexOf('  function renderList()');
  const end = source.indexOf('  const timelineLanes =', start);
  assert.ok(start !== -1 && end > start, 'the actual run selector region must exist');
  const nodes = Object.fromEntries(['run-count', 'run-list', 'more-runs', 'no-matches', 'run-search',
    'run-jump', 'filter-all', 'filter-active', 'filter-review',
    'filter-count-all', 'filter-count-active', 'filter-count-review']
    .map(id => [id, new Element(id === 'run-jump' ? 'select' : 'div')]));
  const selections = [];
  const selected = runs.find(run => run.slug === 'accepted') || runs[0];
  const context = {
    state: {snapshot: {runs}, selected: selected?.slug, detail: selected, query: '',
      limit: 8, runScope: 'all', ...overrides},
    presentation,
    $: id => { assert.ok(nodes[id], `unexpected selector element: ${id}`); return nodes[id]; },
    element: (tag, className, content) => new Element(tag, className, content),
    number: value => String(value),
    duration: value => value == null ? '—' : `${value}s`,
    label: run => run.verdict || run.status,
    remember: () => {},
    selectRun: slug => { selections.push(slug); },
  };
  context.set = (id, value) => { context.$(id).textContent = value; };
  context.document = {createElement: tag => new Element(tag)};
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nthis.render = renderList;', context);
  return {nodes, state: context.state, selections, render: context.render};
}

function slugs(ui) { return ui.nodes['run-list'].children.map(node => node.dataset.slug); }
function jumpValues(ui) { return ui.nodes['run-jump'].children.map(node => node.value).filter(Boolean); }

test('scope filters preserve the selected detail and use canonical active and review evidence', () => {
  const ui = harness();
  const selected = ui.state.detail;
  ui.render();
  assert.equal(ui.nodes['filter-count-all'].textContent, '7');
  assert.equal(ui.nodes['filter-count-active'].textContent, '1');
  assert.equal(ui.nodes['filter-count-review'].textContent, '2');
  assert.equal(typeof ui.nodes['filter-active'].listeners.click, 'function');
  ui.nodes['filter-active'].listeners.click();
  assert.equal(ui.state.runScope, 'active');
  assert.deepEqual(slugs(ui), ['confirmed']);
  assert.equal(ui.nodes['filter-active'].getAttribute('aria-pressed'), 'true');
  assert.equal(ui.nodes['filter-all'].getAttribute('aria-pressed'), 'false');
  assert.equal(ui.state.selected, 'accepted');
  assert.equal(ui.state.detail, selected);
  assert.deepEqual(ui.selections, []);
  ui.nodes['filter-review'].listeners.click();
  assert.equal(ui.state.runScope, 'review');
  assert.deepEqual(slugs(ui), ['pending', 'reviewing']);
  assert.equal(ui.state.selected, 'accepted');
  assert.equal(ui.state.detail, selected);
  assert.deepEqual(ui.selections, []);
});

test('the compact jump selector includes every match even when the visible list is limited', () => {
  const ui = harness(fixtureRuns(), {limit: 2});
  ui.render();
  assert.deepEqual(jumpValues(ui),
    ['confirmed', 'unconfirmed', 'pending', 'reviewing', 'accepted', 'superseded', 'unknown']);
  assert.equal(ui.nodes['run-jump'].value, 'accepted');
  assert.equal(ui.nodes['more-runs'].hidden, false);
  assert.equal(typeof ui.nodes['run-jump'].listeners.change, 'function');
  ui.nodes['run-jump'].value = 'pending';
  ui.nodes['run-jump'].listeners.change();
  assert.deepEqual(ui.selections, ['pending']);
});

test('a selected run outside the filter leaves an explicit jump placeholder and keeps its detail', () => {
  const ui = harness(fixtureRuns(), {runScope: 'review'});
  const selected = ui.state.detail;
  ui.render();
  assert.deepEqual(jumpValues(ui), ['pending', 'reviewing']);
  assert.equal(ui.nodes['run-jump'].value, '');
  const placeholder = ui.nodes['run-jump'].children.find(node => node.value === '');
  assert.ok(placeholder, 'an outside-filter selection needs a neutral jump option');
  assert.match(placeholder.textContent, /select|choose|outside|filter/i);
  assert.equal(ui.state.selected, 'accepted');
  assert.equal(ui.state.detail, selected);
  assert.deepEqual(ui.selections, []);
});

test('identical polling retains jump options and an in-progress native selection', () => {
  const ui = harness();
  ui.render();
  const options = [...ui.nodes['run-jump'].children];
  assert.ok(options.length > 0, 'the jump selector must have options to retain');
  const mutations = ui.nodes['run-jump'].mutations;
  ui.nodes['run-jump'].value = 'pending';
  ui.state.snapshot.runs = JSON.parse(JSON.stringify(ui.state.snapshot.runs));
  ui.render();
  assert.deepEqual(ui.nodes['run-jump'].children, options);
  assert.equal(ui.nodes['run-jump'].mutations, mutations);
  assert.equal(ui.nodes['run-jump'].value, 'pending');
  assert.equal(ui.state.selected, 'accepted');
  assert.deepEqual(ui.selections, []);
});

test('compact selection distinguishes repeated attempts with the same task and status', () => {
  const ui = harness([
    {slug: 'attempt-one', task: 'Fix the renderer.', status: 'completed'},
    {slug: 'attempt-two', task: 'Fix the renderer.', status: 'completed'},
  ]);
  ui.render();
  const choices = ui.nodes['run-jump'].children.filter(node => node.value);
  assert.equal(new Set(choices.map(node => node.textContent)).size, 2);
  for (const choice of choices) assert.ok(choice.textContent.includes(choice.value));
});

test('polling new runs and changed labels keeps a pending compact selection', () => {
  const ui = harness();
  ui.render();
  const pending = ui.nodes['run-jump'].children.find(node => node.value === 'pending');
  ui.nodes['run-jump'].value = 'pending';
  ui.state.snapshot.runs.find(run => run.slug === 'confirmed').verdict = 'accept';
  ui.state.snapshot.runs.unshift({slug: 'new-run', task: 'New task', status: 'running'});
  ui.render();
  assert.equal(ui.nodes['run-jump'].children.find(node => node.value === 'pending'), pending);
  assert.equal(ui.nodes['run-jump'].value, 'pending');
  assert.ok(jumpValues(ui).includes('new-run'));
  assert.deepEqual(ui.selections, []);
});

test('run rows and jump labels use concise literal task titles while search keeps the full task', () => {
  const ui = harness();
  ui.render();
  const confirmed = ui.nodes['run-list'].children.find(node => node.dataset.slug === 'confirmed');
  assert.equal(confirmed.children[0].textContent, 'Fix the live renderer.');
  const pending = ui.nodes['run-list'].children.find(node => node.dataset.slug === 'pending');
  assert.equal(pending.children[0].textContent, 'Review <script>literal</script> changes');
  const jump = ui.nodes['run-jump'].children.find(node => node.value === 'pending');
  assert.ok(jump.textContent.includes('Review <script>literal</script> changes'));
  assert.doesNotMatch(jump.textContent, /Second line/);
  ui.nodes['run-search'].value = 'FULL INSTRUCTIONS';
  ui.nodes['run-search'].listeners.input();
  assert.deepEqual(slugs(ui), ['confirmed']);
  assert.deepEqual(jumpValues(ui), ['confirmed']);
  assert.equal(ui.state.selected, 'accepted');
  assert.deepEqual(ui.selections, []);
});

test('the loaded document provides the selection and review nodes before their controller runs', () => {
  const html = fs.readFileSync(path.join(assets, 'index.html'), 'utf8');
  assert.ok(html.indexOf('src="/presentation.js"') >= 0,
    'the document must load the shared presentation helpers');
  assert.ok(html.indexOf('src="/presentation.js"') < html.indexOf('src="/app.js"'));
  for (const id of ['run-jump', 'filter-all', 'filter-active', 'filter-review',
    'filter-count-all', 'filter-count-active', 'filter-count-review',
    'review-panel', 'review-form', 'review-summary', 'review-summary-title',
    'review-summary-note', 'review-history', 'review-events']) {
    assert.equal((html.match(new RegExp(`\\bid="${id}"`, 'g')) || []).length, 1,
      `the actual document needs exactly one ${id}`);
  }
  const selection = html.indexOf('aria-label="Run selection"');
  const detail = html.indexOf('id="run-panel"');
  const review = html.indexOf('id="review-panel"');
  const files = html.indexOf('id="files-details"');
  const verify = html.indexOf('id="verify-details"');
  const timeline = html.indexOf('id="timeline-chart"');
  assert.ok(selection < detail, 'run selection precedes selected detail in reading order');
  assert.ok(review < files && review < verify, 'review actions precede detailed file and verification evidence');
  assert.ok(files < timeline && verify < timeline, 'file and verification evidence precede the worker timeline');
});
