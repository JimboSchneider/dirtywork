const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

process.env.TZ = 'America/Chicago';
const source = fs.readFileSync(path.join(__dirname, '../dirtywork/dashboard_assets/app.js'), 'utf8');

// Only the DOM boundary is replaced; these tests execute the dashboard's renderer.
class Element {
  constructor(tag, className = '', content = '') {
    this.tag = tag; this.className = className; this.children = []; this.parentNode = null;
    this.attributes = {}; this.dataset = {}; this.style = {}; this.listeners = {};
    this.hidden = false; this.mutations = 0; this._text = content ?? '';
  }
  get textContent() { return String(this._text) + this.children.map(node => node.textContent).join(''); }
  set textContent(value) {
    this._text = value ?? ''; this.children.forEach(node => { node.parentNode = null; });
    this.children = []; this.mutations++;
  }
  get firstChild() { return this.children[0] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    return this.parentNode.children[this.parentNode.children.indexOf(this) + 1] || null;
  }
  append(...nodes) { for (const node of nodes) this.insertBefore(node, null); }
  insertBefore(node, next) {
    if (node.parentNode) node.remove();
    const index = next ? this.children.indexOf(next) : this.children.length;
    assert.ok(index >= 0, 'insertBefore target must belong to its parent');
    this.children.splice(index, 0, node); node.parentNode = this; this.mutations++;
  }
  replaceChildren(...nodes) {
    this.children.forEach(node => { node.parentNode = null; }); this.children = []; this._text = '';
    this.append(...nodes); this.mutations++;
  }
  remove() {
    if (!this.parentNode) return;
    const parent = this.parentNode; parent.children.splice(parent.children.indexOf(this), 1);
    this.parentNode = null; parent.mutations++;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  click() { this.listeners.click?.(); }
  querySelector(selector) {
    return all(this).slice(1).find(node => selector.startsWith('.')
      ? node.className.split(/\s+/).includes(selector.slice(1))
      : selector.startsWith('[data-lane=')
        ? node.dataset.lane === selector.match(/"(.*?)"/)[1]
        : node.tag === selector) || null;
  }
  set innerHTML(_) { throw new Error('Timeline evidence must remain literal text'); }
}
function all(node) { return [node, ...node.children.flatMap(all)]; }
function harness() {
  const start = source.indexOf('  const timelineLanes =');
  const end = source.indexOf('  function diffCode(', start);
  assert.ok(start >= 0 && end > start, 'the real timeline renderer must exist');
  const nodes = Object.fromEntries(['timeline-chart', 'timeline-selection', 'timeline-status',
    'timeline-note', 'review-history', 'review-events'].map(id => [id, new Element('div')]));
  const context = {
    $: id => { assert.ok(nodes[id], `unexpected element ${id}`); return nodes[id]; },
    element: (tag, className, content) => new Element(tag, className, content),
    number: value => new Intl.NumberFormat('en-US', {maximumFractionDigits: 0}).format(value),
  };
  context.set = (id, value) => { context.$(id).textContent = value; };
  vm.createContext(context);
  const durationStart = source.indexOf('  const duration =');
  const durationEnd = source.indexOf('  const words =', durationStart);
  vm.runInContext(source.slice(durationStart, durationEnd) + source.slice(start, end)
    + '\nthis.render = renderTimeline;', context);
  return {nodes, render: context.render};
}
function run(points = [], overrides = {}) {
  return {slug: 'example', activity_state: 'ended', started: '2026-10-18T10:00:00Z',
    timeline: {points, elapsed_s: 120, origin: 'run start', untimed_events: 0, omitted_events: 0},
    ...overrides};
}
function point(sequence, lane, at_s, label) {
  return {sequence, lane, at_s, label, outcome: 'recorded'};
}
function markers(ui) { return all(ui.nodes['timeline-chart']).filter(node => node.tag === 'button'); }
function reviews(ui) { return all(ui.nodes['review-events']).filter(node => node.tag === 'button'); }

test('reviews hours later stay outside the worker axis and expose their actual recorded time', () => {
  const ui = harness();
  ui.render(run([point(1, 'model', 60, 'Model reply'),
    point('review-start', 'review', 7200, 'Review started'),
    point('review', 'review', 7260, 'Review: accept')]));
  assert.equal(markers(ui).length, 1);
  assert.equal(markers(ui)[0].style.left, '50%');
  assert.equal(ui.nodes['timeline-chart'].querySelector('[data-lane="review"]'), null);
  assert.match(ui.nodes['timeline-chart'].querySelector('.timeline-axis').textContent, /2m 0s$/);
  assert.equal(ui.nodes['review-history'].hidden, false);
  assert.equal(reviews(ui).length, 2);
  assert.match(reviews(ui)[0].textContent, /Review started/);
  assert.match(reviews(ui)[0].textContent, /7:00:00/);
  assert.match(reviews(ui)[0].textContent, /2026/);
  reviews(ui)[0].click();
  assert.equal(reviews(ui)[0].getAttribute('aria-pressed'), 'true');
  assert.match(ui.nodes['timeline-selection'].textContent, /Review started.*7:00:00/);
  assert.doesNotMatch(ui.nodes['timeline-selection'].textContent, /120s|2m 0s|worker elapsed/i);
  assert.doesNotMatch(ui.nodes['timeline-note'].textContent, /sit at the end|pinned/i);
});

test('review-only evidence has a history and no worker markers or scaled review axis', () => {
  const ui = harness();
  const data = run([{...point('review', 'review', 86400, 'Review: reject'), outcome: 'failed'}]);
  data.timeline.elapsed_s = null;
  ui.render(data);
  assert.equal(markers(ui).length, 0);
  assert.equal(ui.nodes['timeline-chart'].querySelector('.timeline-axis'), null);
  assert.equal(reviews(ui).length, 1);
  reviews(ui)[0].click();
  assert.match(ui.nodes['timeline-selection'].textContent, /Oct 19, 2026/);
  assert.match(ui.nodes['timeline-selection'].textContent, /failed or blocked/);
});

test('missing timestamp origins retain precise offsets and never invent a wall clock', () => {
  const ui = harness();
  const data = run([point('review', 'review', 7200.25, '<Review: accept>')], {started: null});
  data.timeline.origin = 'first recorded event';
  ui.render(data);
  const review = reviews(ui)[0];
  assert.ok(review, 'recorded review offsets should remain selectable');
  assert.match(review.textContent, /7,200\.25s after first recorded event/);
  assert.doesNotMatch(review.textContent, /2026|Invalid Date|AM|PM/);
  review.click();
  assert.match(ui.nodes['timeline-selection'].textContent, /<Review: accept>.*7,200\.25s/);
});

test('an explicit recorded timestamp takes precedence and is sorted chronologically', () => {
  const ui = harness();
  const late = {...point('review', 'review', 60, 'Review: accept'), ts: '2026-10-19T03:10:00Z'};
  const early = {...point('review-start', 'review', 120, 'Review started'), ts: '2026-10-19T03:00:00Z'};
  const data = run([late, early], {started: null});
  data.timeline.origin = 'first recorded event';
  ui.render(data);
  assert.match(reviews(ui)[0].textContent, /Review started.*10:00:00 PM/);
  assert.match(reviews(ui)[1].textContent, /Review: accept.*10:10:00 PM/);
  assert.match(reviews(ui)[0].textContent, /Oct 18, 2026/);
});

test('empty timing keeps notices and hides review history without an invented review start', () => {
  const ui = harness();
  const data = run([], {review_in_progress: true, review_started_at: null});
  data.timeline.untimed_events = 3; data.timeline.omitted_events = 17;
  ui.render(data);
  assert.equal(ui.nodes['review-history'].hidden, true);
  assert.equal(reviews(ui).length, 0);
  assert.equal(markers(ui).length, 0);
  assert.match(ui.nodes['timeline-note'].textContent, /3 events without usable timestamps/);
  assert.match(ui.nodes['timeline-note'].textContent, /17 older events omitted/);
  assert.match(ui.nodes['timeline-selection'].textContent, /No timestamped events/);
});

test('polling retains focused review controls and updates an existing selection from new evidence', () => {
  const ui = harness();
  const data = run([point(1, 'tools', 60, 'Tool finished'),
    point('review-start', 'review', 7200, 'Review started')]);
  ui.render(data);
  const review = reviews(ui)[0], marker = markers(ui)[0];
  review.click();
  const chartMutations = ui.nodes['timeline-chart'].mutations;
  const historyMutations = ui.nodes['review-events'].mutations;
  const axisMutations = ui.nodes['timeline-chart'].querySelector('.timeline-axis').mutations;
  ui.render(data);
  assert.equal(reviews(ui)[0], review);
  assert.equal(markers(ui)[0], marker);
  assert.equal(ui.nodes['timeline-chart'].mutations, chartMutations);
  assert.equal(ui.nodes['review-events'].mutations, historyMutations);
  assert.equal(ui.nodes['timeline-chart'].querySelector('.timeline-axis').mutations, axisMutations);
  data.timeline.points[1].at_s = 7260;
  ui.render(data);
  assert.equal(reviews(ui)[0], review);
  assert.equal(review.getAttribute('aria-pressed'), 'true');
  assert.match(ui.nodes['timeline-selection'].textContent, /7:01:00/);
  marker.click();
  assert.equal(review.getAttribute('aria-pressed'), 'false');
  assert.equal(marker.getAttribute('aria-pressed'), 'true');
  assert.match(ui.nodes['timeline-selection'].textContent, /60s after run start/);
});

test('removed review evidence clears its selection and switching runs clears old history', () => {
  const ui = harness();
  const data = run([point(1, 'tools', 60, 'Tool finished'),
    point('review', 'review', 7200, 'Review: accept')]);
  ui.render(data); reviews(ui)[0].click();
  data.timeline.points.pop();
  ui.render(data);
  assert.equal(ui.nodes['review-history'].hidden, true);
  assert.equal(reviews(ui).length, 0);
  assert.match(ui.nodes['timeline-selection'].textContent, /outside the retained timeline/);
  ui.render(run([], {slug: 'another'}));
  assert.equal(markers(ui).length, 0);
  assert.equal(reviews(ui).length, 0);
  assert.match(ui.nodes['timeline-selection'].textContent, /No timestamped events/);
});
