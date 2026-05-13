"""Slack channel package. Importing this triggers `channel.py` which
registers the SlackChannel with `channels`."""

from . import channel  # noqa: F401 -- side-effect: registration
