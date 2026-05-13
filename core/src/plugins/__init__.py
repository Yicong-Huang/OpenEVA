"""Open-source plugin namespace.

Each submodule under here is one plugin. Drop a `.py` in this
directory, define a class with `id` / `name` / `register(app)` /
`start_jobs(scheduler)`, and call `common.plugins.register(...)` at
module top level. `server.py` discovers the package on boot via
`common.plugins.discover("plugins")`; no central wiring file to keep
in sync.

Plugins shipped with the OSS install land here. Internal /
proprietary extension plugins (paid SaaS integrations, vendor-
specific tooling, etc.) live under a sibling extension namespace
(any folder containing an `extension.conf` marker) that the OSS
install can omit entirely.

See `common/plugins.py` for the framework + protocol details.
"""
