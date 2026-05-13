"""Channels: message-source integrations (Slack, Discord, ...).

Each subdirectory is a self-contained channel implementation that
registers itself with `common.channels` on import. The OSS install
ships with the slack subpackage; extensions can drop additional
channels under `<ext>/src/channels/<id>/`.

Default registry is empty -- a channel is "live" only when its
package gets imported, which happens at server boot via the
discovery hook in `server.py`.

See `common/channels.py` for the framework + protocol details.
"""
