const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

// Exercise the real shared renderer without booting the polling dashboard.
const source = fs.readFileSync(path.join(__dirname, '../dirtywork/dashboard_assets/app.js'), 'utf8');
class Element {
  constructor(tag, className, content) {
    this.tag = tag; this.className = className || ''; this.textContent = content ?? '';
    this.children = []; this.attributes = {};
  }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(name, value) { this.attributes[name] = value; }
  set innerHTML(_) { throw new Error('Run evidence must never be interpreted as HTML'); }
}
function renderers() {
  const start = source.indexOf('  function diffCode(');
  assert.notEqual(start, -1, 'the shared diff renderer must exist');
  const end = source.indexOf('  let changesRequest', start);
  const context = {element: (tag, className, content) => new Element(tag, className, content)};
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nthis.render = {diffCode, renderResultPreview};', context);
  return context.render;
}
const rows = [
  {kind: 'hunk', text: '', old: null, new: null},
  {kind: 'context', text: 'unchanged', old: 10, new: 10},
  {kind: 'remove', text: 'before', old: 11, new: null},
  {kind: 'add', text: '<script>after</script>', old: null, new: 11},
  {kind: 'notice', text: 'No newline at end of file', old: null, new: null},
];
function all(node) { return [node, ...node.children.flatMap(all)]; }
function preview(truncated = false) {
  return {event: 'tool_result', tool: 'edit_file', target: 'demo.txt', detail: 'raw patch metadata',
    change_preview: {summary: '1 line added · 1 line removed', additions: 1, deletions: 1, rows, truncated}};
}

test('shared diff renderer retains line numbers, change colors, and literal file text', () => {
  const code = renderers().diffCode({path: 'demo.txt', rows});
  assert.equal(code.attributes.role, 'table');
  assert.equal(code.attributes['aria-label'], 'Changes to demo.txt');
  assert.equal(code.children[0].children[3].textContent, 'Changed lines');
  const removed = code.children[2], added = code.children[3];
  assert.match(removed.className, /diff-remove/);
  assert.deepEqual(removed.children.map(node => node.textContent), [11, '', '−', 'before']);
  assert.match(added.className, /diff-add/);
  assert.deepEqual(added.children.map(node => node.textContent), ['', 11, '+', '<script>after</script>']);
  assert.match(code.children[4].className, /diff-notice/);
});

test('activity edits show a readable summary and the shared diff without raw patch headers', () => {
  const result = renderers().renderResultPreview(preview());
  const nodes = all(result);
  assert.equal(result.tag, 'details');
  assert.equal(result.children[0].tag, 'summary');
  assert.ok(nodes.some(node => node.textContent === 'View edit'));
  assert.ok(nodes.some(node => node.textContent === '+1'));
  assert.ok(nodes.some(node => node.textContent === '−1'));
  assert.ok(nodes.some(node => node.textContent === '1 line added · 1 line removed'));
  assert.ok(nodes.some(node => node.className === 'diff-code'));
  assert.ok(nodes.some(node => /This edit only/.test(node.textContent)));
  assert.ok(!nodes.some(node => node.tag === 'pre' || node.textContent === 'raw patch metadata'));
});

test('partial activity edits clearly point to the full run diff', () => {
  const nodes = all(renderers().renderResultPreview(preview(true)));
  assert.ok(nodes.some(node => /Partial preview/.test(node.textContent) && /File changes/.test(node.textContent)));
});

test('ordinary tool results retain their literal text output', () => {
  const detail = '--- a/file\n+++ b/file\nThis is a file read, not an edit. <img src=x>';
  const nodes = all(renderers().renderResultPreview({tool: 'read_file', detail}));
  assert.ok(nodes.some(node => node.textContent === 'View output'));
  assert.ok(nodes.some(node => node.tag === 'pre' && node.textContent === detail));
  assert.ok(!nodes.some(node => node.className === 'diff-code'));
});

test('the shared renderer preserves the empty or binary file explanation', () => {
  const code = renderers().diffCode({path: 'image.png', rows: []});
  assert.match(code.children[0].textContent, /metadata or binary/);
});
