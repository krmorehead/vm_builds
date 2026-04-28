"""UI constants shared by page modules and tests.

Pure string/int constants with zero external dependencies.
Extracted from data.py for single-responsibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class Routes:
    """URL paths for all application pages."""
    DASHBOARD = "/"
    HUB = "/hub"
    BRIDGE = "/bridge"
    MESH = "/mesh"
    ROUTER = "/router"
    SERVICES = "/services"
    DEPLOY = "/deploy"
    IMAGES = "/images"
    NODES = "/nodes"
    NODE_DETAIL = "/nodes/{hostname}"
    HOSTS = "/hosts"
    ENVIRONMENT = "/environment"
    CONTAINERS = "/containers"
    WIREGUARD = "/wireguard"
    LOGS = "/logs"
    FLEET = "/fleet"
    FLEET_DETAIL = "/fleet/{node_id}"
    LAUNCH = "/launch"
    TIMELINE = "/timeline"
    VIEW = "/view"
    REMOTE_KIOSK = "/remote/{node_id}"
    CONSOLE = "/console/{node_id}/{app_id}"


class ApiRoutes:
    """REST API paths for internal and external calls."""
    CHECKIN = "/api/checkin"
    NODES = "/api/nodes"
    FLEET_READY = "/api/fleet/ready"
    FLEET_STALE = "/api/fleet/stale"
    FLEET_HEALTH = "/api/fleet/health"
    FLEET_VERSIONS = "/api/fleet/versions"
    CONTAINER_READY = "/api/container/{container_id}/ready"
    EVENTS = "/api/events"
    TIMELINE_START = "/api/timeline/start"
    TIMELINE_STOP = "/api/timeline/stop"
    TIMELINE_CURRENT = "/api/timeline/current"
    HOST_REGISTER = "/api/hosts/register"
    DISPLAY_ENTER = "/api/display/{app_id}/enter"
    DISPLAY_EXIT = "/api/display/{app_id}/exit"
    DISPLAY_STATUS = "/api/display/{app_id}/status"
    DISPLAY_LIST = "/api/display/list"
    GUESTS = "/api/guests"
    GUEST_ACTION = "/api/guests/{vmid}/{action}"
    WIFI_MODE = "/api/wifi/mode/{node}/{mode}"
    WIFI_STATUS = "/api/wifi/status/{node}"
    WIFI_STATUS_ALL = "/api/wifi/status"
    BATMAN_ENABLE = "/api/batman/enable"
    BATMAN_DISABLE = "/api/batman/disable"
    BATMAN_STATUS = "/api/batman/status"
    BRIDGE_RESTART_WIFI = "/api/bridge/restart-wifi"
    HEARTBEAT_SUBSCRIBE = "/api/heartbeat/subscribe"
    HEARTBEAT_SUBSCRIPTION = "/api/heartbeat/subscribe/{subscription_id}"
    HEARTBEAT_METRIC = "/api/heartbeat/{node_id}/{metric_type}"
    HEARTBEAT_SUBSCRIPTIONS = "/api/heartbeat/subscriptions"


class PageTitles:
    """Primary heading text for each page."""
    DASHBOARD = "vm_builds"
    HUB = "Home Hub"
    BRIDGE = "WiFi Bridge"
    MESH = "Mesh Network"
    ROUTER = "Router"
    SERVICES = "Service Selection"
    DEPLOY = "Deploy"
    IMAGES = "Image Management"
    NODES = "Fleet Nodes"
    NODE_DETAIL = "Node Detail"
    HOSTS = "Host Connectivity"
    ENVIRONMENT = "Environment"
    CONTAINERS = "Containers & VMs"
    TIMELINE = "Deploy Timeline"
    CLUSTER_FLEET = "Cluster Fleet"
    WIREGUARD = "WireGuard VPN"
    LOGS = "Centralized Logs"
    VIEWER = "Service Viewer"


class Labels:
    """Shared button text and status messages used across pages and tests."""
    APP_TITLE = "vm_builds"
    HOME = "Home"
    HOME_HUB = "Home Hub"
    BACK_TO_HUB = "Back to Hub"
    FULL_DEPLOY = "Full Deploy"
    BUILD_IMAGES = "Build Images"
    CHECK_HOSTS = "Check Hosts"
    VIEW_FLEET = "View Fleet Dashboard"
    START_DEPLOY = "Start Deploy"
    CANCEL = "Cancel"
    SELECT_ALL = "Select All"
    DESELECT_ALL = "Deselect All"
    DEPLOY_SELECTED = "Deploy Selected"
    REFRESH = "Refresh"
    REFRESH_NOW = "Refresh Now"
    VALIDATE = "Validate"
    SAVE = "Save"
    CREATE_ENV = "Create .env"
    PROBE_ALL = "Probe All"
    TEST_API = "Test API"
    BUILD_SELECTED = "Build Selected"
    BUILD_ALL = "Build All"
    DEPLOY_BRIDGE = "Deploy Bridge"
    RESTART_WIFI = "Restart WiFi"
    FORCE_REPAIR = "Force Re-pair"
    SWAP_ROLES = "Swap Roles"
    BRIDGE_HOW_IT_WORKS = "How It Works"
    BRIDGE_STEP_DEPLOY = "Deploy"
    BRIDGE_STEP_NEGOTIATE = "Negotiate"
    BRIDGE_STEP_PAIR = "Pair"
    ENABLE_BATMAN = "Enable Batman"
    DISABLE_BATMAN = "Disable Batman"
    REFRESH_STATUS = "Refresh Status"
    CONTAINERS = "Containers"
    NOT_AVAILABLE = "Not available"
    NO_TAGS_SELECTED = "No tags selected"
    NO_VMID = "No VMID configured"
    NO_URL = "No URL configured"
    NO_SERVICES = "No services selected"
    SELECT_ROW = "Select a row first"
    SELECT_IMAGE = "Select an image to build"
    NODE_STATUS = "Node Status"
    FLEET_HEALTH = "Fleet Health"
    SERVICE_MATRIX = "Service Matrix"
    ADD_HOST = "Add Host"
    REGISTER = "Register"
    BUCKET_TEST = "Test Units"
    BUCKET_LAB = "Lab Units"
    BUCKET_PRODUCTION = "Production"
    OPEN_KIOSK = "Open Kiosk"
    KIOSK_NOT_REACHABLE = "Kiosk not reachable"
    DRILL_INTO = "Drill into"
    LAUNCH_PREFIX = "Launch"
    CONSOLE_SUFFIX = "Console"
    NO_VMID_CONFIGURED = "No VMID configured for this app"
    MANAGER_NOT_INITIALIZED = "Manager not initialized"
    HOST_UNREACHABLE = "Cannot reach host"
    NO_HANDLER = "No handler registered"
    VIEW_CONSOLE_FROM_MANAGER = (
        "started. View the console from the Manager or SuperManager fleet page."
    )
    GO_BACK = "Go Back"
    BACK_TO_FLEET = "Back to Fleet"
    NOT_CLUSTER_MANAGER = "Not a Cluster Manager"
    NOT_FOUND_IN_CLUSTER = "Not found in cluster"
    NO_URL_FOR_SERVICE = "No URL configured for this service"
    AUTO_REFRESH = "Auto-refresh (5s)"


class Ports:
    """Well-known port numbers for fleet services."""
    MANAGER = 9001
    CALLHOME_CMD = 9002
    WIREGUARD = 51820
    SUPERMANAGER = 52500
    KIOSK_DISPLAY = 6080
    DESKTOP_DISPLAY = 6081
    KODI_DISPLAY = 6082
    MOONLIGHT_DISPLAY = 6083
    LAN_DISPLAY_RELAY_OFFSET = 100
    JELLYFIN = 8096
    KODI_WEB = 8080
    HOMEASSISTANT = 8123
    SUNSHINE_WEB = 47990
    NETDATA = 19999


class ManagerDefaults:
    """Operational constants for the manager hierarchy."""
    HEARTBEAT_VERSION = "1.0"
    PROVISION_DEADLINE_SECONDS = 600
    FILE_CHUNK_SIZE = 30_000


class VMIDs:
    """Well-known VMIDs mirroring inventory/group_vars/all.yml.

    Single source of truth for Python code. Ansible reads from all.yml;
    Python reads from here. Keep in sync when adding services.
    """
    ROUTER_VM = 100
    WIREGUARD_CT = 101
    PIHOLE_CT = 102
    MESH_CT = 103
    BRIDGE_CT = 104
    HOMEASSISTANT_CT = 200
    JELLYFIN_CT = 300
    KODI_CT = 301
    MOONLIGHT_CT = 302
    DESKTOP_CT = 400
    KIOSK_CT = 401
    NETDATA_CT = 500
    RSYSLOG_CT = 501
    GAMING_VM = 600
    GAMING_CT = 601


class NetworkAddresses:
    """Well-known IP addresses and hostnames for fleet services."""
    ROUTER_VM_LAN_IP = "10.10.10.1"
    CLUSTER_MANAGER_HOST = "home"


@dataclass
class DisplayAppConfig:
    """Static configuration for a display app in the handler registry."""
    app_id: str
    handler_type: str
    display_port: int = 0
    ct_id: str = ""
    service_port: int = 0
    service_path: str = "/"
    conflicts: list[str] = field(default_factory=list)
    label: str = ""
    icon: str = ""
    description: str = ""
    target_hosts: list[str] = field(default_factory=list)


NavItem = tuple[str, str, str]  # (label, path, icon)

NAV_SECTIONS: list[NavItem] = [
    ("Dashboard", Routes.DASHBOARD, "dashboard"),
    ("Home Hub", Routes.HUB, "tv"),
    ("Bridge", Routes.BRIDGE, "swap_horiz"),
    ("Mesh", Routes.MESH, "hub"),
    ("Router", Routes.ROUTER, "router"),
    ("Services", Routes.SERVICES, "widgets"),
    ("Deploy", Routes.DEPLOY, "rocket_launch"),
    ("Images", Routes.IMAGES, "inventory_2"),
    ("Nodes", Routes.NODES, "device_hub"),
    ("Hosts", Routes.HOSTS, "dns"),
    ("Environment", Routes.ENVIRONMENT, "settings"),
]

CLUSTER_NAV_SECTIONS: list[NavItem] = [
    ("Fleet", Routes.FLEET, "device_hub"),
    ("Home Hub", Routes.HUB, "tv"),
    ("Bridge", Routes.BRIDGE, "swap_horiz"),
    ("Mesh", Routes.MESH, "hub"),
    ("Router", Routes.ROUTER, "router"),
    ("Containers", Routes.CONTAINERS, "view_in_ar"),
]

KIOSK_NAV_ITEMS: list[NavItem] = [
    ("Fleet", Routes.FLEET, "device_hub"),
    ("Bridge", Routes.BRIDGE, "swap_horiz"),
    ("Mesh", Routes.MESH, "hub"),
    ("Router", Routes.ROUTER, "router"),
    ("Containers", Routes.CONTAINERS, "view_in_ar"),
]
