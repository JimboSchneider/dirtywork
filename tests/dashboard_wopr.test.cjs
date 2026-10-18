const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const modulePath = path.join(__dirname, '../dirtywork/dashboard_assets/wopr.js');
const {statusStamp, hasNewActivity} = require(modulePath);

function run(overrides = {}) {
  return {
    slug: 'test-mission',
    status: 'running',
    activity_state: 'running',
    turns: 0,
    tool_calls: 0,
    activity: [],
    ...overrides,
  };
}

function event(sequence, overrides = {}) {
  return {sequence, event: 'tool_result', ts: '2026-10-18T12:00:00Z', ...overrides};
}

test('browser script exposes the same pure helpers without CommonJS or a DOM', () => {
  const browser = {};
  vm.runInNewContext(readFileSync(modulePath, 'utf8'), browser);
  assert.equal(typeof browser.DirtyworkWopr.statusStamp, 'function');
  assert.equal(typeof browser.DirtyworkWopr.hasNewActivity, 'function');
  assert.equal(browser.DirtyworkWopr.statusStamp(run()), '[ ESTABLISHING UPLINK… ]');
});

test('no selected run has no status stamp', () => {
  assert.equal(statusStamp(null), '');
});

test('a live process waits for worker evidence before claiming it is online', () => {
  assert.equal(statusStamp(run()), '[ ESTABLISHING UPLINK… ]');
  assert.equal(statusStamp(run({activity: [event(0, {event: 'run_start'})]})), '[ ESTABLISHING UPLINK… ]');
});

test('recorded model turns or tool results establish that the worker is online', () => {
  for (const evidence of [{turns: 1}, {tool_calls: 1}]) {
    assert.equal(statusStamp(run(evidence)), '[ WORKER ONLINE ]');
  }
});

test('model and tool activity establish worker evidence even when counts are zero', () => {
  for (const kind of ['assistant', 'tool_result']) {
    assert.equal(statusStamp(run({activity: [event(1, {event: kind})]})), '[ WORKER ONLINE ]');
  }
});

test('completed runs report the mission result and respect failed verification', () => {
  const completed = run({status: 'completed', activity_state: 'ended'});
  assert.equal(statusStamp(completed), '[ MISSION COMPLETE ]');
  assert.equal(statusStamp({...completed, verify: {passed: true}}), '[ MISSION COMPLETE ]');
  assert.equal(statusStamp({...completed, verify: {passed: false}}), '[ VERIFICATION FAILED ]');
});

test('stopped processes and terminal failures report lost signal', () => {
  assert.equal(statusStamp(run({activity_state: 'inactive', turns: 2})), '[ SIGNAL LOST ]');
  for (const status of ['interrupted', 'timeout', 'budget_exceeded', 'sandbox_error', 'error']) {
    assert.equal(statusStamp(run({status, activity_state: 'ended'})), '[ SIGNAL LOST ]');
  }
});

test('unconfirmed and unknown states do not claim a worker connection', () => {
  for (const activity_state of ['unconfirmed', 'unknown']) {
    assert.equal(statusStamp(run({activity_state, turns: 3})), '[ AWAITING SIGNAL ]');
  }
  assert.equal(statusStamp({status: 'unknown', activity_state: 'unknown'}), '[ AWAITING SIGNAL ]');
});

test('initial load, cleared selection, and switching runs do not count as new activity', () => {
  const selected = run({activity: [event(4)]});
  assert.equal(hasNewActivity(null, selected), false);
  assert.equal(hasNewActivity(selected, null), false);
  assert.equal(hasNewActivity(selected, {...selected, slug: 'another-mission', activity: [event(5)]}), false);
});

test('an unseen sequence ID in the same run counts as new activity', () => {
  const previous = run({activity: [event(4)]});
  assert.equal(hasNewActivity(previous, run({activity: [event(4), event(5)]})), true);
  assert.equal(hasNewActivity(run(), run({activity: [event(0, {event: 'run_start'})]})), true);
});

test('rolling the fixed-size activity window retains old identities and detects the new event', () => {
  const previous = run({activity: Array.from({length: 60}, (_, index) => event(index + 6))});
  const next = run({activity: Array.from({length: 60}, (_, index) => event(index + 7))});
  assert.equal(previous.activity.length, next.activity.length);
  assert.equal(hasNewActivity(previous, next), true);
  assert.equal(hasNewActivity(next, {...next, activity: next.activity.map(item => ({...item}))}), false);
});

test('retained IDs remain unchanged activity despite timestamp or detail changes', () => {
  const previous = run({activity: [event(4)]});
  const next = run({activity: [event(4, {ts: '2026-10-18T12:00:03Z', detail: 'Updated preview'})]});
  assert.equal(hasNewActivity(previous, next), false);
});

test('status, elapsed time, and count updates alone do not trigger activity', () => {
  const previous = run({activity: [event(4)], duration_s: 3});
  const next = {...previous, status: 'completed', activity_state: 'ended', duration_s: 6, turns: 2, tool_calls: 2};
  assert.equal(hasNewActivity(previous, next), false);
});

test('removing, reordering, or repeating retained events does not trigger activity', () => {
  const previous = run({activity: [event(4), event(5)]});
  assert.equal(hasNewActivity(previous, run({activity: [event(5)]})), false);
  assert.equal(hasNewActivity(previous, run({activity: [event(5), event(4), event(5)]})), false);
});

test('events without sequence IDs cannot trigger an activity pulse', () => {
  const previous = run({activity: [event(4)]});
  const next = run({activity: [event(4), {event: 'assistant', ts: '2026-10-18T12:00:03Z'}]});
  assert.equal(hasNewActivity(previous, next), false);
});
