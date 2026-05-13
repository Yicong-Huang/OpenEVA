"""Back-compat shim. The Slack monitor moved to
`channels/slack/channel.py` as part of the channels-registry refactor.

This file aliases its own module entry in `sys.modules` to the new
location so existing `from services import slack_monitor` imports and
`patch("services.slack_monitor.X")` calls keep working through a
single shared module object. Update call sites to import from
`channels.slack.channel` directly when convenient; this shim can then
go away.
"""

import sys

from channels.slack import channel as _impl

# Replace this module's entry in sys.modules with the canonical
# implementation. Any subsequent `import services.slack_monitor` (or
# attribute lookups on the previously-cached module) resolve to the
# real channel module, so module-level state (`_token`, `_channels`,
# etc.) and patches stay coherent across both paths.
sys.modules[__name__] = _impl
