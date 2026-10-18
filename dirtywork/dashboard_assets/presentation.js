(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DirtyworkPresentation = factory();
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function taskTitle(task, fallback = 'Untitled run') {
    if (typeof task !== 'string') return fallback;
    let title = '';
    for (const line of task.split(/\r?\n/)) {
      title = line.trim().replace(/^#{1,6}\s+/, '').replace(/^Task:\s*/i, '').trim();
      if (title) break;
    }
    if (!title) return fallback;
    // Sentence punctuation needs following whitespace: internal filename and
    // version periods remain literal task text.
    const sentence = title.match(/^.*?[.!?](?:["')\]]*)?(?=\s+\S)/);
    if (sentence) title = sentence[0];
    const characters = Array.from(title);
    if (characters.length <= 96) return title;
    let preview = characters.slice(0, 95).join('');
    const boundary = preview.search(/\s+\S*$/);
    if (boundary > 0) preview = preview.slice(0, boundary);
    return preview.trimEnd() + '…';
  }

  function needsReview(run) {
    return run.review_eligible === true && !run.verdict && !run.resumed_by;
  }

  function scopeSnapshot(snapshot, repository = null) {
    if (repository === null) return snapshot;
    const group = (snapshot.repositories || []).find(group => group.repo === repository);
    if (!group) return null;
    // Aggregate from full briefs on the server, never the truncated run previews.
    return {...snapshot,
      runs: snapshot.runs.filter(run => (typeof run.repo === 'string' ? run.repo : '') === repository),
      summary: group.summary, models: group.models, offloading: group.offloading};
  }

  function filterRuns(runs, {scope = 'all', query = ''} = {}) {
    const search = typeof query === 'string' ? query.toLowerCase().trim() : '';
    return runs.filter(run => {
      if (scope === 'active' && run.activity_state !== 'running') return false;
      if (scope === 'review' && !needsReview(run)) return false;
      if (!search) return true;
      return [run.task, run.slug, run.model, run.repo, run.worktree, run.branch, run.status, run.verdict,
        run.task_type && run.task_type.label]
        .some(value => typeof value === 'string' && value.toLowerCase().includes(search));
    });
  }

  function runCounts(runs) {
    return {all: runs.length,
      active: runs.filter(run => run.activity_state === 'running').length,
      review: runs.filter(needsReview).length};
  }

  return {taskTitle, scopeSnapshot, filterRuns, runCounts};
});
