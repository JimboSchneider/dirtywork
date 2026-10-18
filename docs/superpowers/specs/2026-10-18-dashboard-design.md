# Local run dashboard

The user approved the interactive dashboard and requested implementation against real run data, preserving theme support. Subsequent requests added token metrics, a live event timeline, explanatory icons, review timers and browser review controls. The browser review controls explicitly supersede the original read-only scope below: a same-origin, CSRF-protected POST updates only review metadata through shared CLI state transitions. No tools, merges or cleanup actions are exposed.

## Delivery

`dirtywork dashboard [--port 8765] [--runs-dir PATH] [--open]` serves a read-only dashboard on IPv4 loopback. Port zero selects an available port. The existing stdlib-only Python >=3.9 runtime remains unchanged. Static HTML, CSS and JavaScript ship as package data, without a build step or external assets.

The browser refreshes run summaries and the selected run every three seconds. It shows activity, elapsed worker time, turns, tool results, final token totals, verification evidence, and recorded review verdicts. Recent runs are selectable. Outcomes aggregate acceptance among accept/reject verdicts, worker duration among ended runs, recorded review time, and comparisons by model. Cleanup is counted separately from review. Missing values stay unknown. There are no synthetic savings or GPU metrics.

## Data

Read run.json and transcript.jsonl from the selected runs root without changing either. Metadata supplies provenance and verdicts; transcript run_end supplies duration and usage. Assistant and tool_result events supply observed counts. Tolerate missing fields, unknown events, corrupt files, incomplete final lines and concurrent updates, with visible data-quality warnings. Ignore symlinked run directories/files. Cache transcript scans by file identity, size and modification time, prune deleted runs, and keep only a bounded activity tail in memory. Running records without a live host_pid are marked inactive; records without PID evidence are unconfirmed. This is process evidence, not a guarantee against PID reuse.

## Interface and themes

Use the approved mission-control layout, responsive at 320px, with Run activity and Outcomes views. Ion, Orchid and Terminal accents share semantic theme tokens. Explicit System/Light/Dark appearance and theme selections persist in localStorage. All run-authored strings render as text. Preserve keyboard focus during polling; avoid replacing navigation or selection controls on every refresh.

## Local HTTP boundary

Serve only explicit asset routes and JSON read endpoints. Require the loopback Host and reject foreign Origin/Sec-Fetch-Site requests. No CORS, write endpoints, shell actions or arbitrary file serving. Send no-store, nosniff and a restrictive same-origin CSP. Run path resolution uses rundir.run_dir_for. Binding failures exit 2; Ctrl-C shuts down cleanly.

## Verification

Test aggregation denominators and missing values; current/dead/unconfirmed processes; transcript append and partial lines; malformed records; unknown events; symlink and traversal refusals; local HTTP route/origin boundaries; CLI parsing; and package assets. Exercise themes, run selection, view switching and polling in the browser against local records. Run the existing unit suite.
