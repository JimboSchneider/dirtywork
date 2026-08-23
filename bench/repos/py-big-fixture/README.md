# fixtures/rows.csv schema

Create `fixtures/rows.csv`: a header row followed by exactly 400 data rows,
one per customer, columns in this exact order:

`id,name,email,plan,created_at,balance`

- `id` -- integer, 1 through 400 inclusive, strictly increasing, one per
  row (row N of the data holds id N).
- `name` -- `User<id>`, e.g. `User1`, `User42`, `User400`.
- `email` -- lowercase `name` plus `@example.com`, e.g. `user1@example.com`.
- `plan` -- one of `free`, `pro`, `enterprise`, cycling by `id % 3`:
  remainder 1 -> `free`, remainder 2 -> `pro`, remainder 0 -> `enterprise`.
- `created_at` -- an ISO-8601 date `YYYY-MM-DD`, starting at `2024-01-01`
  for id 1 and advancing by one day per id (id 2 -> `2024-01-02`, ...,
  id 400 -> `2025-02-03`).
- `balance` -- a decimal amount with exactly two digits after the point:
  `id * 3.33`, rounded to two decimal places (id 1 -> `3.33`,
  id 2 -> `6.66`).

No blank lines, no quoting, no trailing blank line beyond the file's final
newline -- plain comma-separated values, UTF-8, LF line endings.
