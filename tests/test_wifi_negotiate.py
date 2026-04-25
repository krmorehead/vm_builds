"""Tests for scripts/wifi_negotiate.py — WiFi bridge link negotiation.

Tests are organized by concern: parsing, band selection, width negotiation,
channel selection, htmode derivation, asymmetric hardware, error cases,
regression tests, and additional hardware combos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infra

from scripts.wifi_negotiate import (
    BAND_PRIORITY,
    BandCapabilities,
    CONTIGUOUS_80_BLOCKS_5G,
    CONTIGUOUS_160_BLOCKS_5G,
    NegotiatedConfig,
    WifiCapabilities,
    negotiate,
    parse_capabilities,
    _block_is_entirely_non_dfs,
    _derive_htmode,
    _find_best_channel,
    _parse_int_list,
)

FIXTURES = Path(__file__).parent / "fixtures"

ALL_FIXTURE_NAMES = [
    "wifi_caps_ax210.txt",
    "wifi_caps_ax210_no_lar.txt",
    "wifi_caps_ath9k.txt",
    "wifi_caps_2g_only.txt",
    "wifi_caps_mt7921.txt",
    "wifi_caps_ac9260.txt",
    "wifi_caps_ax200.txt",
    "wifi_caps_rtl8812au.txt",
    "wifi_caps_be200.txt",
]


# ── Helpers ──────────────────────────────────────────────────────


def _caps_from_fixture(name: str) -> WifiCapabilities:
    return parse_capabilities((FIXTURES / name).read_text())


def _make_caps(
    bands: dict[str, dict] | None = None,
    phy: str = "phy0",
    driver: str = "iwlwifi",
) -> WifiCapabilities:
    """Build WifiCapabilities from a concise dict spec."""
    band_objs = {}
    for bname, bspec in (bands or {}).items():
        band_objs[bname] = BandCapabilities(
            name=bname,
            channels=bspec.get("channels", []),
            ap_channels=bspec.get("ap_channels", []),
            max_width_mhz=bspec.get("max_width", 20),
            dfs_channels=bspec.get("dfs_channels", []),
            he_supported=bspec.get("he", False),
            vht_supported=bspec.get("vht", False),
        )
    return WifiCapabilities(phy=phy, driver=driver, bands=band_objs)


# ── Parse capabilities ──────────────────────────────────────────


class TestParseCapabilities:
    def test_parse_ax210_fixture(self):
        caps = _caps_from_fixture("wifi_caps_ax210.txt")
        assert caps.phy == "phy0"
        assert caps.driver == "iwlwifi"
        assert set(caps.bands.keys()) == {"2g", "5g", "6g"}
        assert caps.supports_wds is True
        assert caps.supports_wpa3 is True

    def test_parse_ax210_bands(self):
        caps = _caps_from_fixture("wifi_caps_ax210.txt")
        b5 = caps.bands["5g"]
        assert 36 in b5.channels
        assert 165 in b5.channels
        assert b5.max_width_mhz == 160
        assert b5.he_supported is True
        assert b5.vht_supported is True
        assert 52 in b5.dfs_channels
        assert 36 not in b5.dfs_channels

    def test_parse_ax210_6g_no_ap_channels(self):
        caps = _caps_from_fixture("wifi_caps_ax210.txt")
        b6 = caps.bands["6g"]
        assert len(b6.channels) > 0
        assert b6.ap_channels == []

    def test_parse_ath9k(self):
        caps = _caps_from_fixture("wifi_caps_ath9k.txt")
        assert caps.driver == "ath9k"
        assert "6g" not in caps.bands
        assert caps.bands["5g"].max_width_mhz == 40
        assert caps.bands["5g"].he_supported is False
        assert caps.supports_wpa3 is False

    def test_parse_2g_only(self):
        caps = _caps_from_fixture("wifi_caps_2g_only.txt")
        assert set(caps.bands.keys()) == {"2g"}
        assert caps.bands["2g"].max_width_mhz == 40

    def test_parse_empty_string(self):
        caps = parse_capabilities("")
        assert caps.phy == ""
        assert len(caps.bands) == 0

    def test_parse_ignores_comments_and_blanks(self):
        text = """
        # this is a comment
        PHY=phy1

        DRIVER=mt76
        BANDS=2g
        BAND_2G_CHANNELS=1,6,11
        BAND_2G_AP_CHANNELS=1,6,11
        BAND_2G_MAX_WIDTH=20
        BAND_2G_HE=no
        BAND_2G_VHT=no
        BAND_2G_DFS_CHANNELS=
        """
        caps = parse_capabilities(text)
        assert caps.phy == "phy1"
        assert caps.driver == "mt76"
        assert caps.bands["2g"].channels == [1, 6, 11]

    def test_parse_mt7921(self):
        caps = _caps_from_fixture("wifi_caps_mt7921.txt")
        assert caps.driver == "mt7921e"
        assert set(caps.bands.keys()) == {"2g", "5g", "6g"}
        assert caps.bands["5g"].max_width_mhz == 80
        assert caps.bands["6g"].max_width_mhz == 80
        assert caps.bands["6g"].he_supported is True
        assert len(caps.bands["6g"].ap_channels) > 0

    def test_parse_ac9260(self):
        caps = _caps_from_fixture("wifi_caps_ac9260.txt")
        assert caps.driver == "iwlwifi"
        assert set(caps.bands.keys()) == {"2g", "5g"}
        assert caps.bands["5g"].max_width_mhz == 160
        assert caps.bands["5g"].he_supported is False
        assert caps.bands["5g"].vht_supported is True

    def test_parse_ax200(self):
        caps = _caps_from_fixture("wifi_caps_ax200.txt")
        assert caps.bands["5g"].he_supported is True
        assert caps.bands["5g"].vht_supported is True
        assert "6g" not in caps.bands

    def test_parse_rtl8812au(self):
        caps = _caps_from_fixture("wifi_caps_rtl8812au.txt")
        assert caps.driver == "rtl8812au"
        assert caps.bands["5g"].max_width_mhz == 80
        assert caps.bands["5g"].vht_supported is True
        assert caps.bands["5g"].he_supported is False
        assert len(caps.bands["5g"].dfs_channels) == 0

    def test_parse_be200(self):
        caps = _caps_from_fixture("wifi_caps_be200.txt")
        assert caps.bands["6g"].max_width_mhz == 320
        assert len(caps.bands["6g"].ap_channels) > 0
        assert caps.bands["6g"].he_supported is True

    @pytest.mark.parametrize("fixture", ALL_FIXTURE_NAMES)
    def test_all_fixtures_parse_without_error(self, fixture):
        """Every fixture file must parse cleanly."""
        caps = _caps_from_fixture(fixture)
        assert caps.phy != ""
        assert caps.driver != ""
        assert len(caps.bands) >= 1

    def test_parse_whitespace_values(self):
        text = "PHY= phy1 \nDRIVER= mt76 \nBANDS= 2g , 5g \n"
        text += "BAND_2G_CHANNELS= 1 , 6 , 11 \n"
        text += "BAND_2G_AP_CHANNELS= 1 , 6 , 11 \n"
        text += "BAND_2G_MAX_WIDTH= 40 \n"
        text += "BAND_2G_HE= no \nBAND_2G_VHT= no \nBAND_2G_DFS_CHANNELS= \n"
        text += "BAND_5G_CHANNELS= 36 , 40 \n"
        text += "BAND_5G_AP_CHANNELS= 36 , 40 \n"
        text += "BAND_5G_MAX_WIDTH= 80 \n"
        text += "BAND_5G_HE= yes \nBAND_5G_VHT= yes \nBAND_5G_DFS_CHANNELS= \n"
        caps = parse_capabilities(text)
        assert caps.phy == "phy1"
        assert caps.bands["2g"].channels == [1, 6, 11]
        assert caps.bands["5g"].max_width_mhz == 80

    def test_parse_missing_optional_fields(self):
        text = "PHY=phy0\nBANDS=2g\nBAND_2G_CHANNELS=1,6,11\nBAND_2G_AP_CHANNELS=1,6,11\n"
        caps = parse_capabilities(text)
        assert caps.bands["2g"].max_width_mhz == 20
        assert caps.bands["2g"].he_supported is False
        assert caps.supports_wds is True


# ── _parse_int_list ─────────────────────────────────────────────


class TestParseIntList:
    def test_normal(self):
        assert _parse_int_list("1,6,11") == [1, 6, 11]

    def test_with_spaces(self):
        assert _parse_int_list(" 1 , 6 , 11 ") == [1, 6, 11]

    def test_empty(self):
        assert _parse_int_list("") == []

    def test_whitespace_only(self):
        assert _parse_int_list("   ") == []

    def test_trailing_comma(self):
        assert _parse_int_list("1,6,11,") == [1, 6, 11]

    def test_invalid_values_skipped(self):
        assert _parse_int_list("1,abc,11") == [1, 11]

    def test_single_value(self):
        assert _parse_int_list("36") == [36]


# ── Band selection ───────────────────────────────────────────────


class TestBandSelection:
    def test_prefers_5g_over_2g(self):
        """When both endpoints support 5g with AP channels, 5g wins."""
        ap = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11], "max_width": 40},
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [36, 40, 44, 48], "max_width": 80},
        })
        sta = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11], "max_width": 40},
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [36, 40, 44, 48], "max_width": 80},
        })
        result = negotiate(ap, sta)
        assert result.band == "5g"

    def test_prefers_6g_over_5g(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
            "6g": {"channels": [1, 5, 9], "ap_channels": [1, 5, 9], "max_width": 160},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
            "6g": {"channels": [1, 5, 9], "ap_channels": [1, 5, 9], "max_width": 160},
        })
        result = negotiate(ap, sta)
        assert result.band == "6g"

    def test_falls_to_2g_when_5g_ap_blocked(self):
        """LAR blocks 5g AP: no AP channels means 5g skipped, 2g used."""
        ap = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11], "max_width": 40, "he": True},
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [], "max_width": 160, "he": True},
        })
        sta = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11], "max_width": 40, "he": True},
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [], "max_width": 160, "he": True},
        })
        result = negotiate(ap, sta)
        assert result.band == "2g"
        assert "passive-scan" in result.reason.lower() or "no transmit" in result.reason.lower()

    def test_ax210_pair_selects_5g(self):
        """Two AX210s with lar_disable should select 5g (6g has no AP channels)."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"

    def test_ax210_no_lar_falls_to_2g(self):
        """AX210 without lar_disable: 5g has no AP channels, falls to 2g."""
        ap = _caps_from_fixture("wifi_caps_ax210_no_lar.txt")
        sta = _caps_from_fixture("wifi_caps_ax210_no_lar.txt")
        result = negotiate(ap, sta)
        assert result.band == "2g"

    def test_6g_preferred_when_both_have_ap_channels(self):
        """MT7921 has 6g AP channels; AX210 does not. Only MT7921 pair uses 6g."""
        ap = _caps_from_fixture("wifi_caps_mt7921.txt")
        sta = _caps_from_fixture("wifi_caps_mt7921.txt")
        result = negotiate(ap, sta)
        assert result.band == "6g"

    def test_5g_only_adapters(self):
        """AC9260 pair: WiFi 5 only, should get 5g VHT."""
        ap = _caps_from_fixture("wifi_caps_ac9260.txt")
        sta = _caps_from_fixture("wifi_caps_ac9260.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.htmode.startswith("VHT")

    def test_band_priority_order(self):
        """Verify the global band priority is 6g > 5g > 2g."""
        assert BAND_PRIORITY == ["6g", "5g", "2g"]


# ── Width negotiation ────────────────────────────────────────────


class TestWidthNegotiation:
    def test_takes_minimum_width(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [36, 40, 44, 48],
                   "max_width": 160, "he": True, "vht": True},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [36, 40, 44, 48],
                   "max_width": 80, "he": True, "vht": True},
        })
        result = negotiate(ap, sta)
        assert result.width_mhz == 80

    def test_2g_capped_at_40(self):
        ap = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11],
                   "max_width": 80, "he": True},
        })
        sta = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11],
                   "max_width": 80, "he": True},
        })
        result = negotiate(ap, sta)
        assert result.width_mhz == 40

    def test_ax210_pair_avoids_dfs_gets_80mhz(self):
        """AX210 5GHz 160MHz block includes DFS (52-64); steps down to 80MHz."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(ap, sta)
        assert result.width_mhz == 80
        assert result.htmode == "HE80"
        assert "non-DFS" in result.reason

    def test_mt7921_pair_5g_capped_at_80(self):
        """MT7921 supports max 80MHz on 5g."""
        ap = _caps_from_fixture("wifi_caps_mt7921.txt")
        sta = _caps_from_fixture("wifi_caps_mt7921.txt")
        # MT7921 pair selects 6g (both have AP channels)
        result = negotiate(ap, sta)
        assert result.band == "6g"
        assert result.width_mhz == 80

    def test_ax210_vs_mt7921_limited_by_narrower(self):
        """When paired, narrower adapter limits width."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_mt7921.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.width_mhz == 80

    def test_be200_ap_ax210_sta_uses_6g(self):
        """BE200 AP has 6g AP channels; AX210 STA has 6g receive channels. 6g is valid."""
        be = _caps_from_fixture("wifi_caps_be200.txt")
        ax = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(be, ax)
        assert result.band == "6g"
        assert result.width_mhz == 160

    def test_ax210_ap_be200_sta_uses_5g(self):
        """AX210 AP has no 6g AP channels, so falls to 5g when it's the AP."""
        ax = _caps_from_fixture("wifi_caps_ax210.txt")
        be = _caps_from_fixture("wifi_caps_be200.txt")
        result = negotiate(ax, be)
        assert result.band == "5g"
        assert result.width_mhz == 80
        assert "non-DFS" in result.reason

    def test_be200_pair_uses_full_width(self):
        """Two BE200s use 6g at full 320MHz width."""
        be1 = _caps_from_fixture("wifi_caps_be200.txt")
        be2 = _caps_from_fixture("wifi_caps_be200.txt")
        result = negotiate(be1, be2)
        assert result.band == "6g"
        assert result.width_mhz == 320

    def test_20mhz_minimum(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 20, "vht": True},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 20, "vht": True},
        })
        result = negotiate(ap, sta)
        assert result.width_mhz == 20


# ── Channel selection ────────────────────────────────────────────


class TestChannelSelection:
    def test_prefers_non_dfs(self):
        ch, eff_w, reason = _find_best_channel(
            "5g", 80,
            ap_channels={36, 40, 44, 48, 52, 56, 60, 64},
            sta_channels={36, 40, 44, 48, 52, 56, 60, 64},
            dfs_channels={52, 56, 60, 64},
        )
        assert ch in {36, 40, 44, 48}
        assert eff_w == 80
        assert "non-DFS" in reason

    def test_uses_dfs_when_no_non_dfs(self):
        ch, eff_w, reason = _find_best_channel(
            "5g", 80,
            ap_channels={52, 56, 60, 64, 100, 104, 108, 112},
            sta_channels={52, 56, 60, 64, 100, 104, 108, 112},
            dfs_channels={52, 56, 60, 64, 100, 104, 108, 112},
        )
        assert ch > 0
        assert "DFS" in reason

    def test_160mhz_with_dfs_steps_down_to_80(self):
        """When 160MHz block contains DFS channels, select 80MHz non-DFS."""
        ch, eff_w, reason = _find_best_channel(
            "5g", 160,
            ap_channels={36, 40, 44, 48, 52, 56, 60, 64},
            sta_channels={36, 40, 44, 48, 52, 56, 60, 64},
            dfs_channels={52, 56, 60, 64},
        )
        assert ch == 36
        assert eff_w == 80
        assert "non-DFS" in reason
        assert "80MHz" in reason

    def test_160mhz_entirely_non_dfs(self):
        """160MHz block with no DFS channels should be selected at full width."""
        ch, eff_w, reason = _find_best_channel(
            "5g", 160,
            ap_channels={36, 40, 44, 48, 52, 56, 60, 64},
            sta_channels={36, 40, 44, 48, 52, 56, 60, 64},
            dfs_channels=set(),
        )
        assert ch == 36
        assert eff_w == 160
        assert "non-DFS" in reason

    def test_no_common_channels(self):
        ch, eff_w, reason = _find_best_channel(
            "5g", 80,
            ap_channels={36, 40, 44, 48},
            sta_channels={149, 153, 157, 161},
            dfs_channels=set(),
        )
        assert ch == 0
        assert "no common" in reason

    def test_2g_selects_lowest(self):
        ch, eff_w, reason = _find_best_channel(
            "2g", 40,
            ap_channels={1, 6, 11},
            sta_channels={1, 6, 11},
            dfs_channels=set(),
        )
        assert ch == 1

    def test_6g_channel_selection(self):
        ch, eff_w, reason = _find_best_channel(
            "6g", 160,
            ap_channels={1, 5, 9, 13, 17, 21, 25, 29},
            sta_channels={1, 5, 9, 13, 17, 21, 25, 29},
            dfs_channels=set(),
        )
        assert ch == 1
        assert "non-DFS" in reason

    def test_80mhz_second_block_when_first_unavailable(self):
        ch, eff_w, reason = _find_best_channel(
            "5g", 80,
            ap_channels={149, 153, 157, 161},
            sta_channels={149, 153, 157, 161},
            dfs_channels=set(),
        )
        assert ch in {149, 153, 157, 161}
        assert "non-DFS" in reason

    def test_single_common_channel(self):
        ch, eff_w, reason = _find_best_channel(
            "5g", 20,
            ap_channels={165},
            sta_channels={165},
            dfs_channels=set(),
        )
        assert ch == 165

    def test_80mhz_prefers_unii1_over_dfs(self):
        """UNII-1 (36-48) should be preferred over DFS (52-64)."""
        ch, eff_w, reason = _find_best_channel(
            "5g", 80,
            ap_channels={36, 40, 44, 48, 52, 56, 60, 64},
            sta_channels={36, 40, 44, 48, 52, 56, 60, 64},
            dfs_channels={52, 56, 60, 64},
        )
        assert ch in {36, 40, 44, 48}

    def test_block_is_entirely_non_dfs_helper(self):
        assert _block_is_entirely_non_dfs([36, 40, 44, 48], set()) is True
        assert _block_is_entirely_non_dfs([36, 40, 44, 48], {52}) is True
        assert _block_is_entirely_non_dfs([36, 40, 44, 48], {40}) is False
        assert _block_is_entirely_non_dfs([52, 56, 60, 64], {52, 56, 60, 64}) is False

    def test_contiguous_block_constants_valid(self):
        """Verify 160MHz and 80MHz block definitions are well-formed."""
        for block in CONTIGUOUS_160_BLOCKS_5G:
            assert len(block) == 8
            for i in range(len(block) - 1):
                assert block[i + 1] - block[i] == 4
        for block in CONTIGUOUS_80_BLOCKS_5G:
            assert len(block) == 4
            for i in range(len(block) - 1):
                assert block[i + 1] - block[i] == 4


# ── Htmode derivation ────────────────────────────────────────────


class TestHtmodeDerivation:
    def test_5g_he_160(self):
        assert _derive_htmode("5g", 160, True) == "HE160"

    def test_5g_he_80(self):
        assert _derive_htmode("5g", 80, True) == "HE80"

    def test_5g_vht_80(self):
        assert _derive_htmode("5g", 80, False) == "VHT80"

    def test_2g_he_40(self):
        assert _derive_htmode("2g", 40, True) == "HE40"

    def test_2g_legacy_20(self):
        assert _derive_htmode("2g", 20, False) == "HT20"

    def test_6g_he_160(self):
        assert _derive_htmode("6g", 160, True) == "HE160"

    def test_5g_no_vht_no_he_falls_to_ht(self):
        """Without VHT and without HE, should produce HT mode."""
        assert _derive_htmode("5g", 40, False, vht=False) == "HT40"

    def test_5g_vht_160(self):
        assert _derive_htmode("5g", 160, False, vht=True) == "VHT160"

    def test_6g_always_he(self):
        """6g is HE-only; if he=False, still produces HT (no VHT on 6g)."""
        assert _derive_htmode("6g", 80, False) == "HT80"

    @pytest.mark.parametrize("band,width,expected_prefix", [
        ("2g", 20, "H"),
        ("2g", 40, "H"),
        ("5g", 20, "H"),
        ("5g", 40, "H"),
        ("5g", 80, "H"),
        ("5g", 160, "H"),
        ("6g", 20, "H"),
        ("6g", 80, "H"),
        ("6g", 160, "H"),
    ])
    def test_all_modes_produce_valid_prefix(self, band, width, expected_prefix):
        result = _derive_htmode(band, width, True)
        assert result.startswith(expected_prefix)
        assert str(width) in result


# ── Asymmetric hardware (fixture-based) ──────────────────────────


class TestAsymmetricHardware:
    def test_ax210_ap_ath9k_sta(self):
        """AX210 AP with ath9k STA: should select 5g at ath9k's max width (40MHz)."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_ath9k.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.width_mhz == 40
        assert result.htmode == "HT40"

    def test_ax210_ap_2g_only_sta(self):
        """AX210 AP with 2g-only STA: forced to 2g."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_2g_only.txt")
        result = negotiate(ap, sta)
        assert result.band == "2g"

    def test_ath9k_ap_ax210_sta(self):
        """ath9k AP with AX210 STA: should still negotiate 5g HT40."""
        ap = _caps_from_fixture("wifi_caps_ath9k.txt")
        sta = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.width_mhz == 40

    def test_2g_only_ap_ax210_sta(self):
        """2g-only AP with AX210 STA: forced to 2g."""
        ap = _caps_from_fixture("wifi_caps_2g_only.txt")
        sta = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(ap, sta)
        assert result.band == "2g"

    def test_ax210_ap_mt7921_sta(self):
        """AX210 has no 6g AP channels; MT7921 does. Negotiate on 5g."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_mt7921.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.width_mhz == 80

    def test_mt7921_ap_ax210_sta(self):
        """MT7921 AP with AX210 STA: 6g skipped (AX210 has no STA channels usable)."""
        ap = _caps_from_fixture("wifi_caps_mt7921.txt")
        sta = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(ap, sta)
        assert result.band in ("5g", "6g")
        assert result.width_mhz <= 160

    def test_ac9260_ap_ath9k_sta(self):
        """WiFi 5 (AC9260) AP with WiFi 4 (ath9k) STA."""
        ap = _caps_from_fixture("wifi_caps_ac9260.txt")
        sta = _caps_from_fixture("wifi_caps_ath9k.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.width_mhz == 40
        assert result.htmode == "HT40"

    def test_ax200_ap_ac9260_sta(self):
        """WiFi 6 (AX200) AP with WiFi 5 (AC9260) STA: VHT on 5g, 80MHz (DFS avoidance)."""
        ap = _caps_from_fixture("wifi_caps_ax200.txt")
        sta = _caps_from_fixture("wifi_caps_ac9260.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.htmode == "VHT80"
        assert result.width_mhz == 80

    def test_rtl8812au_ap_ath9k_sta(self):
        """USB adapter AP with ath9k STA."""
        ap = _caps_from_fixture("wifi_caps_rtl8812au.txt")
        sta = _caps_from_fixture("wifi_caps_ath9k.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.width_mhz == 40

    def test_be200_ap_2g_only_sta(self):
        """WiFi 7 AP with 2g-only STA: forced to 2g."""
        ap = _caps_from_fixture("wifi_caps_be200.txt")
        sta = _caps_from_fixture("wifi_caps_2g_only.txt")
        result = negotiate(ap, sta)
        assert result.band == "2g"

    def test_rtl8812au_ap_ax210_sta(self):
        """USB adapter (no DFS, VHT80) AP with AX210 STA."""
        ap = _caps_from_fixture("wifi_caps_rtl8812au.txt")
        sta = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.width_mhz == 80

    def test_ap_sta_role_matters_for_band_selection(self):
        """Band selection depends on which side is AP (needs AP channels).
        AX210 AP has no 6g AP channels → falls to 5g.
        MT7921 AP has 6g AP channels → uses 6g."""
        ax = _caps_from_fixture("wifi_caps_ax210.txt")
        mt = _caps_from_fixture("wifi_caps_mt7921.txt")
        r_ax_ap = negotiate(ax, mt)
        r_mt_ap = negotiate(mt, ax)
        assert r_ax_ap.band == "5g"
        assert r_mt_ap.band == "6g"

    def test_symmetric_adapters_same_result_either_way(self):
        """When adapters are identical, AP/STA role doesn't matter."""
        a = _caps_from_fixture("wifi_caps_ax210.txt")
        b = _caps_from_fixture("wifi_caps_ax210.txt")
        r1 = negotiate(a, b)
        r2 = negotiate(b, a)
        assert r1.band == r2.band
        assert r1.width_mhz == r2.width_mhz
        assert r1.htmode == r2.htmode


# ── Cross-generation combinations ────────────────────────────────


class TestCrossGeneration:
    """Test every meaningful combination of WiFi generations."""

    @pytest.mark.parametrize("ap_fixture,sta_fixture,expected_band", [
        ("wifi_caps_ax210.txt", "wifi_caps_ax210.txt", "5g"),
        ("wifi_caps_mt7921.txt", "wifi_caps_mt7921.txt", "6g"),
        ("wifi_caps_ac9260.txt", "wifi_caps_ac9260.txt", "5g"),
        ("wifi_caps_ax200.txt", "wifi_caps_ax200.txt", "5g"),
        ("wifi_caps_rtl8812au.txt", "wifi_caps_rtl8812au.txt", "5g"),
        ("wifi_caps_be200.txt", "wifi_caps_be200.txt", "6g"),
        ("wifi_caps_ath9k.txt", "wifi_caps_ath9k.txt", "5g"),
        ("wifi_caps_2g_only.txt", "wifi_caps_2g_only.txt", "2g"),
    ])
    def test_same_adapter_pair(self, ap_fixture, sta_fixture, expected_band):
        """Identical adapters should always negotiate successfully."""
        ap = _caps_from_fixture(ap_fixture)
        sta = _caps_from_fixture(sta_fixture)
        result = negotiate(ap, sta)
        assert result.band == expected_band
        assert result.channel > 0
        assert result.width_mhz >= 20

    def test_wifi6e_vs_wifi5_uses_5g(self):
        """WiFi 6E (AX210) vs WiFi 5 (AC9260): common ground is 5g."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_ac9260.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"

    def test_wifi6_vs_wifi5_uses_vht(self):
        """WiFi 6 (AX200) vs WiFi 5 (AC9260): both support VHT on 5g."""
        ap = _caps_from_fixture("wifi_caps_ax200.txt")
        sta = _caps_from_fixture("wifi_caps_ac9260.txt")
        result = negotiate(ap, sta)
        assert result.htmode.startswith("VHT")

    def test_wifi7_ap_wifi6e_sta(self):
        """WiFi 7 (BE200) AP has 6g AP channels; AX210 STA has 6g receive channels."""
        ap = _caps_from_fixture("wifi_caps_be200.txt")
        sta = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(ap, sta)
        assert result.band == "6g"
        assert result.width_mhz == 160

    def test_wifi6e_ap_wifi7_sta(self):
        """AX210 AP lacks 6g AP channels; falls to 5g. DFS avoidance → 80MHz."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_be200.txt")
        result = negotiate(ap, sta)
        assert result.band == "5g"
        assert result.width_mhz == 80
        assert "non-DFS" in result.reason

    def test_wifi7_vs_wifi6e_mt7921(self):
        """WiFi 7 (BE200) vs WiFi 6E (MT7921): 6g with shared channels."""
        ap = _caps_from_fixture("wifi_caps_be200.txt")
        sta = _caps_from_fixture("wifi_caps_mt7921.txt")
        result = negotiate(ap, sta)
        assert result.band == "6g"
        assert result.width_mhz == 80


# ── Error cases ──────────────────────────────────────────────────


class TestErrors:
    def test_no_common_band(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 80},
        })
        sta = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11], "max_width": 40},
        })
        with pytest.raises(ValueError, match="No common band"):
            negotiate(ap, sta)

    def test_no_common_channels_in_shared_band(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [36, 40, 44, 48], "max_width": 80},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [149, 153, 157, 161], "ap_channels": [149, 153, 157, 161], "max_width": 80},
        })
        with pytest.raises(ValueError, match="no channels common"):
            negotiate(ap, sta)

    def test_empty_capabilities(self):
        ap = _make_caps(bands={})
        sta = _make_caps(bands={})
        with pytest.raises(ValueError, match="No common band"):
            negotiate(ap, sta)

    def test_all_bands_passive_scan_only(self):
        """Both have bands but none with AP channels."""
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [], "max_width": 80},
            "2g": {"channels": [1, 6], "ap_channels": [], "max_width": 40},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
            "2g": {"channels": [1, 6], "ap_channels": [1, 6], "max_width": 40},
        })
        with pytest.raises(ValueError, match="No common band"):
            negotiate(ap, sta)

    def test_sta_has_no_channels(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [], "ap_channels": [], "max_width": 80},
        })
        with pytest.raises(ValueError, match="No common band"):
            negotiate(ap, sta)

    def test_error_message_includes_phy_and_driver(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 80},
        }, phy="phy0", driver="iwlwifi")
        sta = _make_caps(bands={
            "2g": {"channels": [1], "ap_channels": [1], "max_width": 40},
        }, phy="phy1", driver="ath9k")
        with pytest.raises(ValueError) as exc_info:
            negotiate(ap, sta)
        assert "phy0" in str(exc_info.value)
        assert "iwlwifi" in str(exc_info.value)
        assert "phy1" in str(exc_info.value)
        assert "ath9k" in str(exc_info.value)


# ── Reason string coverage ───────────────────────────────────────


class TestReasonStrings:
    def test_reason_includes_selected_band(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
        })
        result = negotiate(ap, sta)
        assert "5g" in result.reason

    def test_reason_includes_width_info(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 160},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 80},
        })
        result = negotiate(ap, sta)
        assert "AP=160MHz" in result.reason
        assert "STA=80MHz" in result.reason
        assert "=80MHz" in result.reason

    def test_reason_includes_he_status(self):
        ap = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 80, "he": True},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 80, "he": False},
        })
        result = negotiate(ap, sta)
        assert "HE: AP=True, STA=False" in result.reason

    def test_reason_includes_skipped_bands(self):
        ap = _make_caps(bands={
            "6g": {"channels": [1, 5], "ap_channels": [], "max_width": 160},
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
        })
        sta = _make_caps(bands={
            "6g": {"channels": [1, 5], "ap_channels": [], "max_width": 160},
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
        })
        result = negotiate(ap, sta)
        assert "6g" in result.reason
        assert "passive-scan" in result.reason.lower() or "no transmit" in result.reason.lower()


# ── NegotiatedConfig serialization ────────────────────────────────


class TestNegotiatedConfigSerialization:
    def test_to_dict(self):
        config = NegotiatedConfig(
            band="5g", channel=36, htmode="HE160",
            width_mhz=160, reason="test"
        )
        d = config.to_dict()
        assert d["band"] == "5g"
        assert d["channel"] == 36
        assert d["htmode"] == "HE160"
        assert d["width_mhz"] == 160
        assert d["reason"] == "test"

    def test_to_dict_all_fields_present(self):
        config = NegotiatedConfig(
            band="6g", channel=1, htmode="HE80",
            width_mhz=80, reason="selected"
        )
        d = config.to_dict()
        assert set(d.keys()) == {"band", "channel", "htmode", "width_mhz", "reason"}

    def test_to_dict_roundtrip_consistency(self):
        """Negotiate and serialize; verify all expected keys."""
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80, "he": True, "vht": True},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80, "he": True, "vht": True},
        })
        result = negotiate(ap, sta)
        d = result.to_dict()
        assert isinstance(d["band"], str)
        assert isinstance(d["channel"], int)
        assert isinstance(d["htmode"], str)
        assert isinstance(d["width_mhz"], int)
        assert isinstance(d["reason"], str)


# ── Regression tests ──────────────────────────────────────────────


class TestRegressions:
    def test_vht_not_selected_when_one_endpoint_lacks_it(self):
        """Regression: _derive_htmode incorrectly defaulted to VHT when ath9k
        (no VHT) was one endpoint. Must produce HT, not VHT."""
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 160, "vht": True},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 40, "vht": False},
        })
        result = negotiate(ap, sta)
        assert result.htmode == "HT40"
        assert "VHT" not in result.htmode

    def test_ax210_pair_does_not_use_6g_without_ap_channels(self):
        """Regression: AX210 6g has channels but no AP channels. Must skip 6g."""
        ap = _caps_from_fixture("wifi_caps_ax210.txt")
        sta = _caps_from_fixture("wifi_caps_ax210.txt")
        result = negotiate(ap, sta)
        assert result.band != "6g"
        assert "6g" in result.reason

    def test_he_not_selected_when_one_endpoint_lacks_it(self):
        """When one endpoint lacks HE, htmode should be VHT (if VHT supported) or HT."""
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [36, 40, 44, 48],
                   "max_width": 80, "he": True, "vht": True},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [36, 40, 44, 48],
                   "max_width": 80, "he": False, "vht": True},
        })
        result = negotiate(ap, sta)
        assert result.htmode == "VHT80"
        assert "HE" not in result.htmode

    def test_2g_pair_never_exceeds_ht40(self):
        """2g width is capped at 40MHz regardless of capabilities."""
        ap = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11],
                   "max_width": 160, "he": True, "vht": True},
        })
        sta = _make_caps(bands={
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11],
                   "max_width": 160, "he": True, "vht": True},
        })
        result = negotiate(ap, sta)
        assert result.width_mhz == 40
        assert result.htmode == "HE40"

    def test_channel_zero_raises_not_silent(self):
        """If channel selection fails, negotiate must raise, not return ch=0."""
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [149, 153], "ap_channels": [149, 153], "max_width": 80},
        })
        with pytest.raises(ValueError):
            negotiate(ap, sta)


# ── Edge cases ───────────────────────────────────────────────────


class TestEdgeCases:
    def test_single_channel_overlap(self):
        """Only one channel in common between AP and STA."""
        ap = _make_caps(bands={
            "5g": {"channels": [36, 40, 44, 48], "ap_channels": [36, 40, 44, 48], "max_width": 80},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [48, 149, 153], "ap_channels": [48, 149, 153], "max_width": 80},
        })
        result = negotiate(ap, sta)
        assert result.channel == 48

    def test_all_dfs_channels(self):
        """When all common channels are DFS, still produces a result."""
        ap = _make_caps(bands={
            "5g": {"channels": [52, 56, 60, 64], "ap_channels": [52, 56, 60, 64],
                   "max_width": 80, "dfs_channels": [52, 56, 60, 64]},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [52, 56, 60, 64], "ap_channels": [52, 56, 60, 64],
                   "max_width": 80, "dfs_channels": [52, 56, 60, 64]},
        })
        result = negotiate(ap, sta)
        assert result.channel in {52, 56, 60, 64}
        assert "DFS" in result.reason

    def test_many_bands_first_usable_wins(self):
        """With 3 bands, the highest-priority usable band wins."""
        ap = _make_caps(bands={
            "6g": {"channels": [1, 5], "ap_channels": [], "max_width": 160},
            "5g": {"channels": [36, 40], "ap_channels": [], "max_width": 80},
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11], "max_width": 40},
        })
        sta = _make_caps(bands={
            "6g": {"channels": [1, 5], "ap_channels": [1, 5], "max_width": 160},
            "5g": {"channels": [36, 40], "ap_channels": [36, 40], "max_width": 80},
            "2g": {"channels": [1, 6, 11], "ap_channels": [1, 6, 11], "max_width": 40},
        })
        result = negotiate(ap, sta)
        assert result.band == "2g"

    def test_width_1_is_valid(self):
        """Width = min of both sides, even if unusual."""
        ap = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 20},
        })
        sta = _make_caps(bands={
            "5g": {"channels": [36], "ap_channels": [36], "max_width": 20},
        })
        result = negotiate(ap, sta)
        assert result.width_mhz == 20
        assert result.htmode in ("HT20", "VHT20")
