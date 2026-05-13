"""Pull Requests sidebar plugin -- frontend-only.

The PR widget aggregates data from existing core endpoints
(`/api/live-stats`, `/api/workstats`) and renders it in the Eva
sidebar. There's no plugin-specific backend code; the only reason
this folder exists is so the plugin shows up in the registry, gets
its enable toggle seeded, and the Settings UI can list it
alongside the other plugins instead of treating it as a magic
hardcoded entry.

Future iteration: if PR-widget data ever needs aggregation that
doesn't fit the live-stats / workstats endpoints, add a
`/api/plugins/pr` route here.
"""

from common import plugins as _plugin_registry


class PRPlugin:
    id = "pr"
    name = "Pull Requests"

    def register(self, app):
        # No routes; the frontend talks to /api/live-stats +
        # /api/workstats which are core endpoints.
        pass

    def start_jobs(self, scheduler):
        # No periodic work; the sidebar fetches on demand.
        pass


_plugin_registry.register(PRPlugin())
