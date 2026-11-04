# ADHDashboard integration

The existing local ADHDashboard checkouts use a Python standard-library backend,
SQLite event/task boundary and calendar-led widget UI. Their documentation says
real Things/Linear sources are unconnected. This change provides and tests a
backend client and review helper here; it does not import source records into a
dashboard database, modify its manual task memberships or activate a demo executor.
No dashboard instance is claimed to be connected.

The related “Create ADHD Dashboard Routine” chat specifies Top 3, This Week and
Needs Review. It also says Kapture migration was not completed. This integration
never performs that migration or changes existing Things/Linear records. Do not
assume the existing cloud Linear review can reach this Mac automatically.

## Minimal backend attachment

Keep the token on the dashboard backend. Import `dashboard_client.py` with its
companion modules from this checkout (or package these reviewed files together).
Use the same Mac by default. For another trusted tailnet host, use private HTTPS
and provision the credential securely. Do not add credentials to SQLite/profile
JSON, browser localStorage, front-end JavaScript or any Git-tracked configuration.

```python
# Backend pseudocode, not an activated dashboard route:
from dashboard_client import ThingsClient, things_tasks, linear_task, combine_tasks, daily_review
from things_service import DEFAULT_TOKEN

client = ThingsClient(DEFAULT_TOKEN)
pages = [client.get('/v1/lists/today?limit=50'),
         client.get('/v1/lists/inbox?limit=50'),
         client.get('/v1/lists/upcoming?limit=50')]
# The real dashboard supplies its own existing read-only Linear source.
# Engineering-only scope should come from verified team/project identifiers.
rows = things_tasks(pages) + [linear_task(issue) for issue in linear_issues]
groups = combine_tasks(rows, aliases=reviewed_cross_source_links)
review = daily_review(groups)
# Render private response in memory; Cache-Control: no-store; no body/request logs.
```

`linear_issues` and `reviewed_cross_source_links` are caller inputs, not configured
sources in this repository. Linear normalization uses real `updatedAt`, `dueDate`,
priority and state type. Never infer an engineering team from an issue title.
If a source is unavailable, show it as unavailable separately from the other
source. Do not overwrite good results with an empty list or silently hide a
failure. Add `has_more`/partial-coverage information beside the review. Explicitly
page as needed with a caller-chosen page/time budget. To include work outside the
three built-in lists, page open projects and explicitly request their tasks.
No helper claims a complete inventory from the sample above.

## Identity and safe aggregation

Canonical task identity is `things:<id>` or `linear:<id>`. The same Things ID in
Inbox/Today/project pages is one task with combined memberships. Equal names do
not establish a duplicate. When a user has verified that one Linear issue and
one Things task represent the same commitment, an explicit alias can map
`linear:<id>` to `things:<id>`. One card then retains both source records and
links. Keep that private mapping outside Git; no migrations or deletions occur.
Without a verified link, show both sources rather than guess. Cross-source
deduplication cannot be automatic and reliable from titles alone.

Source cards are read-only projections. Never route their Complete/Postpone/
Close controls through the dashboard's existing local completion handler.
Suggestions such as “review for completion” are advice only. A source row must
not become a new accepted local task merely by viewing the projection. Preserve
existing manually chosen Today/Later/This week memberships independently.

## Review rules

- Today: at most three **candidates**, ranked by overdue deadline, deadline today,
  explicit importance, then actual Things Today membership. No arbitrary Inbox
  item is used merely to fill three slots. Overdue and immediate-attention arrays
  are also returned independently.
- This week: deadlines or activation days from today through local Sunday, plus
  explicitly important unscheduled work to consider scheduling. It does not
  reschedule anything or automatically edit dashboard memberships.
- Things importance is unknown unless supplied as an explicit `important_keys`
  preference. Linear priority 1/2 is explicit importance. No keywords, task age,
  client names or inferred priorities are used.
- Needs Review: at least 14 days since actual modification for Linear Started
  work, 30 days otherwise. Exclude future activation/deadline commitments.
  Missing timestamps on either linked source mean `activity_unknown`, never
  stale. Creation age is ignored. A modification timestamp is only a signal to
  review; it does not prove neglected work or meaningful progress.
- Project progress/history is returned as null. The available Things fields do
  not justify “project without meaningful progress.” A future verified activity
  source can add that signal; do not synthesize it from project age.

`daily_review` is deterministic and has no AI call, task writes or persistence.
It can run on an authenticated backend refresh or a local daily routine. No new
cloud routine is installed, and the existing ChatGPT/Linear routine is unchanged.
A private dashboard session should use a backend-only authenticated projection
route or private side panel. Do not publish this data through the existing
unauthenticated demo server. Before attaching a live instance, identify its
canonical checkout, session authentication and private-data boundary.

Tests exercise real localhost HTTP client/server integration, canonical IDs,
explicit cross-source aliasing, memberships, unknown modification data, future
scheduling and review rules using fictional records only.
