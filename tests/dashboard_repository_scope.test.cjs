const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const helperPath = path.join(__dirname, '../dirtywork/dashboard_assets/presentation.js');
const {scopeSnapshot, filterRuns, runCounts} = require(helperPath);

const firstRepo = '/clients/one/shared';
const secondRepo = '/clients/two/shared';
const literalRepo = '/clients/<img src=x onerror=alert(1)>/shared';
const briefPreview = 'A shared truncated task preview. '.repeat(10).slice(0, 240);
const snapshot = {
  runs: [
    {slug: 'first-live', repo: firstRepo, task: briefPreview, model: 'Local',
      worktree: '/worktrees/first-live', branch: 'codex/FIRST',
      status: 'running', activity_state: 'running', review_eligible: false, verdict: null},
    {slug: 'first-review', repo: firstRepo, task: briefPreview, model: 'Local',
      worktree: '/worktrees/first-review', branch: 'codex/shared-branch',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null},
    {slug: 'first-accepted', repo: firstRepo, task: briefPreview, model: 'Other',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: 'accept'},
    {slug: 'second-live', repo: secondRepo, task: 'Second repository', model: 'Local',
      worktree: '/worktrees/second-live', branch: 'codex/shared-branch',
      status: 'running', activity_state: 'running', review_eligible: false, verdict: null},
    {slug: 'unknown-empty', repo: '', task: 'No recorded repository',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null},
    {slug: 'unknown-missing', task: 'Legacy metadata with no repository',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: 'reject'},
    {slug: 'literal', repo: literalRepo, task: '<script>alert("task")</script>',
      worktree: '/worktrees/<script>alert("tree")</script>', branch: 'fix/<b>literal</b>',
      status: 'completed', activity_state: 'ended', review_eligible: true, verdict: null},
  ],
  summary: {total: 7, median_duration_s: 90, median_review_s: 12, offloaded_tokens: 4700},
  models: [{model: 'Local', total: 3, median_duration_s: 90, offloaded_tokens: 3100}],
  offloading: {groups: [{id: 'implementation', distinct_tasks: 7, median_review_s: 12}],
    reviewed: 3, review_samples: 3},
  repositories: [
    {repo: firstRepo,
      summary: {total: 3, median_duration_s: 40, median_review_s: 6, offloaded_tokens: 1300},
      models: [{model: 'Local', total: 2, median_duration_s: 25, offloaded_tokens: 400},
        {model: 'Other', total: 1, median_duration_s: 90, offloaded_tokens: 900}],
      offloading: {groups: [{id: 'implementation', distinct_tasks: 3, median_review_s: 6}],
        reviewed: 1, review_samples: 1}},
    {repo: secondRepo,
      summary: {total: 1, median_duration_s: null, median_review_s: null, offloaded_tokens: 2700},
      models: [{model: 'Local', total: 1, offloaded_tokens: 2700}],
      offloading: {groups: [{id: 'documentation', distinct_tasks: 1}], reviewed: 0, review_samples: 0}},
    {repo: '', summary: {total: 2, median_review_s: 18}, models: [],
      offloading: {groups: [], reviewed: 1, review_samples: 1}},
    {repo: literalRepo, summary: {total: 1}, models: [],
      offloading: {groups: [], reviewed: 0, review_samples: 0}},
  ],
  warnings: ['A retained server warning'],
  task_type_options: [{id: 'implementation', label: 'Implementation'}],
  runs_dir: '/recorded/runs',
  updated_at: '2026-10-18T11:00:00Z',
};

function freeze(value) {
  if (value && typeof value === 'object') {
    Object.freeze(value);
    for (const item of Object.values(value)) freeze(item);
  }
  return value;
}
freeze(snapshot);

test('All repositories preserves the complete server snapshot and aggregates', () => {
  assert.equal(scopeSnapshot(snapshot), snapshot);
  assert.equal(scopeSnapshot(snapshot, null), snapshot);
  assert.equal(scopeSnapshot(snapshot).summary.offloaded_tokens, 4700);
});

test('repository selection keeps exact recorded paths distinct and reuses full server aggregates', () => {
  const scoped = scopeSnapshot(snapshot, firstRepo);
  const group = snapshot.repositories[0];
  assert.notEqual(scoped, snapshot);
  assert.notEqual(scoped.runs, snapshot.runs);
  assert.deepEqual(scoped.runs.map(run => run.slug), ['first-live', 'first-review', 'first-accepted']);
  assert.equal(scoped.runs[0], snapshot.runs[0]);
  assert.equal(scoped.summary, group.summary);
  assert.equal(scoped.models, group.models);
  assert.equal(scoped.offloading, group.offloading);
  assert.equal(scoped.summary.total, 3);
  assert.equal(scoped.summary.median_duration_s, 40);
  assert.equal(scoped.summary.median_review_s, 6);
  assert.equal(scoped.summary.offloaded_tokens, 1300);
  assert.equal(scoped.models[0].median_duration_s, 25);
  // All three previews are identical; distinct full briefs were counted on the server.
  assert.equal(scoped.offloading.groups[0].distinct_tasks, 3);
  assert.equal(scoped.warnings, snapshot.warnings);
  assert.equal(scoped.task_type_options, snapshot.task_type_options);
  assert.equal(scoped.repositories, snapshot.repositories);
  assert.equal(scoped.runs_dir, '/recorded/runs');
  assert.equal(scoped.updated_at, '2026-10-18T11:00:00Z');

  const second = scopeSnapshot(snapshot, secondRepo);
  assert.deepEqual(second.runs.map(run => run.slug), ['second-live']);
  assert.equal(second.summary.offloaded_tokens, 2700);
  assert.equal(snapshot.runs.length, 7);
  assert.equal(snapshot.summary.total, 7);
});

test('an empty recorded repository includes missing metadata and remains distinct from All', () => {
  const scoped = scopeSnapshot(snapshot, '');
  assert.deepEqual(scoped.runs.map(run => run.slug), ['unknown-empty', 'unknown-missing']);
  assert.equal(scoped.summary, snapshot.repositories[2].summary);
  assert.equal(scoped.summary.total, 2);
  assert.equal(scopeSnapshot(snapshot, null).runs.length, 7);
});

test('an absent selection is explicit so the controller can reset to All', () => {
  for (const repository of ['/gone/repository', 'shared', '/clients/one/shared/', '/CLIENTS/one/shared']) {
    assert.equal(scopeSnapshot(snapshot, repository), null, repository);
  }
  assert.equal(scopeSnapshot({...snapshot, repositories: []}, firstRepo), null);
  assert.equal(scopeSnapshot({runs: []}, firstRepo), null);
});

test('repository and search values preserve XSS-like strings as literal evidence', () => {
  const scoped = scopeSnapshot(snapshot, literalRepo);
  assert.deepEqual(scoped.runs.map(run => run.slug), ['literal']);
  assert.equal(scoped.runs[0].repo, literalRepo);
  assert.equal(scoped.runs[0].task, '<script>alert("task")</script>');
  assert.deepEqual(filterRuns(scoped.runs, {query: '<script>'}).map(run => run.slug), ['literal']);
  assert.deepEqual(filterRuns(scoped.runs, {query: '<B>LITERAL</B>'}).map(run => run.slug), ['literal']);
});

test('counts, scopes and metadata searches only inspect runs in the selected repository', () => {
  const scoped = scopeSnapshot(snapshot, firstRepo);
  assert.deepEqual(runCounts(scoped.runs), {all: 3, active: 1, review: 1});
  assert.deepEqual(filterRuns(scoped.runs, {scope: 'active'}).map(run => run.slug), ['first-live']);
  assert.deepEqual(filterRuns(scoped.runs, {scope: 'review'}).map(run => run.slug), ['first-review']);
  assert.deepEqual(filterRuns(scoped.runs, {query: 'CODEX/SHARED-BRANCH'}).map(run => run.slug), ['first-review']);
  assert.deepEqual(filterRuns(scoped.runs, {query: '/WORKTREES/FIRST-LIVE'}).map(run => run.slug), ['first-live']);
  assert.deepEqual(filterRuns(scoped.runs, {scope: 'review', query: '/worktrees/first-live'}), []);
  assert.deepEqual(filterRuns(scoped.runs, {query: secondRepo}), []);
  assert.equal(scoped.summary.total, 3);
});

test('the repository helper is exported in the browser namespace', () => {
  const context = {window: {}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(helperPath, 'utf8'), context);
  const scoped = context.window.DirtyworkPresentation.scopeSnapshot(snapshot, secondRepo);
  assert.equal(scoped.runs.length, 1);
  assert.equal(scoped.runs[0].slug, 'second-live');
  assert.equal(scoped.summary, snapshot.repositories[1].summary);
});
