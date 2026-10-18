const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const assets = path.join(__dirname, '../dirtywork/dashboard_assets');
const source = fs.readFileSync(path.join(assets, 'app.js'), 'utf8');
const presentation = require(path.join(assets, 'presentation.js'));
const firstRepo = '/clients/one/shared';
const secondRepo = '/clients/two/shared';
const literalRepo = '/clients/<script>alert("repo")</script>/shared';
const writeControls = ['start-review', 'accept-review', 'reject-review', 'review-note', 'task-type'];
const scopeLabels = ['token-scope', 'outcomes-scope', 'funnel-scope', 'models-scope', 'fit-scope'];

class Element {
  constructor(tag, className = '', content = '') {
    this.tagName = tag.toUpperCase(); this.className = className;
    this.children = []; this.parentNode = null; this.attributes = {};
    this.dataset = {}; this.listeners = {}; this.hidden = false; this.disabled = false;
    this._text = content ?? ''; this._value = ''; this.mutations = 0;
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
  get value() { return this._value; }
  set value(value) {
    this._value = String(value);
    if (this.tagName === 'SELECT' && !this.children.some(node => node.value === this._value)) this._value = '';
  }
  get firstChild() { return this.children[0] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    return this.parentNode.children[this.parentNode.children.indexOf(this) + 1] || null;
  }
  get options() { return this.children; }
  reconcileSelection() {
    if (this.tagName === 'SELECT' && !this.children.some(node => node.value === this._value)) {
      this._value = this.children[0]?.value || '';
    }
  }
  append(...nodes) {
    for (const node of nodes) {
      node.remove(); this.children.push(node); node.parentNode = this;
    }
    this.reconcileSelection(); this.mutations++;
  }
  appendChild(node) { this.append(node); return node; }
  insertBefore(node, before) {
    if (node === before) return node;
    node.remove();
    const index = before ? this.children.indexOf(before) : this.children.length;
    assert.ok(index >= 0, 'insertBefore target must belong to the parent');
    this.children.splice(index, 0, node); node.parentNode = this;
    this.reconcileSelection(); this.mutations++;
    return node;
  }
  remove() {
    if (!this.parentNode) return;
    const parent = this.parentNode;
    parent.children.splice(parent.children.indexOf(this), 1);
    this.parentNode = null; parent.reconcileSelection(); parent.mutations++;
  }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parentNode = null;
    this.children = []; this._text = ''; this.append(...nodes);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  set innerHTML(_) { throw new Error('Repository and run evidence must remain literal text'); }
}

function fixture() {
  const runs = [
    {slug: 'second-ended', repo: secondRepo, task: 'Second repo saved task',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null},
    {slug: 'first-unconfirmed', repo: firstRepo, task: 'Unconfirmed worker',
      status: 'running', activity_state: 'unconfirmed', review_eligible: false, verdict: null},
    {slug: 'first-live', repo: firstRepo, task: 'Confirmed worker',
      status: 'running', activity_state: 'running', review_eligible: false, verdict: null},
    {slug: 'first-ended', repo: firstRepo, task: 'First repo saved task',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null},
    {slug: 'unknown', task: 'Legacy metadata',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null},
    {slug: 'literal', repo: literalRepo, task: '<img src=x onerror=alert(1)>',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null},
  ];
  const first = {repo: firstRepo,
    summary: {total: 3, active: 1, reviewing: 1, median_duration_s: 40,
      median_review_s: 6, offloaded_tokens: 1300},
    models: [{model: 'Local first', total: 3, median_duration_s: 40}],
    offloading: {groups: [{id: 'implementation', distinct_tasks: 3, median_review_s: 6}],
      reviewed: 2, review_samples: 2}};
  const second = {repo: secondRepo,
    summary: {total: 1, active: 0, reviewing: 0, median_duration_s: 19, offloaded_tokens: 2700},
    models: [{model: 'Local second', total: 1, median_duration_s: 19}],
    offloading: {groups: [{id: 'documentation', distinct_tasks: 1}], reviewed: 0, review_samples: 0}};
  const unknown = {repo: '', summary: {total: 1, active: 0, reviewing: 0}, models: [],
    offloading: {groups: [], reviewed: 0, review_samples: 0}};
  const literal = {repo: literalRepo, summary: {total: 1, active: 0, reviewing: 0}, models: [],
    offloading: {groups: [], reviewed: 0, review_samples: 0}};
  return {runs, repositories: [first, second, unknown, literal],
    summary: {total: 6, active: 1, reviewing: 1, median_duration_s: 90,
      median_review_s: 12, offloaded_tokens: 4700},
    models: [{model: 'Global model', total: 6, median_duration_s: 90}],
    offloading: {groups: [{id: 'other', distinct_tasks: 6}], reviewed: 3, review_samples: 3},
    task_type_options: [], warnings: [], runs_dir: '/recorded/runs', updated_at: '2026-10-18T11:00:00Z'};
}

function harness(overrides = {}) {
  const start = source.indexOf('  function renderRepositoryScope()');
  const end = source.indexOf('  function selectRun(slug)', start);
  assert.ok(start !== -1 && end > start, 'the actual repository controller region must exist');
  const nodes = Object.fromEntries(['repository', 'repository-context', ...scopeLabels,
    'connection', 'run-panel', 'run-loading', 'empty', 'run-task', ...writeControls,
    'worker-time', 'turns', 'verdict', 'worker-time-note', 'verdict-note']
    .map(id => [id, new Element(id === 'repository' ? 'select' : 'div')]));
  const allSnapshot = fixture();
  const remembered = [], detailLoads = [], lists = [], outcomes = [], woprRuns = [];
  const state = {allSnapshot, snapshot: allSnapshot, repository: null, selected: 'second-ended',
    detail: allSnapshot.runs[0], detailRevision: 7, reviewBusy: false, taskTypeBusy: null,
    reviewFeedback: null, taskTypeFeedback: null, limit: 30, query: '', runScope: 'all', ...overrides};
  const context = {
    state, presentation,
    $: id => { assert.ok(nodes[id], `unexpected repository controller element: ${id}`); return nodes[id]; },
    element: (tag, className, content) => new Element(tag, className, content),
    number: value => value == null ? '—' : String(value),
    remember: (key, value) => remembered.push([key, value]),
    renderWoprRun: run => woprRuns.push(run),
    renderList: () => lists.push(state.snapshot.runs),
    renderOutcomes: () => outcomes.push({summary: state.snapshot.summary,
      models: state.snapshot.models, offloading: state.snapshot.offloading}),
    loadDetail: async () => { detailLoads.push({slug: state.selected, revision: state.detailRevision}); },
  };
  context.set = (id, value) => { context.$(id).textContent = value; };
  context.document = {createElement: tag => new Element(tag), activeElement: null};
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) +
    '\nthis.apply = applyRepositoryScope; this.render = renderRepositoryScope;', context);
  return {state, nodes, remembered, detailLoads, lists, outcomes, woprRuns,
    apply: context.apply, render: context.render,
    async choose(value) {
      nodes.repository.value = value;
      assert.equal(typeof nodes.repository.listeners.change, 'function');
      await nodes.repository.listeners.change({target: nodes.repository});
    }};
}

function selectedRuns(ui) { return Array.from(ui.state.snapshot.runs, run => run.slug); }
function optionFor(ui, value) { return ui.nodes.repository.children.find(node => node.value === value); }

test('repository scope supplies all panels with the matching full server aggregate group', () => {
  const ui = harness({repository: firstRepo});
  const group = ui.state.allSnapshot.repositories[0];
  ui.apply();
  assert.deepEqual(selectedRuns(ui), ['first-unconfirmed', 'first-live', 'first-ended']);
  assert.equal(ui.state.snapshot.summary, group.summary);
  assert.equal(ui.state.snapshot.models, group.models);
  assert.equal(ui.state.snapshot.offloading, group.offloading);
  assert.equal(ui.outcomes.at(-1).summary, group.summary);
  assert.equal(ui.outcomes.at(-1).models, group.models);
  assert.equal(ui.outcomes.at(-1).offloading, group.offloading);
  assert.equal(ui.lists.at(-1), ui.state.snapshot.runs);
  assert.match(ui.nodes.connection.textContent, /1 active/);
  assert.match(ui.nodes.connection.textContent, /3 recorded/);
  assert.doesNotMatch(ui.nodes.connection.textContent, /6 recorded/);
  assert.match(ui.nodes['repository-context'].textContent, /shared|repository/i);
  for (const id of scopeLabels) assert.match(ui.nodes[id].textContent, /repository/i, id);
});

test('changing repositories selects confirmed activity inside that repository and disables stale writes', async () => {
  const ui = harness();
  ui.apply();
  await ui.choose('repo:' + firstRepo);
  assert.equal(ui.state.repository, firstRepo);
  assert.equal(ui.state.selected, 'first-live');
  assert.equal(ui.state.detail, null);
  assert.ok(ui.state.detailRevision > 7, 'a repository change must invalidate old detail requests');
  assert.equal(ui.state.limit, 8);
  for (const id of writeControls) assert.equal(ui.nodes[id].disabled, true, id);
  assert.equal(ui.nodes['run-panel'].hidden, true);
  assert.equal(ui.nodes['run-loading'].hidden, false);
  assert.match(ui.nodes['run-task'].textContent, /loading/i);
  assert.deepEqual(ui.detailLoads, [{slug: 'first-live', revision: ui.state.detailRevision}]);
  assert.ok(ui.remembered.some(([key, value]) => key === 'repository' && value === 'repo:' + firstRepo));
  assert.ok(ui.remembered.some(([key, value]) => key === 'run' && value === 'first-live'));
});

test('matching selected detail survives polling and a missing run falls back only within the repository', () => {
  const allSnapshot = fixture();
  const detail = allSnapshot.runs.find(run => run.slug === 'first-ended');
  const ui = harness({allSnapshot, repository: firstRepo, selected: detail.slug, detail});
  ui.apply();
  assert.equal(ui.state.selected, 'first-ended');
  assert.equal(ui.state.detail, detail);
  assert.equal(ui.state.detailRevision, 7);
  ui.state.allSnapshot = JSON.parse(JSON.stringify(allSnapshot));
  ui.apply();
  assert.equal(ui.state.selected, 'first-ended');
  assert.equal(ui.state.detail, detail);
  assert.equal(ui.state.detailRevision, 7);
  ui.state.allSnapshot.runs = ui.state.allSnapshot.runs.filter(run => run.slug !== 'first-ended');
  ui.apply();
  assert.equal(ui.state.selected, 'first-live');
  assert.equal(ui.state.detail, null);
  assert.ok(ui.state.detailRevision > 7);
  assert.ok(ui.state.snapshot.runs.some(run => run.slug === ui.state.selected));
});

test('unknown repository detail with missing metadata survives identical polls', () => {
  const allSnapshot = fixture();
  const detail = allSnapshot.runs.find(run => run.slug === 'unknown');
  const ui = harness({allSnapshot, repository: '', selected: 'unknown', detail});
  ui.apply();
  assert.equal(ui.state.detail, detail);
  assert.equal(ui.state.detailRevision, 7);
  assert.equal(ui.nodes['run-panel'].hidden, false);
  assert.equal(ui.nodes['run-loading'].hidden, true);
  ui.state.allSnapshot = JSON.parse(JSON.stringify(allSnapshot));
  ui.apply();
  assert.equal(ui.state.detail, detail);
  assert.equal(ui.state.detailRevision, 7);
  assert.equal(ui.nodes['run-panel'].hidden, false);
});

test('a matching slug cannot show detail recorded for another repository', () => {
  const ui = harness({repository: firstRepo, selected: 'first-live',
    detail: {slug: 'first-live', repo: secondRepo, task: 'Outside detail'}});
  ui.apply();
  assert.equal(ui.state.selected, 'first-live');
  assert.equal(ui.state.detail, null);
  assert.equal(ui.nodes['run-panel'].hidden, true);
  assert.equal(ui.nodes['run-loading'].hidden, false);
  for (const id of writeControls) assert.equal(ui.nodes[id].disabled, true, id);
  assert.equal(ui.nodes['worker-time'].textContent, '—');
  assert.equal(ui.nodes.verdict.textContent, '—');
});

test('All and an unknown recorded repository have distinct choices and selected data', async () => {
  const ui = harness();
  ui.apply();
  assert.ok(optionFor(ui, ''), 'All needs its own neutral option');
  assert.ok(optionFor(ui, 'repo:'), 'unknown recorded metadata needs a distinct option');
  assert.notEqual(optionFor(ui, ''), optionFor(ui, 'repo:'));
  await ui.choose('repo:');
  assert.equal(ui.state.repository, '');
  assert.deepEqual(selectedRuns(ui), ['unknown']);
  assert.equal(ui.state.selected, 'unknown');
  assert.equal(ui.state.snapshot.summary.total, 1);
  await ui.choose('');
  assert.equal(ui.state.repository, null);
  assert.equal(ui.state.snapshot, ui.state.allSnapshot);
  assert.equal(ui.state.snapshot.summary.total, 6);
  for (const id of scopeLabels) assert.match(ui.nodes[id].textContent, /all/i, id);
});

test('a stale saved repository explicitly resets the controller and remembered selection to All', () => {
  const ui = harness({repository: '/repository/that/disappeared'});
  ui.apply();
  assert.equal(ui.state.repository, null);
  assert.equal(ui.state.snapshot, ui.state.allSnapshot);
  assert.equal(ui.nodes.repository.value, '');
  assert.match(ui.nodes['repository-context'].textContent, /no longer available/i);
  assert.ok(ui.remembered.some(([key, value]) => key === 'repository' &&
    value === ''), 'the missing saved repository must be replaced by All');
});

test('a repository disappearing during polling resets All and clears its obsolete detail', () => {
  const ui = harness({repository: firstRepo});
  ui.apply();
  ui.state.detail = {...ui.state.allSnapshot.runs.find(run => run.slug === 'first-live')};
  ui.state.allSnapshot.runs = ui.state.allSnapshot.runs.filter(run => run.repo !== firstRepo);
  ui.state.allSnapshot.repositories = ui.state.allSnapshot.repositories.filter(group => group.repo !== firstRepo);
  ui.apply();
  assert.equal(ui.state.repository, null);
  assert.equal(ui.state.snapshot, ui.state.allSnapshot);
  assert.equal(ui.state.selected, 'second-ended');
  assert.equal(ui.state.detail, null);
  assert.equal(ui.nodes.repository.value, '');
  assert.match(ui.nodes['repository-context'].textContent, /no longer available/i);
  assert.equal(optionFor(ui, 'repo:' + firstRepo), undefined);
  assert.ok(ui.remembered.some(([key, value]) => key === 'repository' && value === ''));
});

test('identical polling retains option nodes and a pending native repository choice', () => {
  const ui = harness();
  ui.apply();
  const options = [...ui.nodes.repository.children];
  const mutations = ui.nodes.repository.mutations;
  ui.nodes.repository.value = 'repo:' + firstRepo;
  ui.state.allSnapshot = JSON.parse(JSON.stringify(ui.state.allSnapshot));
  ui.apply();
  assert.deepEqual(ui.nodes.repository.children, options);
  assert.equal(ui.nodes.repository.mutations, mutations);
  assert.equal(ui.nodes.repository.value, 'repo:' + firstRepo);
  assert.equal(ui.state.repository, null);
  assert.equal(ui.state.selected, 'second-ended');
  assert.deepEqual(ui.detailLoads, []);
});

test('new and reordered repository groups preserve existing option identities and a pending choice', () => {
  const ui = harness();
  ui.apply();
  const pending = optionFor(ui, 'repo:' + firstRepo);
  const retained = optionFor(ui, 'repo:' + secondRepo);
  ui.nodes.repository.value = 'repo:' + firstRepo;
  const added = {repo: '/clients/new/shared', summary: {total: 1, active: 0}, models: [], offloading: {groups: []}};
  ui.state.allSnapshot.repositories = [added, ...ui.state.allSnapshot.repositories.slice().reverse()];
  ui.apply();
  assert.equal(optionFor(ui, 'repo:' + firstRepo), pending);
  assert.equal(optionFor(ui, 'repo:' + secondRepo), retained);
  assert.ok(optionFor(ui, 'repo:' + added.repo));
  assert.equal(ui.nodes.repository.value, 'repo:' + firstRepo);
  assert.equal(ui.state.repository, null);
});

test('option labels retain exact path evidence and literal XSS-like strings', () => {
  const ui = harness();
  ui.apply();
  const first = optionFor(ui, 'repo:' + firstRepo);
  const second = optionFor(ui, 'repo:' + secondRepo);
  assert.ok(first.textContent.includes(firstRepo));
  assert.ok(second.textContent.includes(secondRepo));
  assert.notEqual(first.textContent, second.textContent);
  const literal = optionFor(ui, 'repo:' + literalRepo);
  assert.ok(literal.textContent.includes(literalRepo));
  assert.equal(literal.children.length, 0);
});

test('an in-flight review or task type write prevents repository changes and detail requests', async () => {
  for (const busy of [{reviewBusy: true}, {taskTypeBusy: 'second-ended'}]) {
    const ui = harness(busy);
    const detail = ui.state.detail;
    ui.apply();
    await ui.choose('repo:' + firstRepo);
    assert.equal(ui.state.repository, null);
    assert.equal(ui.state.selected, 'second-ended');
    assert.equal(ui.state.detail, detail);
    assert.equal(ui.state.detailRevision, 7);
    assert.equal(ui.nodes.repository.value, '');
    assert.deepEqual(ui.detailLoads, []);
  }
});

test('an empty snapshot clears selection, hides detail and leaves all run write controls disabled', () => {
  const allSnapshot = fixture();
  allSnapshot.runs = []; allSnapshot.repositories = [];
  allSnapshot.summary = {total: 0, active: 0, reviewing: 0};
  const ui = harness({allSnapshot});
  ui.apply();
  assert.equal(ui.state.selected, null);
  assert.equal(ui.state.detail, null);
  assert.equal(ui.nodes['run-panel'].hidden, true);
  assert.equal(ui.nodes.empty.hidden, false);
  for (const id of writeControls) assert.equal(ui.nodes[id].disabled, true, id);
  assert.equal(ui.woprRuns.at(-1), null);
  assert.deepEqual(ui.detailLoads, []);
});

function detailHarness(repository = firstRepo) {
  const start = source.indexOf('  async function loadDetail()');
  const end = source.indexOf('  async function refresh()', start);
  assert.ok(start !== -1 && end > start, 'the actual detail loader must exist');
  const error = new Element('p', '', 'New scope is healthy');
  error.hidden = true;
  const rendered = [], requests = [];
  let resolveRead, rejectRead;
  const pending = new Promise((resolve, reject) => { resolveRead = resolve; rejectRead = reject; });
  const state = {repository, selected: 'retained-run', detailRevision: 3, detail: null,
    reviewBusy: false, taskTypeBusy: null};
  const context = {state,
    api: url => { requests.push(url); return pending; },
    renderDetail: run => rendered.push(run),
    $: id => { assert.equal(id, 'error'); return error; },
    set: (id, value) => { assert.equal(id, 'error'); error.textContent = value; }};
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nthis.load = loadDetail;', context);
  return {state, error, rendered, requests, resolveRead, rejectRead, load: context.load};
}

test('an obsolete failed detail read cannot show an error after the same run changes repository scope', async () => {
  const ui = detailHarness(null);
  const loading = ui.load();
  assert.deepEqual(ui.requests, ['/api/runs/retained-run']);
  // All -> one repository can retain a slug while invalidating its older request.
  ui.state.repository = firstRepo;
  ui.state.detailRevision++;
  ui.rejectRead(new Error('Obsolete global detail failure'));
  await loading;
  assert.equal(ui.state.selected, 'retained-run');
  assert.equal(ui.error.hidden, true);
  assert.equal(ui.error.textContent, 'New scope is healthy');
  assert.deepEqual(ui.rendered, []);
});

test('a failed detail read cannot replace feedback while a run write is in flight', async () => {
  for (const busy of [{reviewBusy: true}, {taskTypeBusy: 'retained-run'}]) {
    const ui = detailHarness();
    const loading = ui.load();
    Object.assign(ui.state, busy);
    ui.rejectRead(new Error('Read failed during a newer write'));
    await loading;
    assert.equal(ui.error.hidden, true);
    assert.equal(ui.error.textContent, 'New scope is healthy');
    assert.deepEqual(ui.rendered, []);
  }
});

test('a current failed detail read still exposes its actionable error', async () => {
  const ui = detailHarness();
  const loading = ui.load();
  ui.rejectRead(new Error('Current detail failed'));
  await loading;
  assert.equal(ui.error.hidden, false);
  assert.match(ui.error.textContent, /Current detail failed/);
});

test('a successful detail response from outside the selected repository is ignored', async () => {
  const ui = detailHarness(firstRepo);
  const loading = ui.load();
  ui.resolveRead({slug: 'retained-run', repo: secondRepo, task: 'Outside repository response'});
  await loading;
  assert.equal(ui.state.detail, null);
  assert.deepEqual(ui.rendered, []);
  assert.equal(ui.error.hidden, true);
});

test('unknown repository detail accepts empty and missing recorded repository metadata', async () => {
  for (const metadata of [{repo: ''}, {repo: null}, {}]) {
    const ui = detailHarness('');
    const loading = ui.load();
    const run = {slug: 'retained-run', task: 'Unknown repository response', ...metadata};
    ui.resolveRead(run);
    await loading;
    assert.equal(ui.state.detail, run);
    assert.deepEqual(ui.rendered, [run]);
    assert.equal(ui.error.hidden, true);
  }
});
