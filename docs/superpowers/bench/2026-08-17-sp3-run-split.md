| Run | Status | Turns | Wall | Model time | Tool time | model s/turn | tool s/turn | prompt tok/s (model time) | compl tok/s (model time) | slowest tool call |
|---|---|---|---|---|---|---|---|---|---|---|
| task-1 | completed | 6 | 0.9m | 0.4m (47%) | 0.5m (53%) | 4.3s | 4.8s | 1,184 | 75.8 | 28s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-2 | completed | 11 | 1.8m | 1.3m (74%) | 0.5m (26%) | 7.3s | 2.6s | 2,158 | 73.1 | 28s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-3 | max_turns | 80 | 4.5m | 4.3m (94%) | 0.3m (6%) | 3.2s | 0.2s | 11,303 | 62.1 | 7s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-4 | completed | 24 | 2.3m | 0.9m (39%) | 1.4m (61%) | 2.2s | 3.4s | 3,233 | 68.4 | 28s bash {"command":"python3 -m pytest -q 2>&1 | head -20"} |
| task-5 | completed | 29 | 3.1m | 2.4m (78%) | 0.7m (22%) | 5.0s | 1.4s | 3,932 | 71.1 | 29s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-6 | max_turns | 120 | 29.4m | 28.7m (98%) | 0.7m (2%) | 14.4s | 0.3s | 3,259 | 20.3 | 29s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-6a | context_exhausted | 119 | 32.9m | 32.7m (100%) | 0.1m (0%) | 16.5s | 0.1s | 2,739 | 22.5 | 1s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-6b | max_turns | 120 | 5.3m | 5.2m (98%) | 0.1m (2%) | 2.6s | 0.1s | 13,348 | 62.0 | 1s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-6c | max_turns | 100 | 6.8m | 6.3m (94%) | 0.4m (6%) | 3.8s | 0.3s | 13,869 | 53.6 | 5s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-7 | completed | 27 | 2.4m | 1.9m (78%) | 0.5m (22%) | 4.2s | 1.2s | 4,146 | 51.7 | 30s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-8 | completed | 64 | 7.1m | 2.8m (39%) | 4.3m (61%) | 2.6s | 4.0s | 7,861 | 57.6 | 121s bash {"command":"python3 -m pytest -q 2>&1 | tail -5"} |
| task-9 | completed | 20 | 3.9m | 1.7m (43%) | 2.2m (57%) | 5.1s | 6.6s | 3,386 | 71.8 | 121s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-10 | completed | 30 | 6.9m | 1.7m (25%) | 5.2m (75%) | 3.5s | 10.3s | 5,161 | 64.7 | 183s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-11 | completed | 37 | 7.9m | 3.2m (41%) | 4.7m (59%) | 5.2s | 7.6s | 5,223 | 45.7 | 277s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-12 | completed | 28 | 6.0m | 1.3m (22%) | 4.7m (78%) | 2.8s | 10.1s | 5,290 | 46.8 | 276s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
| task-13 | model_error | 29 | 7.7m | 1.5m (19%) | 6.2m (81%) | 3.0s | 12.8s | 0 | 0.0 | 127s bash {"command":"python3 -m pytest tests/test_bench.py -q"} |
| task-14 | completed | 41 | 13.7m | 3.0m (22%) | 10.7m (78%) | 4.4s | 15.6s | 6,108 | 67.3 | 183s bash {"command":"cd /Users/jimschneider/repos/dirtywork/.worktree |
