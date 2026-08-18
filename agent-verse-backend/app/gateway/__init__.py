"""Universal Command Gateway — routes commands from any channel to the Org Brain.

Architecture:
  ANY CHANNEL → ChannelAdapter → OrgCommand → CommandRouter → OrgBrain
                                ← OrgResponse ← ResponseFormatter ←

Channels: REST, Telegram, Slack, WhatsApp, Discord, Email, Webhook, MCP, A2A, Teams
"""
from __future__ import annotations
