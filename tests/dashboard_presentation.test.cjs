const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const helperPath = path.join(__dirname, '../dirtywork/dashboard_assets/presentation.js');

function presentation() {
  assert.ok(fs.existsSync(helperPath), 'dashboard presentation helpers must exist');
  return require(helperPath);
}

test('task titles use the first meaningful line or sentence without splitting filenames', () => {
  const {taskTitle} = presentation();
  assert.equal(taskTitle('\n\n### Task: Fix dirtywork/dashboard_data.py and tests/test_dashboard.py. Keep the change small.\nMore context'),
    'Fix dirtywork/dashboard_data.py and tests/test_dashboard.py.');
  assert.equal(taskTitle('Run tests\nThen inspect the report.'), 'Run tests');
  assert.equal(taskTitle('Task:\n\nUpdate v1.2.3 in config.json'), 'Update v1.2.3 in config.json');
});

test('task titles cap long text at a word boundary and preserve literal task text', () => {
  const {taskTitle} = presentation();
  const task = '<script>alert("task")</script> & fix the dashboard while keeping every requested behavior and preserving the complete task elsewhere';
  assert.equal(taskTitle(task),
    '<script>alert("task")</script> & fix the dashboard while keeping every requested behavior and…');
  assert.equal(taskTitle('Fix <img src=x onerror=alert(1)> & keep **literal** text.'),
    'Fix <img src=x onerror=alert(1)> & keep **literal** text.');
  assert.equal(taskTitle('x'.repeat(100)), 'x'.repeat(95) + '…');
});

test('empty task titles use only the provided fallback', () => {
  const {taskTitle} = presentation();
  assert.equal(taskTitle(' \n\t'), 'Untitled run');
  assert.equal(taskTitle(undefined, 'No task'), 'No task');
  assert.equal(taskTitle({slug: 'do-not-derive-a-title'}), 'Untitled run');
});

const runs = [
  {slug: 'confirmed', task: 'Fix live rendering', model: 'Qwen', repo: '/repo/one',
    worktree: '/worktrees/live-rendering', branch: 'codex/renderer',
    status: 'running', activity_state: 'running', review_eligible: false, verdict: null,
    task_type: {label: 'Code changes'}},
  {slug: 'unconfirmed', task: 'An uncertain worker', model: 'Other', repo: '/repo/two',
    status: 'running', activity_state: 'unconfirmed', review_eligible: false, verdict: null},
  {slug: 'pending', task: 'Update docs', model: 'Qwen', repo: '/repo/one',
    status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null,
    task_type: {label: 'Documentation'}},
  {slug: 'reviewing', task: 'Exercise browser', model: 'Other', repo: '/repo/two',
    status: 'verify_failed', activity_state: 'ended', review_eligible: true,
    review_in_progress: true, verdict: null},
  {slug: 'accepted', task: 'Accepted task', model: 'Qwen', repo: '/repo/one',
    status: 'completed', activity_state: 'ended', review_eligible: true, verdict: 'accept'},
  {slug: 'superseded', task: 'An older task', model: 'Other', repo: '/repo/two',
    status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null,
    resumed_by: 'pending'},
  {slug: 'unprojected', task: 'Unknown eligibility', model: 'Other', repo: '/repo/two',
    status: 'completed', activity_state: 'ended', verdict: null},
];

test('active and review scopes use confirmed projected evidence', () => {
  const {filterRuns} = presentation();
  assert.deepEqual(filterRuns(runs, {scope: 'active'}).map(run => run.slug), ['confirmed']);
  assert.deepEqual(filterRuns(runs, {scope: 'review'}).map(run => run.slug), ['pending', 'reviewing']);
  assert.deepEqual(filterRuns(runs).map(run => run.slug),
    ['confirmed', 'unconfirmed', 'pending', 'reviewing', 'accepted', 'superseded', 'unprojected']);
});

test('search matches literal evidence fields case insensitively within the selected scope', () => {
  const {filterRuns} = presentation();
  const cases = [
    ['LIVE RENDERING', ['confirmed']],
    ['unconfirmed', ['unconfirmed']],
    ['qWeN', ['confirmed', 'pending', 'accepted']],
    ['/REPO/ONE', ['confirmed', 'pending', 'accepted']],
    ['/WORKTREES/LIVE-RENDERING', ['confirmed']],
    ['CODEX/RENDERER', ['confirmed']],
    ['verify_FAILED', ['reviewing']],
    ['ACCEPT', ['accepted']],
    ['DOCUMENTATION', ['pending']],
  ];
  for (const [query, expected] of cases) {
    assert.deepEqual(filterRuns(runs, {query}).map(run => run.slug), expected, query);
  }
  assert.deepEqual(filterRuns(runs, {scope: 'review', query: 'QWEN'}).map(run => run.slug), ['pending']);
  assert.deepEqual(filterRuns(runs, {query: '<script>'}), []);
});

test('filtering and counting preserve the input and do not invent eligibility', () => {
  const {filterRuns, runCounts} = presentation();
  const original = JSON.stringify(runs);
  assert.deepEqual(runCounts(runs), {all: 7, active: 1, review: 2});
  assert.deepEqual(runCounts([]), {all: 0, active: 0, review: 0});
  const result = filterRuns(runs, {scope: 'active'});
  assert.equal(result[0], runs[0]);
  assert.notEqual(result, runs);
  assert.equal(JSON.stringify(runs), original);
});

test('the same helpers are available in the browser namespace', () => {
  presentation();
  const context = {window: {}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context);
  assert.equal(context.window.DirtyworkPresentation.taskTitle('Task: Browser title'), 'Browser title');
  assert.equal(context.window.DirtyworkPresentation.filterRuns(runs, {scope: 'active'})[0], runs[0]);
  assert.deepEqual({...context.window.DirtyworkPresentation.runCounts(runs)}, {all: 7, active: 1, review: 2});
});
