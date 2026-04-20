#!/usr/bin/env python3
"""WiFi link negotiation for bridge backhaul optimization.

Pure-logic module that computes optimal WiFi parameters for a
point-to-point WDS link given the capabilities of both endpoints.

The negotiation finds the best shared configuration: highest common
band, widest common channel width, and best available channel that
both endpoints can use.

Usage (pytest):
    from scripts.wifi_negotiate import negotiate, parse_capabilities
    ap = parse_capabilities(ap_text)
    sta = parse_capabilities(sta_text)
    result = negotiate(ap, sta)

Usage (CLI):
    python3 scripts/wifi_negotiate.py --ap-file /tmp/ap.txt --sta-file /tmp/sta.txt
    python3 scripts/wifi_negotiate.py --ap 'PHY=phy0\nBANDS=2g,5g' --sta '...'
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BandCapabilities:
    """Capabilities for a single frequency band on one endpoint."""

    name: str
    channels: list[int] = field(default_factory=list)
    ap_channels: list[int] = field(default_factory=list)
    max_width_mhz: int = 20
    dfs_channels: list[int] = field(default_factory=list)
    he_supported: bool = False
    vht_supported: bool = False


@dataclass
class WifiCapabilities:
    """WiFi capabilities of a single endpoint (AP or STA)."""

    phy: str = ""
    driver: str = ""
    bands: dict[str, BandCapabilities] = field(default_factory=dict)
    supports_wds: bool = True
    supports_wpa3: bool = True


@dataclass
class NegotiatedConfig:
    """Result of negotiation between AP and STA endpoints."""

    band: str
    channel: int
    htmode: str
    width_mhz: int
    reason: str

    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "channel": self.channel,
            "htmode": self.htmode,
            "width_mhz": self.width_mhz,
            "reason": self.reason,
        }


BAND_PRIORITY = ["6g", "5g", "2g"]

CONTIGUOUS_160_BLOCKS_5G = [
    [36, 40, 44, 48, 52, 56, 60, 64],
    [100, 104, 108, 112, 116, 120, 124, 128],
]

CONTIGUOUS_80_BLOCKS_5G = [
    [36, 40, 44, 48],
    [52, 56, 60, 64],
    [100, 104, 108, 112],
    [116, 120, 124, 128],
    [132, 136, 140, 144],
    [149, 153, 157, 161],
]


def _parse_int_list(value: str) -> list[int]:
    """Parse comma-separated integers, ignoring blanks."""
    if not value.strip():
        return []
    result = []
    for part in value.split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return result


def parse_capabilities(text: str) -> WifiCapabilities:
    """Parse KEY=value output from wifi_setup.sh capabilities."""
    caps = WifiCapabilities()
    kvs: dict[str, str] = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        kvs[key.strip()] = value.strip()

    caps.phy = kvs.get("PHY", "")
    caps.driver = kvs.get("DRIVER", "")
    caps.supports_wds = kvs.get("SUPPORTS_WDS", "yes").lower() == "yes"
    caps.supports_wpa3 = kvs.get("SUPPORTS_WPA3", "yes").lower() == "yes"

    band_names = [b.strip() for b in kvs.get("BANDS", "").split(",") if b.strip()]
    for band_name in band_names:
        prefix = f"BAND_{band_name.upper()}_"
        bc = BandCapabilities(
            name=band_name,
            channels=_parse_int_list(kvs.get(f"{prefix}CHANNELS", "")),
            ap_channels=_parse_int_list(kvs.get(f"{prefix}AP_CHANNELS", "")),
            max_width_mhz=int(kvs.get(f"{prefix}MAX_WIDTH", "20") or "20"),
            dfs_channels=_parse_int_list(kvs.get(f"{prefix}DFS_CHANNELS", "")),
            he_supported=kvs.get(f"{prefix}HE", "no").lower() == "yes",
            vht_supported=kvs.get(f"{prefix}VHT", "no").lower() == "yes",
        )
        caps.bands[band_name] = bc

    return caps


def _derive_htmode(band: str, width_mhz: int, he: bool, vht: bool = True) -> str:
    """Derive the OpenWrt htmode string from band, width, and HE/VHT support.

    OpenWrt uses HE* for both WiFi 6 (HE) and WiFi 7 (EHT) modes.
    The hostapd/mac80211 stack handles the actual protocol negotiation.
    """
    if he:
        return f"HE{width_mhz}"
    if band == "5g" and vht:
        return f"VHT{width_mhz}"
    return f"HT{width_mhz}"


def _block_is_entirely_non_dfs(block: list[int], dfs_channels: set[int]) -> bool:
    """True if no channel in the block requires DFS/radar detection."""
    return not (set(block) & dfs_channels)


def _find_best_channel(
    band: str,
    width_mhz: int,
    ap_channels: set[int],
    sta_channels: set[int],
    dfs_channels: set[int],
) -> tuple[int, int, str]:
    """Select the best channel and effective width for the given band.

    Returns (channel, effective_width_mhz, reason).
    Prefers the widest non-DFS configuration. When a wider block would
    require DFS channels (60-second CAC delay), automatically steps down
    to a narrower width that avoids DFS. Falls back to DFS only when no
    non-DFS option exists at any width.
    """
    common = ap_channels & sta_channels
    if not common:
        return 0, 0, "no common channels"

    non_dfs_common = common - dfs_channels
    dfs_common = common & dfs_channels

    # Try widths from widest to narrowest, non-DFS first
    if band == "5g":
        # Pass 1: non-DFS blocks (widest first)
        if width_mhz >= 160:
            for block in CONTIGUOUS_160_BLOCKS_5G:
                block_set = set(block)
                primary = block_set & non_dfs_common
                if primary and _block_is_entirely_non_dfs(block, dfs_channels):
                    ch = min(primary)
                    return ch, 160, f"non-DFS 160MHz block at ch{block[0]}"

        if width_mhz >= 80:
            for block in CONTIGUOUS_80_BLOCKS_5G:
                block_set = set(block)
                primary = block_set & non_dfs_common
                if primary and _block_is_entirely_non_dfs(block, dfs_channels):
                    ch = min(primary)
                    return ch, 80, f"non-DFS 80MHz block at ch{block[0]}"

        # 40 and 20 MHz non-DFS
        if non_dfs_common:
            ch = min(non_dfs_common)
            eff_w = min(width_mhz, 40)
            return ch, eff_w, f"non-DFS {eff_w}MHz ch{ch}"

        # Pass 2: DFS blocks as last resort (widest first)
        if width_mhz >= 160:
            for block in CONTIGUOUS_160_BLOCKS_5G:
                primary = set(block) & common
                if primary:
                    ch = min(primary)
                    return ch, 160, f"DFS 160MHz block at ch{block[0]} (requires 60s CAC)"

        if width_mhz >= 80:
            for block in CONTIGUOUS_80_BLOCKS_5G:
                primary = set(block) & common
                if primary:
                    ch = min(primary)
                    return ch, 80, f"DFS 80MHz block at ch{block[0]} (requires 60s CAC)"

        if dfs_common:
            ch = min(dfs_common)
            return ch, min(width_mhz, 40), f"DFS ch{ch} (non-DFS unavailable)"

    # Non-5GHz or general fallback
    if non_dfs_common:
        ch = min(non_dfs_common)
        return ch, width_mhz, f"non-DFS ch{ch}"

    if dfs_common:
        ch = min(dfs_common)
        return ch, width_mhz, f"DFS ch{ch} (non-DFS unavailable)"

    ch = min(common)
    return ch, width_mhz, f"ch{ch}"


def negotiate(ap: WifiCapabilities, sta: WifiCapabilities) -> NegotiatedConfig:
    """Compute optimal shared link parameters for AP and STA.

    Raises ValueError if no common configuration is possible.
    """
    reasons: list[str] = []

    selected_band = None
    for band_name in BAND_PRIORITY:
        ap_band = ap.bands.get(band_name)
        sta_band = sta.bands.get(band_name)
        if not ap_band or not sta_band:
            continue
        if not ap_band.ap_channels:
            reasons.append(
                f"{band_name}: AP has no transmit-capable channels (passive-scan only)"
            )
            continue
        if not sta_band.channels:
            reasons.append(f"{band_name}: STA has no channels")
            continue
        common_ap_sta = set(ap_band.ap_channels) & set(sta_band.channels)
        if not common_ap_sta:
            reasons.append(f"{band_name}: no channels common to AP and STA")
            continue
        selected_band = band_name
        break

    if not selected_band:
        raise ValueError(
            f"No common band available between AP ({ap.phy}/{ap.driver}) "
            f"and STA ({sta.phy}/{sta.driver}). "
            + "; ".join(reasons)
        )

    ap_band = ap.bands[selected_band]
    sta_band = sta.bands[selected_band]

    width = min(ap_band.max_width_mhz, sta_band.max_width_mhz)
    if selected_band == "2g":
        width = min(width, 40)

    both_he = ap_band.he_supported and sta_band.he_supported
    both_vht = ap_band.vht_supported and sta_band.vht_supported
    if not both_he and width > 160:
        width = 160

    dfs = set(ap_band.dfs_channels) | set(sta_band.dfs_channels)
    channel, effective_width, ch_reason = _find_best_channel(
        selected_band,
        width,
        set(ap_band.ap_channels),
        set(sta_band.channels),
        dfs,
    )

    if channel == 0:
        raise ValueError(
            f"Band {selected_band} selected but {ch_reason}. "
            f"AP channels: {ap_band.ap_channels}, STA channels: {sta_band.channels}"
        )

    # Use the effective width from channel selection (may be narrower
    # than hardware max to avoid DFS startup delay)
    width = effective_width
    htmode = _derive_htmode(selected_band, width, both_he, both_vht)

    skip_reasons = "; ".join(reasons) if reasons else "none skipped"
    reason = (
        f"Selected {selected_band} {htmode} ch{channel} ({ch_reason}). "
        f"Width: min(AP={ap_band.max_width_mhz}MHz, STA={sta_band.max_width_mhz}MHz)={width}MHz. "
        f"HE: AP={ap_band.he_supported}, STA={sta_band.he_supported}. "
        f"Skipped bands: {skip_reasons}."
    )

    return NegotiatedConfig(
        band=selected_band,
        channel=channel,
        htmode=htmode,
        width_mhz=width,
        reason=reason,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Negotiate optimal WiFi bridge link parameters"
    )
    parser.add_argument("--ap", help="AP capabilities as KEY=value text")
    parser.add_argument("--sta", help="STA capabilities as KEY=value text")
    parser.add_argument("--ap-file", help="Path to AP capabilities file")
    parser.add_argument("--sta-file", help="Path to STA capabilities file")

    args = parser.parse_args()

    ap_text = args.ap
    sta_text = args.sta

    if args.ap_file:
        ap_text = Path(args.ap_file).read_text()
    if args.sta_file:
        sta_text = Path(args.sta_file).read_text()

    if not ap_text or not sta_text:
        parser.error("Both --ap/--ap-file and --sta/--sta-file are required")

    ap_text = ap_text.replace("\\n", "\n")
    sta_text = sta_text.replace("\\n", "\n")

    ap_caps = parse_capabilities(ap_text)
    sta_caps = parse_capabilities(sta_text)

    try:
        result = negotiate(ap_caps, sta_caps)
        print(json.dumps(result.to_dict(), indent=2))
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
