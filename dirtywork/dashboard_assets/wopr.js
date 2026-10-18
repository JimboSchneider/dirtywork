/* Decorative WOPR cues derive from recorded evidence, never inferred success. */
(function (root, factory) {
  const helpers = factory();
  if (typeof module === 'object' && module.exports) module.exports = helpers;
  else root.DirtyworkWopr = helpers;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  function statusStamp(run) {
    if (!run) return '';
    if (['unconfirmed', 'unknown'].includes(run.activity_state)) return '[ AWAITING SIGNAL ]';
    if (run.activity_state === 'inactive') return '[ SIGNAL LOST ]';
    if (run.activity_state === 'running') {
      const evidence = run.turns > 0 || run.tool_calls > 0 ||
        (run.activity || []).some(event => ['assistant', 'tool_result'].includes(event.event));
      return evidence ? '[ WORKER ONLINE ]' : '[ ESTABLISHING UPLINK… ]';
    }
    if (run.status === 'completed') {
      return run.verify?.passed === false ? '[ VERIFICATION FAILED ]' : '[ MISSION COMPLETE ]';
    }
    return '[ SIGNAL LOST ]';
  }
  function hasNewActivity(previous, next) {
    if (!previous || !next || previous.slug !== next.slug) return false;
    const seen = new Set((previous.activity || []).map(event => event.sequence));
    return (next.activity || []).some(event => event.sequence != null && !seen.has(event.sequence));
  }
  return {statusStamp, hasNewActivity};
});
