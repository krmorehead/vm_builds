"""Shared metric subscription controller for bridge/mesh/router pages.

Extracts the subscribe → poll → cache-read pipeline that every metric
page repeats. Page modules provide node IDs, metric name, and rendering
callbacks; the controller handles timer lifecycle and cache plumbing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from nicegui import ui

if TYPE_CHECKING:
    from scripts.webui.manager import CachedMetric


class MetricPageController:
    """Manages heartbeat subscriptions and periodic refresh for a metric page.

    Parameters
    ----------
    metric_name:
        Cache key passed to ``subscribe()`` and ``cache.get()``
        (e.g. ``"bridge"``, ``"mesh"``).
    node_ids:
        Static list of node identifiers to subscribe to.
    on_refresh:
        Called with ``dict[node_id, CachedMetric]`` each tick.
    refresh_interval:
        Seconds between automatic refreshes (default 5).
    ttl_seconds:
        Subscription TTL passed to the subscription manager (default 30).
    """

    def __init__(
        self,
        metric_name: str,
        node_ids: list[str],
        *,
        on_refresh: Callable[[dict[str, CachedMetric]], None],
        refresh_interval: float = 5.0,
        ttl_seconds: int = 30,
    ) -> None:
        self.metric_name = metric_name
        self.node_ids = node_ids
        self._on_refresh = on_refresh
        self.refresh_interval = refresh_interval
        self.ttl_seconds = ttl_seconds

    def subscribe_all(self) -> None:
        """Subscribe to heartbeat metrics for all configured nodes."""
        from scripts.webui.manager import get_subscription_manager, resolve_node_ip

        mgr = get_subscription_manager()
        for node_id in self.node_ids:
            ip = resolve_node_ip(node_id)
            if ip:
                mgr.subscribe(node_id, self.metric_name, ttl_seconds=self.ttl_seconds)

    def collect(self) -> dict[str, CachedMetric]:
        """Return cached metrics for all nodes, subscribing first."""
        from scripts.webui.manager import get_metric_cache

        self.subscribe_all()
        cache = get_metric_cache()
        return {
            node_id: cache.get(node_id, self.metric_name)
            for node_id in self.node_ids
        }

    def refresh(self) -> None:
        """Subscribe, collect, and invoke the rendering callback."""
        node_data = self.collect()
        self._on_refresh(node_data)

    def start_timer(self) -> ui.timer:
        """Create a NiceGUI timer that calls ``refresh()`` at the configured interval."""
        self.refresh()
        return ui.timer(self.refresh_interval, self.refresh)
