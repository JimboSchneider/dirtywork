const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

// Run the actual renderer; task labels must remain text throughout rendering.
const source = fs.readFileSync(path.join(__dirname, '../dirtywork/dashboard_assets/app.js'), 'utf8');
class Element {
  constructor(tag, className = '', content = '') {
    this.tag = tag; this.className = className; this.children = []; this.attributes = {};
    this.dataset = {}; this.style = {setProperty(name, value) { this[name] = value; }};
    this.listeners = {}; this.hidden = false; this.mutations = 0; this._text = content ?? '';
    this.classList = {toggle: (name, active) => {
      const classes = new Set(this.className.split(/\s+/).filter(Boolean));
      if (active) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' ');
    }};
  }
  get textContent() { return String(this._text) + this.children.map(node => node.textContent).join(''); }
  set textContent(value) { this._text = value ?? ''; this.children = []; this.mutations++; }
  append(...nodes) { this.children.push(...nodes); this.mutations++; }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) { this._text = ''; this.children = [...nodes]; this.mutations++; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  set innerHTML(_) { throw new Error('Offloading evidence must never be interpreted as HTML'); }
}
function harness(offloading) {
  const start = source.indexOf('  function renderOffloading(');
  assert.notEqual(start, -1, 'the offloading renderer must exist');
  const end = source.indexOf('  function renderOutcomes(', start);
  assert.ok(end > start, 'the offloading renderer must precede outcomes');
  const nodes = Object.fromEntries(['offload-fit-summary', 'offload-fit-empty', 'offload-fit-chart',
    'offload-fit-types', 'offload-fit-selection'].map(id => [id, new Element('div')]));
  const context = {
    state: {snapshot: {offloading}}, $: id => {
      assert.ok(nodes[id], `unexpected dashboard element: ${id}`); return nodes[id];
    },
    element: (tag, className, content) => new Element(tag, className, content),
    number: value => value == null ? '—' : new Intl.NumberFormat('en-US', {maximumFractionDigits: 0}).format(value),
    duration: value => value == null ? '—' : value < 60 ? `${Math.floor(value)}s`
      : `${Math.floor(value / 60)}m ${Math.floor(value) % 60}s`,
  };
  context.set = (id, value) => { context.$(id).textContent = value; };
  context.document = {createElement: tag => new Element(tag)};
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nthis.render = renderOffloading;', context);
  return {nodes, state: context.state, render: context.render};
}

function all(node) { return [node, ...node.children.flatMap(all)]; }
function points(ui) { return all(ui.nodes['offload-fit-chart']).filter(node => /\bfit-point\b/.test(node.className)); }
function group(overrides = {}) {
  return {id: 'code', label: 'Code changes', total: 20, reviewed: 4, accepted: 3,
    acceptance_rate: .75, median_review_s: 12, review_samples: 3, distinct_tasks: 10,
    distinct_reviewed_tasks: 3, distinct_timed_tasks: 2, suggested_runs: 0,
    evidence: 'limited', ...overrides};
}
function history(groups, overrides = {}) {
  return {groups, excluded_demos: 2, reviewed: 4, review_samples: 3, unclassified: 0, ...overrides};
}

test('missing offloading history has no invented chart points', () => {
  const ui = harness(undefined);
  ui.render();
  assert.equal(ui.nodes['offload-fit-empty'].hidden, false);
  assert.equal(points(ui).length, 0);
});

test('untimed reviews stay unplotted and do not become zero-second reviews', () => {
  const ui = harness(history([group({median_review_s: null, review_samples: 0,
    distinct_timed_tasks: 0, evidence: 'no_timing'})]));
  ui.render();
  assert.equal(points(ui).length, 0);
  assert.equal(ui.nodes['offload-fit-empty'].hidden, false);
  const text = ui.nodes['offload-fit-types'].textContent;
  assert.match(text, /Not timed/i);
  assert.match(text, /No timing/i);
  assert.ok(!all(ui.nodes['offload-fit-types']).some(node => node.textContent === '0s'));
});

test('a genuine zero-second review and zero acceptance remain valid chart evidence', () => {
  const ui = harness(history([group({accepted: 0, acceptance_rate: 0,
    median_review_s: 0, review_samples: 1, distinct_timed_tasks: 1})]));
  ui.render();
  assert.equal(points(ui).length, 1);
  assert.equal(ui.nodes['offload-fit-empty'].hidden, true);
  assert.ok(all(ui.nodes['offload-fit-types']).some(node => node.textContent === '0s'));
  assert.match(ui.nodes['offload-fit-types'].textContent, /0%/);
});

test('only finite median and acceptance pairs produce chart marks', () => {
  const ui = harness(history([
    group({id: 'untimed', median_review_s: null}),
    group({id: 'unreviewed', reviewed: 0, accepted: 0, acceptance_rate: null}),
    group({id: 'infinite-time', median_review_s: Infinity}),
    group({id: 'infinite-rate', acceptance_rate: Infinity}),
    group({id: 'timed', label: 'Real timed work'}),
  ]));
  ui.render();
  assert.equal(points(ui).length, 1);
  assert.match(points(ui)[0].textContent + (points(ui)[0].attributes['aria-label'] || ''), /Real timed work/);
});

test('acceptance uses reviewed decisions while revealing repeated briefs and timing coverage', () => {
  const ui = harness(history([group()]));
  ui.render();
  const row = ui.nodes['offload-fit-types'].children[0];
  assert.equal(row.children.length, 4);
  assert.match(row.children[0].textContent, /20/);
  assert.match(row.children[0].textContent, /10/);
  assert.match(row.children[1].textContent, /75%/);
  assert.match(row.children[1].textContent, /3\s*(?:\/|of)\s*4/);
  assert.match(row.children[2].textContent, /3\s*\/\s*4 reviews timed/i);
  assert.match(row.children[3].textContent, /3 distinct reviewed briefs.*2 timed/i);
  assert.match(row.children[3].textContent, /Limited/i);
});

test('repeated reviews of one brief do not present a large timing sample as strong evidence', () => {
  const ui = harness(history([group({total: 12, reviewed: 9, accepted: 9, acceptance_rate: 1,
    review_samples: 9, distinct_tasks: 2, distinct_reviewed_tasks: 1,
    distinct_timed_tasks: 1, evidence: 'limited'})]));
  ui.render();
  const row = ui.nodes['offload-fit-types'].children[0];
  assert.match(row.children[3].textContent, /1 distinct reviewed brief.*1 timed/i);
  assert.match(row.children[3].textContent, /Limited/i);
  assert.doesNotMatch(row.children[3].textContent, /5\+|ready|strong/i);
});

test('task labels remain literal text in the table, point label, and selected explanation', () => {
  const label = '<img src=x onerror=alert(1)> & <script>task</script>';
  const ui = harness(history([group({label})]));
  ui.render();
  assert.ok(all(ui.nodes['offload-fit-types']).some(node => node.textContent === label));
  const point = points(ui)[0];
  assert.ok((point.textContent + (point.attributes['aria-label'] || '')).includes(label));
  assert.equal(typeof point.listeners.click, 'function');
  point.listeners.click();
  assert.ok(ui.nodes['offload-fit-selection'].textContent.includes(label));
});

test('identical live snapshots retain the table and chart DOM, while changed evidence updates them', () => {
  const initial = history([group()]);
  const ui = harness(initial);
  ui.render();
  const row = ui.nodes['offload-fit-types'].children[0];
  const point = points(ui)[0];
  const tableMutations = ui.nodes['offload-fit-types'].mutations;
  const chartMutations = ui.nodes['offload-fit-chart'].mutations;
  ui.state.snapshot.offloading = JSON.parse(JSON.stringify(initial));
  ui.render();
  assert.equal(ui.nodes['offload-fit-types'].children[0], row);
  assert.equal(points(ui)[0], point);
  assert.equal(ui.nodes['offload-fit-types'].mutations, tableMutations);
  assert.equal(ui.nodes['offload-fit-chart'].mutations, chartMutations);
  ui.state.snapshot.offloading.groups[0].median_review_s = 42;
  ui.render();
  assert.match(ui.nodes['offload-fit-types'].textContent, /42s/);
  assert.notEqual(points(ui)[0], point);
});

function taskTypeHarness(fetchResult, detailResult) {
  const start = source.indexOf('  function renderTaskType(run)');
  assert.notEqual(start, -1, 'the editable task type renderer must exist');
  const end = source.indexOf('  function renderReviewControls(run)', start);
  assert.ok(end > start, 'task type controls must precede review controls');
  const nodes = Object.fromEntries(['task-type', 'task-type-source', 'task-type-feedback',
    'start-review', 'accept-review', 'reject-review', 'review-note', 'review-controls-state', 'review-feedback']
    .map(id => [id, new Element(id === 'task-type' ? 'select' : 'div')]));
  const run = {slug: 'run with spaces', verdict: 'accept', review_duration_s: 12,
    task_type: {id: 'code', label: 'Code changes', source: 'suggested'}};
  const calls = [], detailCalls = [], rendered = [];
  const context = {
    state: {snapshot: {csrf_token: 'request-token', task_type_options: [
      {id: 'code', label: 'Code changes'}, {id: 'tests', label: '<b>Test changes</b>'},
    ]}, selected: run.slug, detail: run, detailRevision: 0, taskTypeBusy: null, taskTypeFeedback: null,
    reviewBusy: false, reviewFeedback: null, reviewNotes: new Map()},
    $: id => { assert.ok(nodes[id], `unexpected task type element: ${id}`); return nodes[id]; },
    element: (tag, className, content) => new Element(tag, className, content),
    AbortSignal,
    fetch: async (url, options) => { calls.push({url, options}); return fetchResult(url, options); },
    api: async url => { detailCalls.push(url); return detailResult ? detailResult() : run; },
    renderDetail: detail => { rendered.push(detail); }, refresh: () => {},
  };
  context.set = (id, value) => { context.$(id).textContent = value; };
  vm.createContext(context);
  const detailStart = source.indexOf('  async function loadDetail()');
  const detailEnd = source.indexOf('  async function refresh()', detailStart);
  assert.ok(detailStart !== -1 && detailEnd > detailStart, 'the real detail loader must exist');
  const reviewControlsEnd = source.indexOf("  $('review-note').addEventListener", end);
  const reviewStart = source.indexOf('  async function performReview(action)');
  const reviewEnd = source.indexOf("  $('start-review').addEventListener", reviewStart);
  assert.ok(reviewControlsEnd > end && reviewStart !== -1 && reviewEnd > reviewStart,
    'the real review controls and save handler must exist');
  vm.runInContext(source.slice(start, end) + source.slice(end, reviewControlsEnd)
    + source.slice(reviewStart, reviewEnd) + source.slice(detailStart, detailEnd)
    + '\nthis.controls = {renderTaskType, performTaskType, renderReviewControls, performReview, loadDetail};', context);
  context.controls.renderTaskType(run);
  return {nodes, state: context.state, calls, detailCalls, rendered, ...context.controls};
}

test('saving an edited task type sends its ID with CSRF and preserves the review verdict', async () => {
  const saved = {slug: 'run with spaces', verdict: 'accept', review_duration_s: 12,
    task_type: {id: 'tests', label: '<b>Test changes</b>', source: 'manual'}};
  const ui = taskTypeHarness(async () => ({ok: true, json: async () => ({run: saved})}));
  assert.ok(all(ui.nodes['task-type']).some(node => node.textContent === '<b>Test changes</b>'));
  ui.nodes['task-type'].value = 'tests';
  await ui.performTaskType();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].url, '/api/runs/run%20with%20spaces/task-type');
  assert.equal(ui.calls[0].options.method, 'POST');
  assert.equal(ui.calls[0].options.headers['X-Dirtywork-CSRF'], 'request-token');
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), {task_type: 'tests'});
  assert.equal(ui.state.detail, saved);
  assert.equal(ui.state.detail.verdict, 'accept');
  assert.equal(ui.state.detail.review_duration_s, 12);
  assert.equal(ui.nodes['task-type'].disabled, false);
  assert.equal(ui.nodes['task-type'].value, 'tests');
  assert.match(ui.nodes['task-type-source'].textContent, /you/i);
});

test('resetting the task type sends null and returns the select to the automatic suggestion', async () => {
  const reset = {slug: 'run with spaces', verdict: 'accept',
    task_type: {id: 'code', label: 'Code changes', source: 'suggested'}};
  const ui = taskTypeHarness(async () => ({ok: true, json: async () => ({run: reset})}));
  ui.state.detail.task_type = {id: 'tests', label: 'Test changes', source: 'manual'};
  ui.renderTaskType(ui.state.detail);
  ui.nodes['task-type'].value = '';
  await ui.performTaskType();
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), {task_type: null});
  assert.equal(ui.nodes['task-type'].value, '');
  assert.match(ui.nodes['task-type'].children[0].textContent, /Code changes/);
  assert.match(ui.nodes['task-type-source'].textContent, /Suggested/i);
});

test('a task type save failure leaves current evidence intact and exposes a retryable error', async () => {
  const ui = taskTypeHarness(async () => ({ok: false, json: async () => ({error: '<script>Save failed</script>'})}));
  const original = ui.state.detail;
  ui.nodes['task-type'].value = 'tests';
  await ui.performTaskType();
  assert.equal(ui.state.detail, original);
  assert.equal(ui.state.detail.task_type.id, 'code');
  assert.equal(ui.rendered.length, 0);
  assert.equal(ui.state.taskTypeBusy, null);
  assert.equal(ui.nodes['task-type'].disabled, false);
  assert.equal(ui.state.taskTypeFeedback.error, true);
  assert.ok(ui.nodes['task-type-feedback'].textContent.includes('<script>Save failed</script>'));
});

test('a late task type response cannot replace the run selected while saving', async () => {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const ui = taskTypeHarness(() => response);
  ui.nodes['task-type'].value = 'tests';
  const saving = ui.performTaskType();
  const selected = {slug: 'new-selection', verdict: 'reject',
    task_type: {id: 'code', label: 'Code changes', source: 'suggested'}};
  ui.state.selected = selected.slug; ui.state.detail = selected; ui.state.detailRevision++;
  finish({ok: true, json: async () => ({run: {slug: 'run with spaces',
    task_type: {id: 'tests', label: 'Test changes', source: 'manual'}}})});
  await saving;
  assert.equal(ui.state.detail, selected);
  assert.equal(ui.rendered.length, 0);
  assert.equal(ui.nodes['task-type-feedback'].textContent, '');
});

test('detail polling during a task type save cannot overwrite the saved classification', async () => {
  let finishSave, finishRead;
  const saveResponse = new Promise(resolve => { finishSave = resolve; });
  const oldDetail = new Promise(resolve => { finishRead = resolve; });
  const ui = taskTypeHarness(() => saveResponse, () => oldDetail);
  const original = ui.state.detail;
  const saved = {...original, task_type: {id: 'tests', label: 'Test changes', source: 'manual'}};
  ui.nodes['task-type'].value = 'tests';
  const saving = ui.performTaskType();
  const polling = ui.loadDetail();
  finishSave({ok: true, json: async () => ({run: saved})});
  await saving;
  finishRead(original);
  await polling;
  assert.equal(ui.detailCalls.length, 0, 'do not request stale run detail while its type save is pending');
  assert.equal(ui.state.detail, saved);
  assert.equal(ui.nodes['task-type'].value, 'tests');
});

test('a detail read started before editing cannot replace the classification after its save', async () => {
  let finishRead;
  const oldDetail = new Promise(resolve => { finishRead = resolve; });
  let saved;
  const ui = taskTypeHarness(async () => ({ok: true, json: async () => ({run: saved})}), () => oldDetail);
  const original = ui.state.detail;
  saved = {...original, task_type: {id: 'tests', label: 'Test changes', source: 'manual'}};
  const polling = ui.loadDetail();
  assert.equal(ui.detailCalls.length, 1);
  ui.nodes['task-type'].value = 'tests';
  await ui.performTaskType();
  finishRead(original);
  await polling;
  assert.equal(ui.state.detail, saved);
  assert.equal(ui.nodes['task-type'].value, 'tests');
});

test('a pending review save disables task type editing and blocks a competing type POST', async () => {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const ui = taskTypeHarness(() => response);
  ui.state.detail.verdict = null;
  ui.state.detail.review_eligible = true;
  ui.state.detail.review_in_progress = true;
  ui.renderReviewControls(ui.state.detail);
  const reviewing = ui.performReview('accept');
  const disabledWhileSaving = ui.nodes['task-type'].disabled;
  ui.nodes['task-type'].value = 'tests';
  const typing = ui.performTaskType();
  const accepted = {...ui.state.detail, verdict: 'accept'};
  finish({ok: true, json: async () => ({run: accepted})});
  await Promise.all([reviewing, typing]);
  assert.equal(ui.calls.length, 1, 'a type change must wait for the pending review save');
  assert.match(ui.calls[0].url, /\/review$/);
  assert.equal(disabledWhileSaving, true);
  assert.equal(ui.nodes['task-type'].disabled, false);
  assert.equal(ui.state.detail, accepted);
});

test('a pending task type save disables review decisions and blocks a competing review POST', async () => {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const ui = taskTypeHarness(() => response);
  ui.state.detail.verdict = null;
  ui.state.detail.review_eligible = true;
  ui.state.detail.review_in_progress = true;
  ui.renderReviewControls(ui.state.detail);
  assert.equal(ui.nodes['accept-review'].disabled, false);
  ui.nodes['task-type'].value = 'tests';
  const typing = ui.performTaskType();
  const disabledWhileSaving = ['start-review', 'accept-review', 'reject-review']
    .map(id => ui.nodes[id].disabled);
  const reviewing = ui.performReview('accept');
  const saved = {...ui.state.detail, task_type: {id: 'tests', label: 'Test changes', source: 'manual'}};
  finish({ok: true, json: async () => ({run: saved})});
  await Promise.all([typing, reviewing]);
  assert.equal(ui.calls.length, 1, 'a review decision must wait for the pending task type save');
  assert.match(ui.calls[0].url, /\/task-type$/);
  assert.deepEqual(disabledWhileSaving, [true, true, true]);
  assert.equal(ui.nodes['accept-review'].disabled, false);
  assert.equal(ui.nodes['reject-review'].disabled, false);
  assert.equal(ui.state.detail, saved);
});

test('a late review response cannot replace a newer detail revision for the same run', async () => {
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const ui = taskTypeHarness(() => response);
  ui.state.detail.verdict = null;
  ui.state.detail.review_eligible = true;
  ui.state.detail.review_in_progress = true;
  const stale = {...ui.state.detail, verdict: 'accept'};
  const reviewing = ui.performReview('accept');
  const newer = {...ui.state.detail,
    task_type: {id: 'tests', label: 'Test changes', source: 'manual'}};
  ui.state.detailRevision++;
  ui.state.detail = newer;
  finish({ok: true, json: async () => ({run: stale})});
  await reviewing;
  assert.equal(ui.state.detail, newer);
  assert.equal(ui.rendered.length, 0);
});
