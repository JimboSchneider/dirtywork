/* Run evidence is untrusted text: never insert it as HTML. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const root = document.documentElement;
  const state = {allSnapshot: null, snapshot: null, repository: null, selected: null, detail: null, view: 'activity', limit: 8, query: '', busy: false,
    reviewBusy: false, detailRevision: 0, reviewNotes: new Map(), reviewFeedback: null,
    taskTypeBusy: null, taskTypeFeedback: null};
  const knownThemes = ['ion', 'orchid', 'terminal'];
  const appearances = ['system', 'light', 'dark'];
  const wopr = window.DirtyworkWopr;
  const presentation = window.DirtyworkPresentation;
  let lastWoprRun = null, woprPulseTimer, disconnectTimer, syncIndicatorTimer;
  function renderWoprRun(run) {
    const active = root.dataset.theme === 'wopr';
    const stamp = $('wopr-run-status');
    const text = active ? wopr.statusStamp(run) : '';
    if (stamp.textContent !== text) stamp.textContent = text;
    if (active && wopr.hasNewActivity(lastWoprRun, run) &&
        !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const panel = $('run-panel');
      clearTimeout(woprPulseTimer);
      panel.classList.remove('wopr-activity-pulse');
      // Restart the single pulse even if new events arrive during its fade.
      void panel.offsetWidth;
      panel.classList.add('wopr-activity-pulse');
      woprPulseTimer = setTimeout(() => panel.classList.remove('wopr-activity-pulse'), 800);
    }
    lastWoprRun = run;
  }
  function disconnectWopr() {
    clearTimeout(disconnectTimer);
    $('wopr-disconnect')?.remove();
    const message = element('div', '', '[ CONNECTION TERMINATED ]');
    message.id = 'wopr-disconnect'; message.setAttribute('role', 'status');
    document.body.append(message);
    disconnectTimer = setTimeout(() => message.remove(), 1500);
    clearTimeout(woprPulseTimer);
    $('run-panel').classList.remove('wopr-activity-pulse');
    renderWoprRun(state.detail);
  }
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
  let secretBuffer = [], secretLastKey = 0, secretAlertTimer;
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
    clearTimeout(disconnectTimer); $('wopr-disconnect')?.remove();
    renderWoprRun(state.detail);
    clearTimeout(secretAlertTimer);
    $('secret-theme-status')?.remove();
    const message = document.createElement('div'); message.id = 'secret-theme-status';
    message.setAttribute('role', 'status');
    const panel = document.createElement('div'); panel.className = 'access-panel';
    panel.append(element('span', 'access-code', '[ AUTHENTICATION COMPLETE ]'),
      element('strong', 'access-title', 'ACCESS GRANTED'),
      element('span', 'access-subtitle', 'WOPR ONLINE // SHALL WE PLAY A GAME?'),
      element('span', 'access-countdown'));
    message.append(panel); document.body.append(message);
    secretAlertTimer = setTimeout(() => message.remove(), 3000);
  });
  $('theme').addEventListener('change', () => {
    if ($('theme').value !== 'wopr') {
      const unlocked = $('theme').querySelector('option[value="wopr"]');
      if (unlocked) disconnectWopr();
      unlocked?.remove();
      clearTimeout(secretAlertTimer);
      $('secret-theme-status')?.remove();
    }
  });
  state.selected = stored('run');
  const savedRepository = stored('repository');
  state.repository = savedRepository?.startsWith('repo:') ? savedRepository.slice(5) : null;
  state.runScope = ['all', 'active', 'review'].includes(stored('runScope')) ? stored('runScope') : 'all';
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
    'repository-scope': ['Repository scope', 'Choose a recorded repository to scope runs, token totals, and Outcomes together; search and run filters only narrow the run list.'],
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
    timeline: ['Run timeline', 'Worker events share a time scale; review history is shown separately with its recorded times.'],
    stream: ['Activity stream', 'The latest recorded actions and results from the selected run, updated as they arrive.'],
    runs: ['Runs', 'Search your saved runs and select one to inspect its task and results.'],
    acceptance: ['Accepted and reviewed runs', 'Runs accepted by review, out of runs with an accept or reject decision.'],
    'median-worker': ['Median worker time', 'The middle worker duration across ended runs with timing data, including failed runs.'],
    'median-review': ['Median review time', 'The middle recorded review duration for accepted or rejected runs that include review time.'],
    funnel: ['From recorded to accepted', 'Counts of saved runs, completed work, review decisions, and accepted results.'],
    models: ['Worker comparison', 'Recorded outcomes and worker times grouped by model, across different tasks.'],
    'task-type': ['Task type', 'A suggested category based on the full task description; choose another type to correct it, or return to Auto.'],
    'offload-fit': ['Offloading fit', 'Acceptance and measured review effort by task type, with sample counts so repeated or sparsely timed work stays visible.'],
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
  function renderTaskType(run) {
    const select = $('task-type');
    const options = state.snapshot?.task_type_options || [];
    const signature = JSON.stringify(options);
    if (select.dataset.signature !== signature) {
      const automatic = element('option', '', 'Use automatic suggestion');
      automatic.value = '';
      select.replaceChildren(automatic, ...options.map(type => {
        const option = element('option', '', type.label); option.value = type.id; return option;
      }));
      select.dataset.signature = signature;
    }
    const manual = run.task_type?.source === 'manual';
    select.children[0].textContent = manual ? 'Use automatic suggestion' : `Auto · ${run.task_type?.label || 'Other / unclear'}`;
    if (state.taskTypeBusy !== run.slug) select.value = manual ? run.task_type.id : '';
    select.disabled = Boolean(state.taskTypeBusy) || state.reviewBusy || !options.length;
    set('task-type-source', manual ? 'Set by you.' : 'Suggested from the task description.');
    const feedback = state.taskTypeFeedback?.slug === run.slug ? state.taskTypeFeedback : null;
    set('task-type-feedback', feedback?.message || '');
    $('task-type-feedback').classList.toggle('review-error', Boolean(feedback?.error));
  }
  async function performTaskType() {
    if (state.taskTypeBusy || state.reviewBusy || !state.detail || state.detail.slug !== state.selected) return;
    const slug = state.selected, token = state.snapshot?.csrf_token;
    if (!token) return;
    const taskType = $('task-type').value || null;
    const revision = ++state.detailRevision;
    state.taskTypeBusy = slug;
    state.taskTypeFeedback = {slug, message: 'Saving task type…', error: false};
    renderTaskType(state.detail);
    renderReviewControls(state.detail);
    try {
      const response = await fetch('/api/runs/' + encodeURIComponent(slug) + '/task-type', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Dirtywork-CSRF': token},
        body: JSON.stringify({task_type: taskType}), signal: AbortSignal.timeout(15000),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not save the task type.');
      state.taskTypeFeedback = {slug, message: taskType ? 'Task type saved.' : 'Automatic suggestion restored.', error: false};
      if (state.selected === slug && state.detailRevision === revision) {
        state.detail = payload.run; renderDetail(payload.run);
      }
    } catch (error) {
      state.taskTypeFeedback = {slug, message: error.message + ' Refresh to check the saved type before retrying.', error: true};
    } finally {
      state.detailRevision++;
      state.taskTypeBusy = null;
      if (state.detail?.slug === state.selected) { renderTaskType(state.detail); renderReviewControls(state.detail); }
      refresh();
    }
  }
  $('task-type').addEventListener('change', performTaskType);
  function renderReviewControls(run) {
    $('repository').disabled = state.reviewBusy || Boolean(state.taskTypeBusy);
    const settled = Boolean(run.verdict);
    const eligible = Boolean(!settled && run.review_eligible && !run.resumed_by);
    $('review-panel').classList.toggle('review-actionable', eligible);
    $('review-form').hidden = !eligible;
    $('review-summary').hidden = eligible;
    if (settled) {
      set('review-summary-title', {accept: 'Accepted', reject: 'Rejected', cleanup: 'Cleanup'}[run.verdict] || 'Review saved');
      const timing = run.review_seconds != null ? `${duration(run.review_seconds)} recorded review time` : 'Review time not recorded';
      set('review-summary-note', timing + (run.note ? ` · ${run.note}` : ''));
    } else if (run.resumed_by) {
      set('review-summary-title', 'Superseded');
      set('review-summary-note', `Continued in ${run.resumed_by}. Review the latest run.`);
    } else if (!eligible) {
      set('review-summary-title', 'Waiting for review');
      set('review-summary-note', run.activity_state === 'running'
        ? 'The worker is still running. Review will be available when it finishes.'
        : 'Wait for the worker to finish before starting review.');
    }
    $('start-review').disabled = state.reviewBusy || Boolean(state.taskTypeBusy) || !eligible || run.review_in_progress;
    $('start-review').textContent = run.review_in_progress ? 'Review started' : 'Start Review';
    $('accept-review').disabled = state.reviewBusy || Boolean(state.taskTypeBusy) || !eligible || !run.review_in_progress;
    $('reject-review').disabled = state.reviewBusy || Boolean(state.taskTypeBusy) || !eligible || !run.review_in_progress;
    $('review-note').disabled = state.reviewBusy || !eligible;
    if (settled) {
      $('review-note').value = run.note || '';
      $('review-note').dataset.slug = run.slug;
    } else if ($('review-note').dataset.slug !== run.slug) {
      $('review-note').value = state.reviewNotes.get(run.slug) || '';
      $('review-note').dataset.slug = run.slug;
    }
    set('review-controls-state', settled ? 'Review saved' : run.resumed_by ? `Superseded by ${run.resumed_by}` : !eligible ? 'Wait for the worker to finish' : run.review_in_progress ? `${duration(run.review_elapsed_s)} · Timer running` : 'Start review to begin timing');
    const feedback = state.reviewFeedback?.slug === run.slug ? state.reviewFeedback : null;
    const message = feedback?.message || '';
    if ($('review-feedback').textContent !== message) set('review-feedback', message);
    $('review-feedback').classList.toggle('review-error', Boolean(feedback?.error));
  }
  $('review-note').addEventListener('input', () => {
    if (state.selected) state.reviewNotes.set(state.selected, $('review-note').value);
  });
  async function performReview(action) {
    if (state.reviewBusy || state.taskTypeBusy || !state.detail || state.detail.slug !== state.selected) return;
    const slug = state.selected;
    const token = state.snapshot?.csrf_token;
    if (!token) return;
    state.reviewBusy = true;
    const revision = ++state.detailRevision;
    state.reviewFeedback = {slug, message: 'Saving…', error: false};
    renderReviewControls(state.detail);
    renderTaskType(state.detail);
    try {
      const response = await fetch('/api/runs/' + encodeURIComponent(slug) + '/review', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Dirtywork-CSRF': token},
        body: JSON.stringify({action, note: state.reviewNotes.get(slug) || ''}),
        signal: AbortSignal.timeout(15000),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Could not save the review.');
      state.reviewFeedback = {slug, message: action === 'start' ? 'Review started. The timer is running.' : `${action === 'accept' ? 'Accepted' : 'Rejected'}. Decision and review time saved.`, error: false};
      if (state.selected === slug && state.detailRevision === revision) { state.detail = payload.run; renderDetail(payload.run); }
    } catch (error) {
      state.reviewFeedback = {slug, message: error.message + ' Refresh to check the saved state before retrying.', error: true};
    } finally {
      state.reviewBusy = false;
      if (state.detail?.slug === state.selected) { renderReviewControls(state.detail); renderTaskType(state.detail); }
      refresh();
    }
  }
  $('start-review').addEventListener('click', () => performReview('start'));
  $('accept-review').addEventListener('click', () => performReview('accept'));
  $('reject-review').addEventListener('click', () => performReview('reject'));
  function renderRepositoryScope() {
    const select = $('repository');
    const value = state.repository === null ? '' : 'repo:' + state.repository;
    const choiceBeforePoll = select.value;
    const groups = state.allSnapshot.repositories || [];
    const keys = new Set(['', ...groups.map(group => 'repo:' + group.repo)]);
    for (const option of [...select.children]) if (!keys.has(option.value)) option.remove();
    let previous = [...select.children].find(option => option.value === '');
    if (!previous) {
      previous = element('option', '', 'All repositories'); previous.value = '';
      select.insertBefore(previous, select.firstChild);
    }
    for (const group of groups) {
      const key = 'repo:' + group.repo;
      let option = [...select.children].find(option => option.value === key);
      if (!option) { option = element('option'); option.value = key; }
      const text = `${group.repo || 'Repository not recorded'} · ${number(group.summary.total)} ${group.summary.total === 1 ? 'run' : 'runs'}`;
      if (option.textContent !== text) option.textContent = text;
      if (previous.nextSibling !== option) select.insertBefore(option, previous.nextSibling);
      previous = option;
    }
    if (select.dataset.repository !== value || !keys.has(choiceBeforePoll)) select.value = value;
    else if (select.value !== choiceBeforePoll) select.value = choiceBeforePoll;
    select.dataset.repository = value;
    select.disabled = state.reviewBusy || Boolean(state.taskTypeBusy);
    const all = state.repository === null;
    const scope = all ? 'All repositories' : 'This repository';
    const context = all ? 'All repositories' : `Repository: ${state.repository || 'Not recorded'}`;
    const total = state.snapshot.summary.total;
    const description = `${state.repositoryNotice || ''}${context} · ${number(total)} recorded ${total === 1 ? 'run' : 'runs'}`;
    if ($('repository-context').textContent !== description) set('repository-context', description);
    set('token-scope', `Lifetime · ${scope.toLowerCase()} · including demos`);
    set('outcomes-scope', `${scope} · all runs, including demos`);
    set('funnel-scope', `${scope} · including demos`);
    set('models-scope', `${scope} · including demos`);
    set('fit-scope', `Real work only · ${scope.toLowerCase()}`);
  }
  function applyRepositoryScope() {
    if (!state.allSnapshot) return;
    let snapshot = presentation.scopeSnapshot(state.allSnapshot, state.repository);
    if (!snapshot) {
      state.repository = null;
      state.repositoryNotice = 'Saved repository is no longer available; showing all repositories. ';
      remember('repository', '');
      state.detailRevision++;
      snapshot = state.allSnapshot;
    }
    state.snapshot = snapshot;
    if (!snapshot.runs.some(run => run.slug === state.selected)) {
      state.selected = snapshot.runs.find(run => run.activity_state === 'running')?.slug || snapshot.runs[0]?.slug || null;
      state.detail = null;
      state.detailRevision++;
      remember('run', state.selected || '');
    }
    const hasDetail = state.detail?.slug === state.selected && Boolean(state.selected) &&
      (state.repository === null || (state.detail.repo || '') === state.repository);
    $('run-panel').hidden = !hasDetail;
    $('run-loading').hidden = !state.selected || hasDetail;
    $('empty').hidden = snapshot.runs.length > 0;
    if (!hasDetail) {
      state.detail = null;
      for (const id of ['start-review', 'accept-review', 'reject-review', 'review-note', 'task-type']) $(id).disabled = true;
      renderWoprRun(null);
      for (const [id, text] of [['worker-time', '—'], ['turns', '— / —'], ['verdict', '—'], ['worker-time-note', state.selected ? 'Loading selected run' : 'No run selected'], ['verdict-note', 'No verdict recorded']]) set(id, text);
      set('run-task', 'Loading selected run…');
    }
    renderRepositoryScope();
    renderList();
    renderOutcomes();
    const summary = snapshot.summary;
    const connection = `Live · ${number(summary.active)} active · ${summary.reviewing ? number(summary.reviewing) + ' in review · ' : ''}${number(summary.total)} recorded`;
    if ($('connection').textContent !== connection) set('connection', connection);
  }
  $('repository').addEventListener('change', () => {
    if (state.reviewBusy || state.taskTypeBusy) {
      $('repository').value = state.repository === null ? '' : 'repo:' + state.repository;
      return;
    }
    const value = $('repository').value;
    state.repository = value.startsWith('repo:') ? value.slice(5) : null;
    state.repositoryNotice = '';
    state.detailRevision++;
    state.limit = 8;
    remember('repository', value);
    applyRepositoryScope();
    loadDetail();
  });
  function selectRun(slug) {
    if (state.selected === slug) return;
    state.selected = slug;
    state.detail = null;
    state.detailRevision++;
    for (const id of ['start-review', 'accept-review', 'reject-review', 'review-note', 'task-type']) $(id).disabled = true;
    remember('run', slug);
    renderList();
    $('run-panel').hidden = true;
    $('run-loading').hidden = false;
    renderWoprRun(null);
    for (const [id, text] of [['worker-time', '—'], ['turns', '— / —'], ['verdict', '—'], ['worker-time-note', 'Loading selected run'], ['verdict-note', 'No verdict recorded']]) set(id, text);
    set('run-task', 'Loading selected run…');
    loadDetail();
  }
  function renderList() {
    const allRuns = state.snapshot.runs;
    const runs = presentation.filterRuns(allRuns, {scope: state.runScope, query: state.query});
    const counts = presentation.runCounts(allRuns);
    for (const scope of ['all', 'active', 'review']) {
      set('filter-count-' + scope, number(counts[scope]));
      $('filter-' + scope).setAttribute('aria-pressed', String(scope === state.runScope));
    }
    set('run-count', `${number(runs.length)}${state.query ? ' matches' : ' runs'}`);
    const jump = $('run-jump');
    const jumpScope = JSON.stringify([state.repository, state.runScope, state.query]);
    const choiceBeforePoll = jump.value;
    const jumpKeys = new Set(['', ...runs.map(run => run.slug)]);
    for (const option of [...jump.children]) if (!jumpKeys.has(option.value)) option.remove();
    let previousOption = [...jump.children].find(option => option.value === '');
    if (!previousOption) {
      previousOption = element('option', '', 'Select a run…'); previousOption.value = '';
      jump.insertBefore(previousOption, jump.firstChild);
    }
    for (const run of runs) {
      let option = [...jump.children].find(option => option.value === run.slug);
      if (!option) { option = element('option'); option.value = run.slug; }
      const title = `${presentation.taskTitle(run.task, run.slug)} · ${label(run)} · ${run.slug}`;
      if (option.textContent !== title) option.textContent = title;
      const next = previousOption.nextSibling;
      if (next !== option) jump.insertBefore(option, next);
      previousOption = option;
    }
    const selectedInScope = runs.some(run => run.slug === state.selected);
    if (jump.dataset.scope !== jumpScope || jump.dataset.selected !== state.selected || !jumpKeys.has(choiceBeforePoll)) {
      jump.value = selectedInScope ? state.selected : '';
    } else if (jump.value !== choiceBeforePoll) jump.value = choiceBeforePoll;
    jump.dataset.scope = jumpScope;
    jump.dataset.selected = state.selected;
    jump.disabled = !runs.length;
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
        button.append(element('span', 'run-choice-title'), element('span', 'run-choice-meta'), element('span', 'run-choice-model'), element('span', 'run-choice-slug'), element('span', 'run-choice-context'));
        button.addEventListener('click', () => selectRun(run.slug));
      }
      button.setAttribute('aria-pressed', String(run.slug === state.selected));
      button.children[0].textContent = presentation.taskTitle(run.task, run.slug);
      button.children[1].textContent = `${label(run)} · ${duration(run.duration_s)}`;
      button.children[2].textContent = run.model || 'Unknown model';
      button.children[3].textContent = run.slug;
      button.children[4].textContent = `${run.repo ? run.repo.split(/[\\/]/).filter(Boolean).pop() : 'Repository not recorded'} · ${run.branch || 'Branch not recorded'}`;
      button.children[4].title = [run.repo, run.worktree, run.branch].filter(Boolean).join('\n');
      const next = previous ? previous.nextSibling : list.firstChild;
      if (next !== button) list.insertBefore(button, next);
      previous = button;
    }
    $('more-runs').hidden = runs.length <= state.limit;
    $('no-matches').hidden = runs.length > 0 && selectedInScope;
    set('no-matches', runs.length ? 'Choose a run from this filter. Your current selection is unchanged.'
      : state.query ? 'No runs match this search in the selected filter.' : state.runScope === 'active'
      ? 'No active workers right now.' : state.runScope === 'review' ? 'No runs are waiting for review.' : 'No saved runs yet.');
  }
  for (const scope of ['all', 'active', 'review']) $('filter-' + scope).addEventListener('click', () => {
    state.runScope = scope; state.limit = 8;
    remember('runScope', scope);
    if (state.snapshot) renderList();
  });
  $('run-jump').addEventListener('change', () => { if ($('run-jump').value) selectRun($('run-jump').value); });
  $('run-search').addEventListener('input', () => {
    state.query = $('run-search').value.trim().toLowerCase();
    state.limit = 8;
    if (state.snapshot) renderList();
  });
  $('more-runs').addEventListener('click', () => { state.limit += 20; renderList(); });
  const timelineLanes = [['lifecycle', 'Run'], ['model', 'Model'], ['tools', 'Tools'], ['harness', 'Harness'], ['verify', 'Verify']];
  const timelineNodes = new Map();
  let timelineSlug = null;
  let timelineSelection = null;
  function timelineRecordedTime(point, run) {
    let stamp = typeof point.ts === 'string' ? Date.parse(point.ts) : NaN;
    if (!Number.isFinite(stamp) && run.timeline?.origin === 'run start' &&
        typeof run.started === 'string' && Number.isFinite(point.at_s)) {
      stamp = Date.parse(run.started) + point.at_s * 1000;
    }
    const date = new Date(stamp);
    if (Number.isFinite(date.getTime())) {
      return {stamp, label: date.toLocaleString(undefined, {year: 'numeric', month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit', second: '2-digit', timeZoneName: 'short'})};
    }
    if (!Number.isFinite(point.at_s) || point.at_s < 0) return null;
    const seconds = point.at_s.toLocaleString(undefined, {maximumFractionDigits: 6});
    return {stamp: null, label: `${seconds}s after ${run.timeline?.origin || 'the recorded origin'}`};
  }
  function selectTimelineEvent(key) {
    timelineSelection = key;
    for (const [id, node] of timelineNodes) node.setAttribute('aria-pressed', String(id === key));
    set('timeline-selection', timelineNodes.get(key).getAttribute('aria-label'));
  }
  function renderTimeline(run) {
    const chart = $('timeline-chart');
    const history = $('review-events');
    if (timelineSlug !== run.slug) {
      timelineSlug = run.slug;
      timelineSelection = null;
      timelineNodes.clear();
      chart.replaceChildren();
      history.replaceChildren();
      set('timeline-selection', 'Select an event for its recorded time.');
    }
    const timeline = run.timeline;
    const points = timeline?.points || [];
    const workerPoints = points.filter(point => timelineLanes.some(([lane]) => lane === point.lane) &&
      Number.isFinite(point.at_s) && point.at_s >= 0);
    const reviewPoints = points.filter(point => point.lane === 'review')
      .map(point => ({point, time: timelineRecordedTime(point, run)})).filter(event => event.time)
      .sort((a, b) => a.time.stamp != null && b.time.stamp != null
        ? a.time.stamp - b.time.stamp : (a.point.at_s || 0) - (b.point.at_s || 0));
    const wanted = new Set([...workerPoints, ...reviewPoints.map(event => event.point)]
      .map(point => String(point.sequence)));
    for (const [key, button] of timelineNodes) {
      if (!wanted.has(key)) { button.remove(); timelineNodes.delete(key); }
    }
    if (!workerPoints.length) {
      if (!chart.querySelector('.timeline-empty')) {
        chart.replaceChildren(element('p', 'timeline-empty', 'No timestamped worker events recorded yet.'));
      }
    } else {
      if (!chart.querySelector('.timeline-axis')) {
        chart.replaceChildren(element('div', 'timeline-axis'));
        for (const [lane, label] of timelineLanes) {
          const row = element('div', 'timeline-lane');
          const track = element('div', 'timeline-track');
          track.dataset.lane = lane;
          row.append(element('span', 'timeline-lane-label', label), track);
          chart.append(row);
        }
      }
      const extent = Math.max(Number.isFinite(timeline.elapsed_s) ? timeline.elapsed_s : 0,
        ...workerPoints.map(point => point.at_s), 1);
      const axis = chart.querySelector('.timeline-axis');
      if (!axis.children.length) axis.append(...[0, 0.5, 1].map(() => element('span')));
      [0, 0.5, 1].forEach((fraction, index) => {
        const value = duration(extent * fraction);
        if (axis.children[index].textContent !== value) axis.children[index].textContent = value;
      });
      for (const point of workerPoints) {
        const key = String(point.sequence);
        let marker = timelineNodes.get(key);
        if (!marker) {
          marker = element('button', 'timeline-marker');
          marker.type = 'button';
          marker.dataset.lane = point.lane;
          marker.addEventListener('click', () => selectTimelineEvent(key));
          chart.querySelector(`[data-lane="${point.lane}"]`).append(marker);
          timelineNodes.set(key, marker);
        }
        marker.style.left = `${Math.min(100, point.at_s / extent * 100)}%`;
        marker.dataset.outcome = point.outcome;
        const seconds = point.at_s.toLocaleString(undefined, {maximumFractionDigits: 2});
        marker.setAttribute('aria-label', `${point.label} · ${seconds}s after ${timeline.origin}${point.outcome === 'failed' ? ' · failed or blocked' : ''}`);
        marker.setAttribute('aria-pressed', String(key === timelineSelection));
      }
    }
    $('review-history').hidden = !reviewPoints.length;
    let previous = null;
    for (const {point, time} of reviewPoints) {
      const key = String(point.sequence);
      let button = timelineNodes.get(key);
      if (!button) {
        button = element('button', 'review-event');
        button.type = 'button';
        button.append(element('span', 'review-event-label'), element('span', 'review-event-time'));
        button.addEventListener('click', () => selectTimelineEvent(key));
        timelineNodes.set(key, button);
      }
      button.dataset.outcome = point.outcome;
      if (button.children[0].textContent !== point.label) button.children[0].textContent = point.label;
      if (button.children[1].textContent !== time.label) button.children[1].textContent = time.label;
      button.setAttribute('aria-label', `${point.label} · ${time.label}${point.outcome === 'failed' ? ' · failed or blocked' : ''}`);
      button.setAttribute('aria-pressed', String(key === timelineSelection));
      const next = previous ? previous.nextSibling : history.firstChild;
      if (next !== button) history.insertBefore(button, next);
      previous = button;
    }
    if (timelineSelection && !wanted.has(timelineSelection)) {
      timelineSelection = null;
      set('timeline-selection', 'Selected event is outside the retained timeline.');
    } else if (timelineSelection) {
      const selectedLabel = timelineNodes.get(timelineSelection).getAttribute('aria-label');
      if ($('timeline-selection').textContent !== selectedLabel) set('timeline-selection', selectedLabel);
    } else if (!wanted.size && $('timeline-selection').textContent !== 'No timestamped events to select.') {
      set('timeline-selection', 'No timestamped events to select.');
    } else if (wanted.size && $('timeline-selection').textContent === 'No timestamped events to select.') {
      set('timeline-selection', 'Select an event for its recorded time.');
    }
    set('timeline-status', run.review_in_progress ? 'Live review · 3s refresh' : run.activity_state === 'running' ? 'Live · 3s refresh' : 'Recorded events');
    const notices = ['Worker event timestamps · gaps are unclassified time.'];
    if (timeline?.untimed_events) notices.push(`${number(timeline.untimed_events)} events without usable timestamps.`);
    if (timeline?.omitted_events) notices.push(`Latest 2,000 events shown; ${number(timeline.omitted_events)} older events omitted.`);
    set('timeline-note', notices.join(' '));
  }
  function diffCode(file) {
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
      old.setAttribute('aria-label', line.old == null ? 'No previous line' : `Before: line ${line.old}`);
      next.setAttribute('aria-label', line.new == null ? 'No new line' : `After: line ${line.new}`);
      const sign = element('span', 'diff-sign', {add: '+', remove: '−', hunk: '⋯'}[line.kind] || '');
      sign.setAttribute('role', 'cell');
      const content = element('code', 'diff-text', line.kind === 'hunk' ? (line.text || 'Changed lines') : line.text);
      content.setAttribute('role', 'cell');
      entry.append(old, next, sign, content); code.append(entry);
    }
    if (!file.rows.length) code.append(element('p', 'diff-empty', 'File metadata or binary contents changed; no text lines to display.'));
    return code;
  }
  function renderResultPreview(event) {
    const details = element('details', 'result-preview');
    const preview = event.change_preview;
    if (preview) {
      const heading = element('summary', 'edit-preview-heading');
      heading.append(element('span', '', 'View edit'),
        element('span', 'diff-count-add', `+${preview.additions}`),
        element('span', 'diff-count-remove', `−${preview.deletions}`));
      const body = element('div', 'activity-edit');
      body.append(element('p', 'edit-description', preview.summary),
        diffCode({path: event.target || 'this file', rows: preview.rows}),
        element('p', 'edit-preview-note', preview.truncated
          ? 'Partial preview. See File changes below for more context.'
          : 'This edit only. See File changes below for the overall run.'));
      details.append(heading, body);
    } else {
      details.append(element('summary', '', 'View output'), element('pre', '', event.detail));
    }
    return details;
  }
  let changesRequest = null, changesQueued = null;
  function loadChanges(slug) {
    const revision = state.detailRevision;
    if (changesRequest) {
      if (changesRequest.slug !== slug || changesRequest.revision !== revision) changesQueued = {slug, revision};
      return changesRequest.promise;
    }
    const request = {slug, revision};
    changesRequest = request;
    request.promise = fetchChanges(slug, revision).finally(() => {
      changesRequest = null;
      const next = changesQueued;
      changesQueued = null;
      if (next && state.selected === next.slug && state.detailRevision === next.revision && $('files-details').open) loadChanges(next.slug);
    });
    return request.promise;
  }
  async function fetchChanges(slug, revision) {
    try {
      const data = await api(`/api/runs/${encodeURIComponent(slug)}/changes`);
      if (state.selected !== slug || state.detailRevision !== revision) return;
      if ($('files-status').textContent !== data.message) set('files-status', data.message);
      $('files-status').title = data.captured_at ? `Captured ${new Date(data.captured_at).toLocaleString()}` : '';
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
        row.append(heading, diffCode(file)); return row;
      }));
      output.dataset.signature = signature;
    } catch (_) {
      if (state.selected === slug && state.detailRevision === revision) set('files-status', 'Could not load changes. Retrying on the next refresh.');
    }
  }
  $('files-details').addEventListener('toggle', () => {
    if ($('files-details').open && state.selected) loadChanges(state.selected);
  });

  function renderDetail(run) {
    $('run-panel').hidden = false;
    $('run-loading').hidden = true;
    renderWoprRun(run);
    renderTaskType(run);
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
    const title = presentation.taskTitle(run.task, 'Task not recorded');
    set('run-task', title);
    set('run-model', [run.model || 'Unknown model', run.provider, run.sandbox && `${run.sandbox} sandbox`].filter(Boolean).join(' · '));
    set('run-repository', run.repo || 'Not recorded');
    set('run-worktree', run.worktree || 'Not recorded');
    set('run-branch', run.branch || 'Not recorded');
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
    $('task-details').hidden = !run.task || run.task.trim() === title;
    set('full-task', run.task);
    $('verify-details').hidden = !run.verify;
    set('verify-output', run.verify ? [run.verify.command, `Result: ${verification} · exit ${run.verify.exit_code ?? 'unknown'}`, run.verify.output_tail].filter(Boolean).join('\n') : '');
    if ($('files-output').dataset.slug !== run.slug) {
      $('files-output').replaceChildren();
      $('files-output').dataset.slug = run.slug;
      $('files-output').dataset.signature = '';
      $('files-details').open = Boolean(run.review_eligible || run.activity_state === 'running' || run.files_changed?.length);
      $('verify-details').open = Boolean(run.verify && run.review_eligible);
      set('files-status', 'Expand to load file changes.');
      $('files-status').title = '';
    }
    if ($('files-details').open) loadChanges(run.slug);
    set('lineage', [run.resumed_from && `Resumed from ${run.resumed_from}`, run.resumed_by && `Resumed by ${run.resumed_by}`, run.note && `Review note: ${run.note}`].filter(Boolean).join(' · '));
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
          const details = renderResultPreview(event);
          details.dataset.key = `${run.slug}:${event.sequence}`;
          details.open = expanded.has(details.dataset.key);
          row.append(details);
        }
        return row;
      });
      $('activity-log').replaceChildren(...(rows.length ? rows : [element('p', 'secondary', 'No flushed transcript events yet.')]));
      $('activity-log').dataset.signature = signature;
      if (focusedKey) [...$('activity-log').querySelectorAll('details')].find(node => node.dataset.key === focusedKey)?.querySelector('summary').focus({preventScroll: true});
    }
  }
  function renderOffloading() {
    const data = state.snapshot.offloading || {groups: [], reviewed: 0, review_samples: 0, excluded_demos: 0, unclassified: 0};
    const output = $('offload-fit-types');
    const signature = JSON.stringify(data);
    if (output.dataset.signature === signature) return;
    output.dataset.signature = signature;
    set('offload-fit-summary', `${number(data.reviewed)} reviewed real-work attempts · ${number(data.review_samples)} timed reviews · ${number(data.excluded_demos)} demo or benchmark runs excluded`);
    const groups = data.groups;
    const hasTiming = groups.some(group => Number.isFinite(group.median_review_s) && group.median_review_s >= 0);
    const timed = groups.filter(group => Number.isFinite(group.median_review_s) && group.median_review_s >= 0 &&
      Number.isFinite(group.acceptance_rate) && group.acceptance_rate >= 0 && group.acceptance_rate <= 1);
    const chart = $('offload-fit-chart');
    chart.hidden = !timed.length;
    $('offload-fit-empty').hidden = Boolean(timed.length);
    set('offload-fit-empty', groups.length
      ? hasTiming ? 'No task type has both a recorded review time and an acceptance rate yet.'
        : 'No timed real-work reviews yet. Dashes mark missing timing. Start a review before inspecting a result, then accept or reject it to record the effort.'
      : 'Review a real task to begin comparing task types. Demos and benchmarks stay outside this comparison.');
    set('offload-fit-selection', '');
    const describe = group => `${group.label}: ${number(group.accepted)} / ${number(group.reviewed)} accepted (${Math.round(group.acceptance_rate * 100)}%). Median review ${duration(group.median_review_s)} across ${number(group.review_samples)} timed reviews and ${number(group.distinct_timed_tasks)} unique timed task descriptions. ${group.evidence === 'ready' ? 'At least five unique reviewed and timed task descriptions.' : 'Limited data; treat this as an early signal.'}`;
    const selectGroup = group => set('offload-fit-selection', describe(group));
    chart.replaceChildren();
    if (timed.length) {
      chart.setAttribute('role', 'figure');
      chart.setAttribute('aria-label', 'Task types by acceptance rate and median review time. Higher and farther left is favorable.');
      const maximum = Math.ceil(Math.max(60, ...timed.map(group => group.median_review_s)) / 60) * 60;
      const yLabel = element('p', 'fit-y-label', 'Acceptance rate');
      const yAxis = element('div', 'fit-axis-y');
      yAxis.append(...['100%', '50%', '0%'].map(value => element('span', '', value)));
      const plot = element('div', 'fit-plot');
      for (const group of timed) {
        const index = groups.indexOf(group) + 1;
        const point = element('button', 'fit-point' + (group.evidence === 'ready' ? '' : ' fit-point-limited'), index);
        point.type = 'button';
        point.style.left = `${group.median_review_s / maximum * 100}%`;
        point.style.top = `${(1 - group.acceptance_rate) * 100}%`;
        point.setAttribute('aria-label', describe(group));
        point.title = describe(group);
        point.addEventListener('click', () => selectGroup(group));
        point.addEventListener('focus', () => selectGroup(group));
        plot.append(point);
      }
      const xAxis = element('div', 'fit-axis-x');
      xAxis.append(...[0, maximum / 2, maximum].map(value => element('span', '', duration(value))));
      chart.append(yLabel, yAxis, plot, xAxis,
        element('p', 'fit-x-label', 'Median review time · shorter is farther left'),
        element('p', 'secondary fit-chart-note', 'Numbers match the table. Hollow points have limited data. Select a point or task type for details.'));
    }
    output.replaceChildren(...groups.map((group, index) => {
      const row = element('tr'); row.dataset.type = group.id;
      const type = element('td');
      if (timed.includes(group)) {
        const name = element('button', 'fit-type-button', group.label); name.type = 'button';
        name.addEventListener('click', () => selectGroup(group));
        type.append(element('span', 'fit-key', index + 1), name);
      } else type.append(element('span', '', group.label));
      type.append(element('small', '', `${number(group.total)} ${group.total === 1 ? 'run' : 'runs'} · ${number(group.distinct_tasks)} unique task ${group.distinct_tasks === 1 ? 'description' : 'descriptions'}`));
      const rate = element('td');
      const knownRate = Number.isFinite(group.acceptance_rate) && group.acceptance_rate >= 0 && group.acceptance_rate <= 1;
      rate.append(element('span', 'fit-value', knownRate ? `${Math.round(group.acceptance_rate * 100)}%` : 'No verdicts'),
        element('small', '', `${number(group.accepted)} / ${number(group.reviewed)} reviewed`));
      if (knownRate) {
        const track = element('div', 'fit-rate-track');
        const fill = element('span', 'fit-rate-fill'); fill.style.width = `${group.acceptance_rate * 100}%`;
        track.setAttribute('aria-hidden', 'true'); track.append(fill); rate.append(track);
      }
      const review = element('td');
      const knownTime = Number.isFinite(group.median_review_s) && group.median_review_s >= 0;
      const time = element('span', 'fit-value', knownTime ? duration(group.median_review_s) : '—');
      if (!knownTime) {
        time.setAttribute('aria-label', 'No review time recorded');
        time.title = 'No review time recorded';
      }
      review.append(time,
        element('small', '', `${number(group.review_samples)} / ${number(group.reviewed)} reviews timed`));
      const evidence = element('td');
      const coverage = element('span', 'fit-evidence', {no_timing: '—', limited: 'Limited data', ready: '5+ timed task descriptions'}[group.evidence] || 'Limited data');
      if (group.evidence === 'no_timing') {
        coverage.setAttribute('aria-label', 'No timed reviews recorded');
        coverage.title = 'No timed reviews recorded';
      }
      evidence.append(coverage,
        element('small', '', `${number(group.distinct_reviewed_tasks)} unique reviewed task ${group.distinct_reviewed_tasks === 1 ? 'description' : 'descriptions'} · ${number(group.distinct_timed_tasks)} timed`));
      row.append(type, rate, review, evidence); return row;
    }));
    if (!groups.length) {
      const row = element('tr'), cell = element('td', '', 'No task types to compare yet.');
      cell.setAttribute('colspan', '4'); row.append(cell); output.append(row);
    }
  }
  function renderOutcomes() {
    renderOffloading();
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
    if (state.reviewBusy || state.taskTypeBusy) return;
    const slug = state.selected;
    const revision = state.detailRevision;
    if (!slug) return;
    try {
      const run = await api('/api/runs/' + encodeURIComponent(slug));
      if (state.selected !== slug || state.detailRevision !== revision || state.reviewBusy || state.taskTypeBusy) return;
      if (typeof state.repository === 'string' && (run.repo || '') !== state.repository) return;
      state.detail = run;
      renderDetail(run);
    } catch (error) {
      if (state.selected === slug && state.detailRevision === revision && !state.reviewBusy && !state.taskTypeBusy) {
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
      state.allSnapshot = snapshot;
      $('error').hidden = true;
      const indicator = $('connection-dot');
      indicator.classList.remove('offline');
      indicator.classList.add('syncing');
      clearTimeout(syncIndicatorTimer);
      syncIndicatorTimer = setTimeout(() => indicator.classList.remove('syncing'), 900);
      $('connection').title = `Refreshes every 3 seconds · Last synced ${new Date(snapshot.updated_at).toLocaleTimeString()}`;
      set('source', snapshot.runs_dir);
      applyRepositoryScope();
      $('data-warnings').hidden = !snapshot.warnings.length;
      set('warning-summary', `All repositories · ${number(snapshot.warnings.length)} run data notices`);
      $('warnings-list').replaceChildren(...snapshot.warnings.map(warning => element('li', '', warning)));
      await loadDetail();
    } catch (error) {
      clearTimeout(syncIndicatorTimer);
      $('connection-dot').classList.remove('syncing');
      $('connection-dot').classList.add('offline');
      set('connection', 'Disconnected · last available data shown');
      $('connection').title = 'Connection unavailable · retrying automatically every 3 seconds';
      set('error', error.message + ' Check that dirtywork dashboard is still running. Retrying automatically.');
      $('error').hidden = false;
    } finally { state.busy = false; }
  }
  refresh();
  setInterval(refresh, 3000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
