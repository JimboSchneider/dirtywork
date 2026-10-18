/* Run evidence is untrusted text: never insert it as HTML. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const root = document.documentElement;
  const state = {snapshot: null, selected: null, detail: null, view: 'activity', limit: 8, query: '', busy: false,
    reviewBusy: false, detailRevision: 0, reviewNotes: new Map(), reviewFeedback: null};
  const knownThemes = ['ion', 'orchid', 'terminal'];
  const appearances = ['system', 'light', 'dark'];
  function stored(key) { try { return localStorage.getItem('dirtywork.' + key); } catch { return null; } }
  function remember(key, value) { try { localStorage.setItem('dirtywork.' + key, value); } catch { /* Private browsing can disable storage. */ } }
  for (const [key, options, fallback] of [['theme', knownThemes, 'ion'], ['appearance', appearances, 'system']]) {
    const saved = stored(key);
    const value = options.includes(saved) ? saved : fallback;
    root.dataset[key] = value;
    $(key).value = value;
    $(key).addEventListener('change', () => {
      root.dataset[key] = $(key).value;
      remember(key, $(key).value);
    });
  }
  // A session-only theme: changing the normal theme selector exits it.
  const secretKeys = ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight'];
  let secretBuffer = [], secretLastKey = 0;
  document.addEventListener('keydown', event => {
    if (event.repeat || event.ctrlKey || event.metaKey || event.altKey ||
        event.target.closest('input, textarea, select, [contenteditable]')) {
      secretBuffer = []; return;
    }
    const now = Date.now();
    if (now - secretLastKey > 3000) secretBuffer = [];
    secretLastKey = now;
    secretBuffer.push(event.key);
    secretBuffer = secretBuffer.slice(-secretKeys.length);
    if (!secretKeys.every((key, index) => secretBuffer[index] === key)) return;
    secretBuffer = [];
    if (!$('theme').querySelector('option[value="wopr"]')) {
      const option = document.createElement('option');
      option.value = 'wopr'; option.textContent = 'WOPR · unlocked';
      $('theme').append(option);
    }
    root.dataset.theme = 'wopr'; $('theme').value = 'wopr';
    let message = $('secret-theme-status');
    if (!message) {
      message = document.createElement('div'); message.id = 'secret-theme-status';
      message.setAttribute('role', 'status'); document.body.append(message);
    }
    message.textContent = 'ACCESS GRANTED // WOPR ONLINE // SHALL WE PLAY A GAME?';
  });
  $('theme').addEventListener('change', () => {
    if ($('theme').value !== 'wopr') {
      $('theme').querySelector('option[value="wopr"]')?.remove();
      $('secret-theme-status')?.remove();
    }
  });
  state.selected = stored('run');
  const number = value => value == null ? '—' : new Intl.NumberFormat(undefined, {maximumFractionDigits: 0}).format(value);
  const compactNumber = value => value == null ? '—' : new Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1}).format(value);
  const duration = value => {
    if (value == null) return '—';
    const seconds = Math.floor(value);
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
  };
  const words = value => String(value || 'Unknown').replaceAll('_', ' ');
  function label(run) {
    if (run.verdict === 'accept') return 'Accepted';
    if (run.verdict === 'reject') return 'Rejected';
    if (run.verdict === 'cleanup') return 'Cleanup';
    if (run.resumed_by) return `Superseded by ${run.resumed_by}`;
    if (run.review_in_progress) return 'In review';
    if (run.activity_state === 'inactive') return 'Inactive · process stopped';
    if (run.activity_state === 'unconfirmed') return 'Running · unconfirmed';
    return words(run.status);
  }
  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content != null) node.textContent = content;
    return node;
  }
  function set(id, value) { $(id).textContent = value; }
  const explanations = {
    offloaded: ['Tokens offloaded', 'Recorded worker tokens measure offloaded work, rather than exact token savings.'],
    'accepted-tokens': ['Accepted work', 'Worker tokens used on runs that a reviewer accepted.'],
    coverage: ['Runs with usage', 'Runs with both input and output token counts, out of all saved runs.'],
    'worker-time': ['Worker time', 'Time spent on the selected run, still counting while its worker is active.'],
    turns: ['Turns and tool results', 'The number of model replies and tool results recorded for this run.'],
    verdict: ['Review verdict', 'The recorded review decision, which is separate from the worker finishing.'],
    'review-time': ['Review time', 'Time from starting review to recording a decision, including any checks and waiting.'],
    'review-actions': ['Review actions', 'Start the timer, then save your decision and an optional note after checking the result.'],
    'selected-run': ['Selected run', 'The task, model, and results for the run you selected.'],
    verification: ['Verification', 'Whether the run’s configured check passed, if a result was recorded.'],
    tokens: ['Input and output tokens', 'Tokens are pieces of text: input goes to the worker and output comes back.'],
    files: ['Changed files', 'How many files the selected run changed, when that information is available.'],
    timeline: ['Run timeline', 'When model replies, tools, checks, and reviews were recorded during this run.'],
    stream: ['Activity stream', 'The latest recorded actions and results from the selected run, updated as they arrive.'],
    runs: ['Runs', 'Search your saved runs and select one to inspect its task and results.'],
    acceptance: ['Accepted and reviewed runs', 'Runs accepted by review, out of runs with an accept or reject decision.'],
    'median-worker': ['Median worker time', 'The middle worker duration across ended runs with timing data, including failed runs.'],
    'median-review': ['Median review time', 'The middle recorded review duration for accepted or rejected runs that include review time.'],
    funnel: ['From recorded to accepted', 'Counts of saved runs, completed work, review decisions, and accepted results.'],
    models: ['Worker comparison', 'Recorded outcomes and worker times grouped by model, across different tasks.'],
  };
  const help = element('div', 'section-help');
  help.id = 'section-help';
  help.setAttribute('role', 'tooltip');
  help.hidden = true;
  document.body.append(help);
  let helpButton = null;
  let helpPinned = false;
  let helpTimer;
  function positionHelp() {
    if (!helpButton || help.hidden) return;
    const rect = helpButton.getBoundingClientRect();
    const width = help.offsetWidth;
    const height = help.offsetHeight;
    help.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - width - 12))}px`;
    help.style.top = `${Math.max(12, rect.bottom + height + 20 <= innerHeight ? rect.bottom + 8 : rect.top - height - 8)}px`;
  }
  function closeHelp() {
    clearTimeout(helpTimer);
    if (helpButton) {
      helpButton.setAttribute('aria-expanded', 'false');
      helpButton.removeAttribute('aria-describedby');
    }
    help.hidden = true;
    helpButton = null;
    helpPinned = false;
  }
  function showHelp(button) {
    clearTimeout(helpTimer);
    if (helpButton !== button) closeHelp();
    helpButton = button;
    help.textContent = explanations[button.dataset.helpKey][1];
    help.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    button.setAttribute('aria-describedby', help.id);
    positionHelp();
  }
  function leaveHelp() {
    clearTimeout(helpTimer);
    if (!helpPinned && document.activeElement !== helpButton) helpTimer = setTimeout(closeHelp, 160);
  }
  for (const label of document.querySelectorAll('[data-help]')) {
    const key = label.dataset.help;
    if (/^H[1-6]$/.test(label.tagName)) label.setAttribute('aria-label', label.textContent.trim());
    const button = element('button', 'section-info');
    button.type = 'button';
    button.dataset.helpKey = key;
    button.setAttribute('aria-label', `About ${explanations[key][0]}`);
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', help.id);
    const icon = element('span', '', 'i');
    icon.setAttribute('aria-hidden', 'true');
    button.append(icon);
    label.append(button);
    button.addEventListener('mouseenter', () => showHelp(button));
    button.addEventListener('mouseleave', leaveHelp);
    button.addEventListener('focus', () => showHelp(button));
    button.addEventListener('blur', closeHelp);
    button.addEventListener('click', () => {
      if (helpButton === button && helpPinned) closeHelp();
      else { showHelp(button); helpPinned = true; }
    });
  }
  help.addEventListener('mouseenter', () => clearTimeout(helpTimer));
  help.addEventListener('mouseleave', leaveHelp);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeHelp(); });
  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('.section-info') && !help.contains(event.target)) closeHelp();
  });
  document.addEventListener('scroll', closeHelp, true);
  window.addEventListener('resize', closeHelp);
  function tab(name, focus = false) {
    closeHelp();
    state.view = name;
    for (const key of ['activity', 'outcomes']) {
      const active = key === name;
      $(key + '-view').hidden = !active;
      $(key + '-tab').setAttribute('aria-selected', String(active));
      $(key + '-tab').tabIndex = active ? 0 : -1;
      if (active && focus) $(key + '-tab').focus();
    }
    remember('view', name);
  }
  for (const name of ['activity', 'outcomes']) {
    $(name + '-tab').addEventListener('click', () => tab(name));
    $(name + '-tab').addEventListener('keydown', event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        tab(event.key === 'Home' ? 'activity' : event.key === 'End' ? 'outcomes' : state.view === 'activity' ? 'outcomes' : 'activity', true);
      }
    });
  }
  tab(stored('view') === 'outcomes' ? 'outcomes' : 'activity');
  async function api(path) {
    const response = await fetch(path, {cache: 'no-store', signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error(`Run records request failed (${response.status}).`);
    return response.json();
  }
  function renderReviewControls(run) {
    const settled = Boolean(run.verdict);
    const eligible = !settled && run.review_eligible;
    $('start-review').disabled = state.reviewBusy || !eligible || run.review_in_progress;
    $('start-review').textContent = run.review_in_progress ? 'Review started' : 'Start Review';
    $('accept-review').disabled = state.reviewBusy || !eligible || !run.review_in_progress;
    $('reject-review').disabled = state.reviewBusy || !eligible || !run.review_in_progress;
    $('review-note').disabled = state.reviewBusy || !eligible;
    if (settled) {
      $('review-note').value = run.note || '';
      $('review-note').dataset.slug = run.slug;
    } else if ($('review-note').dataset.slug !== run.slug) {
      $('review-note').value = state.reviewNotes.get(run.slug) || '';
      $('review-note').dataset.slug = run.slug;
    }
    set('review-controls-state', settled ? 'Review saved' : run.resumed_by ? `Superseded by ${run.resumed_by}` : !eligible ? 'Wait for the worker to finish' : run.review_in_progress ? 'Timer running' : 'Start review to begin timing');
    const feedback = state.reviewFeedback?.slug === run.slug ? state.reviewFeedback : null;
    const message = feedback?.message || '';
    if ($('review-feedback').textContent !== message) set('review-feedback', message);
    $('review-feedback').classList.toggle('review-error', Boolean(feedback?.error));
  }
  $('review-note').addEventListener('input', () => {
    if (state.selected) state.reviewNotes.set(state.selected, $('review-note').value);
  });
  async function performReview(action) {
    if (state.reviewBusy || !state.detail || state.detail.slug !== state.selected) return;
    const slug = state.selected;
    const token = state.snapshot?.csrf_token;
    if (!token) return;
    state.reviewBusy = true;
    state.detailRevision++;
    state.reviewFeedback = {slug, message: 'Saving…', error: false};
    renderReviewControls(state.detail);
    try {
      const response = await fetch('/api/runs/' + encodeURIComponent(slug) + '/review', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Dirtywork-CSRF': token},
        body: JSON.stringify({action, note: state.reviewNotes.get(slug) || ''}),
        signal: AbortSignal.timeout(15000),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not save the review.');
      state.reviewFeedback = {slug, message: action === 'start' ? 'Review started. The timer is running.' : `${action === 'accept' ? 'Accepted' : 'Rejected'}. Decision and review time saved.`, error: false};
      if (state.selected === slug) { state.detail = payload.run; renderDetail(payload.run); }
    } catch (error) {
      state.reviewFeedback = {slug, message: error.message + ' Refresh to check the saved state before retrying.', error: true};
    } finally {
      state.reviewBusy = false;
      if (state.detail?.slug === state.selected) renderReviewControls(state.detail);
      refresh();
    }
  }
  $('start-review').addEventListener('click', () => performReview('start'));
  $('accept-review').addEventListener('click', () => performReview('accept'));
  $('reject-review').addEventListener('click', () => performReview('reject'));
  function selectRun(slug) {
    if (state.selected === slug) return;
    state.selected = slug;
    state.detail = null;
    state.detailRevision++;
    for (const id of ['start-review', 'accept-review', 'reject-review', 'review-note']) $(id).disabled = true;
    remember('run', slug);
    renderList();
    set('run-task', 'Loading selected run…');
    loadDetail();
  }
  function renderList() {
    const allRuns = state.snapshot.runs;
    const runs = allRuns.filter(run => [run.task, run.slug, run.model, run.repo, run.status, run.verdict].join(' ').toLowerCase().includes(state.query));
    set('run-count', `${number(runs.length)}${state.query ? ' matches' : ' records'}`);
    const list = $('run-list');
    const desired = runs.slice(0, state.limit);
    if (state.selected && !desired.some(run => run.slug === state.selected)) {
      const selected = runs.find(run => run.slug === state.selected);
      if (selected) desired.unshift(selected);
    }
    const keep = new Set(desired.map(run => run.slug));
    for (const child of [...list.children]) if (!keep.has(child.dataset.slug)) child.remove();
    let previous = null;
    for (const run of desired) {
      let button = [...list.children].find(child => child.dataset.slug === run.slug);
      if (!button) {
        button = element('button', 'run-choice');
        button.type = 'button';
        button.dataset.slug = run.slug;
        button.append(element('span', 'run-choice-title'), element('span', 'run-choice-meta'), element('span', 'run-choice-model'), element('span', 'run-choice-slug'));
        button.addEventListener('click', () => selectRun(run.slug));
      }
      button.setAttribute('aria-pressed', String(run.slug === state.selected));
      button.children[0].textContent = (run.task || run.slug).slice(0, 110);
      button.children[1].textContent = `${label(run)} · ${duration(run.duration_s)}`;
      button.children[2].textContent = run.model || 'Unknown model';
      button.children[3].textContent = run.slug;
      const next = previous ? previous.nextSibling : list.firstChild;
      if (next !== button) list.insertBefore(button, next);
      previous = button;
    }
    $('more-runs').hidden = runs.length <= state.limit;
    $('no-matches').hidden = runs.length > 0;
  }
  $('run-search').addEventListener('input', () => {
    state.query = $('run-search').value.trim().toLowerCase();
    state.limit = 8;
    if (state.snapshot) renderList();
  });
  $('more-runs').addEventListener('click', () => { state.limit += 20; renderList(); });
  const timelineLanes = [['lifecycle', 'Run'], ['model', 'Model'], ['tools', 'Tools'], ['harness', 'Harness'], ['verify', 'Verify'], ['review', 'Review']];
  const timelineNodes = new Map();
  let timelineSlug = null;
  let timelineSelection = null;
  function renderTimeline(run) {
    const chart = $('timeline-chart');
    if (timelineSlug !== run.slug) {
      timelineSlug = run.slug;
      timelineSelection = null;
      timelineNodes.clear();
      chart.replaceChildren();
      set('timeline-selection', 'Select a marker for its recorded time.');
    }
    const timeline = run.timeline;
    if (!timeline || !timeline.points.length) {
      chart.replaceChildren(element('p', 'timeline-empty', 'No timestamped events recorded yet.'));
      timelineNodes.clear();
      timelineSelection = null;
      if ($('timeline-selection').textContent !== 'No timestamped events to select.') set('timeline-selection', 'No timestamped events to select.');
    } else {
      if (!chart.querySelector('.timeline-axis')) {
        chart.replaceChildren(element('div', 'timeline-axis'));
        set('timeline-selection', 'Select a marker for its recorded time.');
        for (const [lane, label] of timelineLanes) {
          const row = element('div', 'timeline-lane');
          const track = element('div', 'timeline-track');
          track.dataset.lane = lane;
          row.append(element('span', 'timeline-lane-label', label), track);
          chart.append(row);
        }
      }
      const extent = Math.max(timeline.elapsed_s || 0, 1);
      const axis = chart.querySelector('.timeline-axis');
      axis.replaceChildren(...[0, 0.5, 1].map(fraction => element('span', '', duration(extent * fraction))));
      const wanted = new Set(timeline.points.map(point => String(point.sequence)));
      for (const [key, button] of timelineNodes) if (!wanted.has(key)) { button.remove(); timelineNodes.delete(key); }
      for (const point of timeline.points) {
        const key = String(point.sequence);
        let marker = timelineNodes.get(key);
        if (!marker) {
          marker = element('button', 'timeline-marker');
          marker.type = 'button';
          marker.dataset.lane = point.lane;
          marker.addEventListener('click', () => {
            timelineSelection = key;
            for (const [id, node] of timelineNodes) node.setAttribute('aria-pressed', String(id === key));
            set('timeline-selection', marker.getAttribute('aria-label'));
          });
          chart.querySelector(`[data-lane="${point.lane}"]`).append(marker);
          timelineNodes.set(key, marker);
        }
        marker.style.left = `${Math.min(100, point.at_s / extent * 100)}%`;
        marker.dataset.outcome = point.outcome;
        const seconds = point.at_s.toLocaleString(undefined, {maximumFractionDigits: 2});
        marker.setAttribute('aria-label', `${point.label} · ${seconds}s after ${timeline.origin}${point.outcome === 'failed' ? ' · failed or blocked' : ''}`);
        marker.setAttribute('aria-pressed', String(key === timelineSelection));
      }
      if (timelineSelection && !wanted.has(timelineSelection)) {
        timelineSelection = null;
        set('timeline-selection', 'Selected event is outside the retained timeline.');
      } else if (timelineSelection) {
        const selectedLabel = timelineNodes.get(timelineSelection).getAttribute('aria-label');
        if ($('timeline-selection').textContent !== selectedLabel) set('timeline-selection', selectedLabel);
      }
    }
    set('timeline-status', run.review_in_progress ? 'Live review · 3s refresh' : run.activity_state === 'running' ? 'Live · 3s refresh' : 'Recorded events');
    const notices = ['Event timestamps · gaps are unclassified time.'];
    if (timeline?.points.some(point => point.lane === 'review')) notices.push('Review markers sit at the end of the worker axis; select one for its actual time.');
    if (timeline?.untimed_events) notices.push(`${number(timeline.untimed_events)} events without usable timestamps.`);
    if (timeline?.omitted_events) notices.push(`Latest 2,000 events shown; ${number(timeline.omitted_events)} older events omitted.`);
    set('timeline-note', notices.join(' '));
  }
  async function loadChanges(slug) {
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(slug)}/changes`);
      if (!response.ok) throw new Error('Changes unavailable');
      const data = await response.json();
      if (state.selected !== slug) return;
      set('files-status', data.message);
      const output = $('files-output');
      const signature = JSON.stringify([slug, data.files]);
      if (output.dataset.signature === signature) return;
      const expanded = new Set([...output.querySelectorAll('details[open]')].map(node => node.dataset.path));
      output.replaceChildren(...data.files.map(file => {
        const row = element('details', 'file-diff');
        row.dataset.path = file.path;
        row.open = data.files.length === 1 || expanded.has(file.path);
        const heading = element('summary');
        heading.append(element('span', 'diff-filename', file.path), element('span', 'diff-type', file.change_type),
          element('span', 'diff-count-add', `+${file.additions}`), element('span', 'diff-count-remove', `−${file.deletions}`));
        const code = element('div', 'diff-code');
        code.setAttribute('role', 'table');
        code.setAttribute('aria-label', `Changes to ${file.path}`);
        for (const line of file.rows) {
          const kind = {add: 'diff-add', remove: 'diff-remove', hunk: 'diff-hunk', notice: 'diff-notice', context: 'diff-context'}[line.kind];
          const entry = element('div', 'diff-line ' + kind);
          entry.setAttribute('role', 'row');
          const old = element('span', 'diff-number', line.old ?? '');
          const next = element('span', 'diff-number', line.new ?? '');
          old.setAttribute('role', 'cell'); next.setAttribute('role', 'cell');
          const sign = element('span', 'diff-sign', {add: '+', remove: '−', hunk: '⋯'}[line.kind] || '');
          sign.setAttribute('role', 'cell');
          const content = element('code', 'diff-text', line.kind === 'hunk' ? (line.text || 'Changed lines') : line.text);
          content.setAttribute('role', 'cell');
          entry.append(old, next, sign, content); code.append(entry);
        }
        if (!file.rows.length) code.append(element('p', 'diff-empty', 'File metadata or binary contents changed; no text lines to display.'));
        row.append(heading, code); return row;
      }));
      output.dataset.signature = signature;
    } catch (_) {
      if (state.selected === slug) set('files-status', 'Could not load changes. Retrying on the next refresh.');
    }
  }
  $('files-details').addEventListener('toggle', () => {
    if ($('files-details').open && state.selected) loadChanges(state.selected);
  });

  function renderDetail(run) {
    renderReviewControls(run);
    renderTimeline(run);
    set('worker-time', duration(run.duration_s));
    set('worker-time-note', run.activity_state === 'running' ? 'Elapsed · live process detected' : run.duration_s == null ? 'Duration unavailable' : 'Recorded worker duration');
    set('turns', `${number(run.turns)} / ${number(run.tool_calls)}`);
    set('verdict', {accept: 'Accepted', reject: 'Rejected', cleanup: 'Cleanup'}[run.verdict] || (run.resumed_by ? 'Superseded' : run.review_in_progress ? 'Reviewing' : 'Pending'));
    set('verdict-note', run.review_in_progress ? `${duration(run.review_elapsed_s)} review in progress` : run.review_seconds != null ? `${duration(run.review_seconds)} recorded review time` : run.verdict ? 'Review time not recorded' : run.activity_state === 'running' ? 'Worker is still running' : 'No verdict recorded');
    set('review-time', duration(run.review_in_progress ? run.review_elapsed_s : run.review_seconds));
    set('review-time-state', run.review_in_progress ? 'Timer running' : run.review_seconds != null ? 'Recorded duration' : 'Not tracked');
    set('run-slug', run.slug);
    set('run-task', (run.task || 'Task not recorded').slice(0, 240) + (run.task.length > 240 ? '…' : ''));
    set('run-model', [run.model || 'Unknown model', run.provider, run.sandbox && `${run.sandbox} sandbox`].filter(Boolean).join(' · '));
    set('run-status', label(run));
    $('run-status').classList.toggle('warning', run.verdict === 'reject' || ['inactive', 'unconfirmed', 'unknown'].includes(run.activity_state) || !['running', 'completed'].includes(run.status));
    const verification = run.verify?.passed === true ? 'Passed' : run.verify?.passed === false ? 'Failed' : 'Not recorded';
    set('verification', verification);
    set('tokens', `${number(run.usage?.prompt_tokens)} / ${number(run.usage?.completion_tokens)}`);
    set('file-count', run.files_changed == null ? '—' : `${number(run.files_changed.length)}${run.files_changed_truncated ? '+' : ''}`);
    for (const [id, recorded] of [['start', Boolean(run.started)], ['work', (run.turns || 0) > 0], ['verify', Boolean(run.verify)], ['review', Boolean(run.verdict) || run.review_in_progress]]) {
      $('stage-' + id).classList.toggle('recorded', recorded);
    }
    $('stage-verify').classList.toggle('failed', run.verify?.passed === false);
    $('task-details').hidden = run.task.length <= 100;
    set('full-task', run.task);
    $('verify-details').hidden = !run.verify;
    set('verify-output', run.verify ? [run.verify.command, `Result: ${verification} · exit ${run.verify.exit_code ?? 'unknown'}`, run.verify.output_tail].filter(Boolean).join('\n') : '');
    if ($('files-output').dataset.slug !== run.slug) {
      $('files-output').replaceChildren();
      $('files-output').dataset.slug = run.slug;
      $('files-output').dataset.signature = '';
      set('files-status', 'Expand to load saved changes.');
    }
    if ($('files-details').open) loadChanges(run.slug);
    set('lineage', [run.repo, run.branch, run.resumed_from && `Resumed from ${run.resumed_from}`, run.resumed_by && `Resumed by ${run.resumed_by}`, run.note && `Review note: ${run.note}`].filter(Boolean).join(' · '));
    $('run-warnings').hidden = !run.warnings.length;
    set('run-warnings', run.warnings.join(' '));
    const signature = JSON.stringify(run.activity);
    if ($('activity-log').dataset.signature !== signature) {
      const focused = document.activeElement?.closest('#activity-log details');
      const focusedKey = focused?.dataset.key;
      const expanded = new Set([...$('activity-log').querySelectorAll('details[open]')].map(node => node.dataset.key));
      const rows = run.activity.map(event => {
        const row = element('div', 'log-row');
        const head = element('div', 'log-summary');
        const elapsed = event.ts && run.started ? (Date.parse(event.ts) - Date.parse(run.started)) / 1000 : null;
        head.append(element('span', 'log-time', elapsed != null && Number.isFinite(elapsed) ? duration(Math.max(0, elapsed)) : '—'), element('span', 'log-event', event.tool || words(event.event)), element('span', 'log-target', event.target || (event.event === 'tool_result' ? '' : event.detail)));
        row.append(head);
        if (event.detail && event.event === 'tool_result') {
          const details = element('details');
          details.dataset.key = `${run.slug}:${event.sequence}`;
          details.open = expanded.has(details.dataset.key);
          details.append(element('summary', '', 'Result preview'), element('pre', '', event.detail));
          row.append(details);
        }
        return row;
      });
      $('activity-log').replaceChildren(...(rows.length ? rows : [element('p', 'secondary', 'No flushed transcript events yet.')]));
      $('activity-log').dataset.signature = signature;
      if (focusedKey) [...$('activity-log').querySelectorAll('details')].find(node => node.dataset.key === focusedKey)?.querySelector('summary').focus({preventScroll: true});
    }
  }
  function renderOutcomes() {
    const summary = state.snapshot.summary;
    set('offloaded-tokens', compactNumber(summary.offloaded_tokens));
    $('offloaded-tokens').setAttribute('aria-label', `${number(summary.offloaded_tokens)} recorded worker tokens`);
    $('offloaded-tokens').title = `${number(summary.offloaded_tokens)} recorded worker tokens`;
    set('accepted-tokens', compactNumber(summary.accepted_offloaded_tokens));
    $('accepted-tokens').setAttribute('aria-label', `${number(summary.accepted_offloaded_tokens)} recorded worker tokens on accepted runs`);
    $('accepted-tokens').title = `${number(summary.accepted_offloaded_tokens)} recorded worker tokens on accepted runs`;
    set('accepted-tokens-note', `${number(summary.accepted_token_usage_samples)} of ${number(summary.accepted)} accepted runs with usage`);
    set('token-coverage', `${number(summary.token_usage_samples)} / ${number(summary.total)}`);
    set('token-breakdown', `${compactNumber(summary.offloaded_prompt_tokens)} input + ${compactNumber(summary.offloaded_completion_tokens)} output`);
    set('accepted', `${number(summary.accepted)} / ${number(summary.reviewed)}`);
    set('accepted-note', summary.acceptance_rate == null ? 'No accept/reject verdicts recorded' : `${Math.round(summary.acceptance_rate * 100)}% acceptance · recorded verdicts`);
    set('median-worker', duration(summary.median_duration_s));
    set('median-worker-note', `${number(summary.duration_samples)} ended runs with duration`);
    set('median-review', duration(summary.median_review_s));
    set('median-review-note', `${number(summary.review_samples)} accept/reject runs with review time`);
    $('funnel').replaceChildren(...[['Recorded', summary.total], ['Completed', summary.completed], ['Reviewed', summary.reviewed], ['Accepted', summary.accepted]].map(([name, value]) => {
      const row = element('div', 'funnel-row');
      const track = element('div', 'funnel-track');
      const fill = element('div', 'funnel-fill');
      fill.style.width = `${summary.total ? value / summary.total * 100 : 0}%`;
      track.append(fill);
      row.append(element('span', '', name), track, element('strong', '', number(value)));
      return row;
    }));
    set('outcome-note', `${number(summary.awaiting_review)} ended runs without a verdict · ${number(summary.cleanup)} cleanup verdicts · ${number(summary.rejected)} rejected. Reviewed runs can include failed worker runs.`);
    $('models').replaceChildren(...state.snapshot.models.map(model => {
      const row = element('tr');
      row.append(element('td', '', model.model));
      const accepted = element('td', '', `${number(model.accepted)} / ${number(model.reviewed)}`);
      accepted.append(element('small', '', `${number(model.total)} total runs`));
      const time = element('td', '', duration(model.median_duration_s));
      time.append(element('small', '', `${number(model.duration_samples)} duration samples`));
      row.append(accepted, time);
      return row;
    }));
  }
  async function loadDetail() {
    if (state.reviewBusy) return;
    const slug = state.selected;
    const revision = state.detailRevision;
    if (!slug) return;
    try {
      const run = await api('/api/runs/' + encodeURIComponent(slug));
      if (state.selected !== slug || state.detailRevision !== revision || state.reviewBusy) return;
      state.detail = run;
      renderDetail(run);
    } catch (error) {
      if (state.selected === slug) {
        set('error', error.message + ' Keeping the last available values; retrying automatically.');
        $('error').hidden = false;
      }
    }
  }
  async function refresh() {
    if (state.busy || document.hidden) return;
    state.busy = true;
    try {
      const snapshot = await api('/api/runs');
      state.snapshot = snapshot;
      $('error').hidden = true;
      $('connection-dot').classList.remove('offline');
      set('connection', `${number(snapshot.summary.active)} active · ${snapshot.summary.reviewing ? number(snapshot.summary.reviewing) + ' in review · ' : ''}${number(snapshot.summary.total)} recorded · updated ${new Date(snapshot.updated_at).toLocaleTimeString()}`);
      set('source', snapshot.runs_dir);
      $('empty').hidden = snapshot.runs.length > 0;
      $('run-panel').hidden = !snapshot.runs.length;
      if (!snapshot.runs.some(run => run.slug === state.selected)) {
        state.selected = snapshot.runs.find(run => run.activity_state === 'running')?.slug || snapshot.runs[0]?.slug || null;
        state.detail = null;
      }
      if (!state.selected) {
        for (const [id, value] of [['worker-time', '—'], ['turns', '— / —'], ['verdict', '—'], ['worker-time-note', 'No run selected'], ['verdict-note', 'No verdict recorded']]) set(id, value);
      }
      renderList();
      renderOutcomes();
      $('data-warnings').hidden = !snapshot.warnings.length;
      set('warning-summary', `${number(snapshot.warnings.length)} run data notices`);
      $('warnings-list').replaceChildren(...snapshot.warnings.map(warning => element('li', '', warning)));
      await loadDetail();
    } catch (error) {
      $('connection-dot').classList.add('offline');
      set('connection', 'Disconnected · last available data shown');
      set('error', error.message + ' Check that dirtywork dashboard is still running. Retrying automatically.');
      $('error').hidden = false;
    } finally { state.busy = false; }
  }
  refresh();
  setInterval(refresh, 3000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
