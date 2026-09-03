"""Tests for airmouse.screen_perception — layered ScreenModel engine.

Fully deterministic and headless: fake providers are injected for the
interesting cases, and the guarded platform providers must degrade to
empty results without raising when their tools are missing.
"""
from __future__ import annotations

import pytest

from airmouse.interfaces import (
    AppContext,
    ScreenModel,
    ScreenTarget,
    ScreenTargetType,
)
from airmouse.screen_perception import (
    AccessibilityProvider,
    GeometryProvider,
    OCRProvider,
    ScreenPerceptionEngine,
    dedupe_targets,
    iou,
    parse_app_name,
)


# ---------------------------------------------------------------------------
# Helpers / fakes (dependency injection)
# ---------------------------------------------------------------------------

class FixedProvider:
    """Fake provider yielding a fixed target list (always available)."""

    name = "fixed"
    available = True

    def __init__(self, targets, title=""):
        self._targets = list(targets)
        self.last_title = title

    def update(self, now):
        return list(self._targets)


class CountingProvider:
    """Fake provider counting update() calls (cache verification)."""

    name = "counting"
    available = True

    def __init__(self):
        self.calls = 0

    def update(self, now):
        self.calls += 1
        return []


def button(text="Submit Order", bbox=(500.0, 300.0, 120.0, 40.0), conf=0.95):
    return ScreenTarget(id="fake:submit", type=ScreenTargetType.BUTTON,
                        bbox=bbox, text=text, confidence=conf,
                        application="Shop", actionable=True, source="dom",
                        timestamp=0.0)


# ---------------------------------------------------------------------------
# GeometryProvider — deterministic decomposition
# ---------------------------------------------------------------------------

class TestGeometryProvider:
    def test_always_available_and_deterministic(self):
        g1 = GeometryProvider(1920, 1080)
        g2 = GeometryProvider(1920, 1080)
        assert g1.available is True
        ids1 = [t.id for t in g1.update(0.0)]
        ids2 = [t.id for t in g2.update(0.0)]
        assert ids1 == ids2
        assert len(ids1) == 9

    def test_expected_zone_ids(self):
        by_id = {t.id: t for t in GeometryProvider(1920, 1080).update(0.0)}
        expected = {"geo:center", "geo:corner_tl", "geo:corner_tr",
                    "geo:corner_bl", "geo:corner_br", "geo:edge_top",
                    "geo:edge_bottom", "geo:edge_left", "geo:edge_right"}
        assert expected == set(by_id)

    def test_center_zone_is_actionable_unknown(self):
        center = {t.id: t for t in GeometryProvider(1920, 1080).update(0.0)}["geo:center"]
        assert center.type is ScreenTargetType.UNKNOWN
        assert center.actionable is True
        assert center.contains(960, 540)
        assert center.bbox == (672.0, 378.0, 576.0, 324.0)  # middle 30%

    def test_corners_and_edges_not_actionable(self):
        by_id = {t.id: t for t in GeometryProvider(1920, 1080).update(0.0)}
        for tid in ("geo:corner_tl", "geo:corner_br", "geo:edge_top",
                    "geo:edge_right"):
            assert by_id[tid].actionable is False
            assert by_id[tid].type is ScreenTargetType.APP_REGION
        assert by_id["geo:corner_tl"].bbox == (0.0, 0.0, 480.0, 270.0)
        assert by_id["geo:edge_top"].bbox == (0.0, 0.0, 1920.0, 108.0)


# ---------------------------------------------------------------------------
# Pure helpers: iou / dedupe / parse_app_name
# ---------------------------------------------------------------------------

class TestPureHelpers:
    def test_iou_basics(self):
        assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
        assert iou((0, 0, 10, 10), (20, 20, 10, 10)) == 0.0
        assert iou((0, 0, 10, 10), (5, 0, 10, 10)) == pytest.approx(1.0 / 3.0)
        assert iou((0, 0, 0, 0), (0, 0, 5, 5)) == 0.0

    def test_dedupe_keeps_higher_confidence(self):
        hi = ScreenTarget(id="dup:hi", bbox=(100, 100, 100, 50), confidence=0.9,
                          source="dom", timestamp=0.0)
        lo = ScreenTarget(id="dup:lo", bbox=(100, 100, 100, 50), confidence=0.6,
                          source="dom", timestamp=0.0)
        far = ScreenTarget(id="far", bbox=(700, 700, 40, 40), confidence=0.7,
                           source="dom", timestamp=0.0)
        kept = dedupe_targets([lo, hi, far])
        assert [t.id for t in kept] == ["dup:hi", "far"]

    def test_parse_app_name(self):
        assert parse_app_name("Mercury Editor — Mozilla Firefox") == "Mozilla Firefox"
        assert parse_app_name("Untitled - Notepad") == "Notepad"
        assert parse_app_name("plain title") == "plain title"
        assert parse_app_name("") == ""
        assert parse_app_name(None) == ""


# ---------------------------------------------------------------------------
# Engine: merging, queries, injection
# ---------------------------------------------------------------------------

class TestEngineQueries:
    def test_target_at_center_hits_center_zone(self):
        engine = ScreenPerceptionEngine(1920, 1080,
                                        providers=[GeometryProvider(1920, 1080)])
        model = engine.update(now=1.0)
        assert model.screen_w == 1920 and model.screen_h == 1080
        hit = engine.target_at(960, 540)
        assert hit is not None and hit.id == "geo:center"

    def test_nearest_within_max_dist(self):
        engine = ScreenPerceptionEngine(1920, 1080,
                                        providers=[GeometryProvider(1920, 1080)])
        engine.update(now=1.0)
        near = engine.nearest(970, 550, max_dist=60.0)
        assert near is not None and near.id == "geo:center"
        assert engine.nearest(1919, 5, max_dist=10.0) is None
        assert engine.nearest(1680, 135, max_dist=5.0).id == "geo:corner_tr"

    def test_fake_provider_target_found_by_text_and_point(self):
        engine = ScreenPerceptionEngine(1920, 1080,
                                        providers=[FixedProvider([button()])])
        engine.update(now=1.0)
        found = engine.find_by_text("submit")
        assert found is not None and found.id == "fake:submit"
        assert engine.target_at(560, 320).id == "fake:submit"
        assert engine.model is not None

    def test_model_helpers_smallest_actionable_wins(self):
        engine = ScreenPerceptionEngine(1920, 1080,
                                        providers=[FixedProvider([button()])])
        model = engine.update(now=1.0)
        hit = model.target_at(560, 320)
        assert hit.id == "fake:submit"          # button area < center zone
        assert model.find_by_text("missing-text") is None

    def test_dedupe_overlap_in_merge(self):
        hi = ScreenTarget(id="dup:hi", bbox=(100, 100, 100, 50), confidence=0.9,
                          source="dom", timestamp=0.0)
        lo = ScreenTarget(id="dup:lo", bbox=(100, 100, 100, 50), confidence=0.6,
                          source="dom", timestamp=0.0)
        far = ScreenTarget(id="far", bbox=(700, 700, 40, 40), confidence=0.7,
                           source="dom", timestamp=0.0)
        engine = ScreenPerceptionEngine(1000, 800,
                                        providers=[FixedProvider([hi, lo, far])])
        model = engine.update(now=1.0)
        dups = [t for t in model.targets if t.id in ("dup:hi", "dup:lo")]
        assert len(dups) == 1 and dups[0].id == "dup:hi"
        assert any(t.id == "far" for t in model.targets)

    def test_geometry_guarantee_with_zero_providers(self):
        engine = ScreenPerceptionEngine(640, 480, providers=[])
        model = engine.update(now=1.0)
        assert any(t.id == "geo:center" for t in model.targets)
        assert engine.target_at(320, 240).id == "geo:center"

    def test_broken_provider_never_raises(self):
        class Exploding:
            name = "boom"
            available = True

            def update(self, now):
                raise RuntimeError("boom")

        engine = ScreenPerceptionEngine(800, 600, providers=[Exploding()])
        model = engine.update(now=1.0)  # must not raise
        assert any(t.id == "geo:center" for t in model.targets)

    def test_unavailable_provider_is_skipped(self):
        dead = FixedProvider([button(bbox=(0, 0, 10, 10))])
        dead.available = False
        engine = ScreenPerceptionEngine(800, 600, providers=[dead])
        model = engine.update(now=1.0)
        assert all(t.id != "fake:submit" for t in model.targets)


# ---------------------------------------------------------------------------
# Context resolver hook
# ---------------------------------------------------------------------------

class TestContextResolver:
    def test_resolver_receives_active_title(self):
        class FakeAX:
            name = "accessibility"
            available = True
            last_title = "Mercury Editor — Mozilla Firefox"

            def update(self, now):
                return [ScreenTarget(
                    id="ax:active_window", type=ScreenTargetType.WINDOW,
                    bbox=(0, 0, 1920, 1080), text=self.last_title,
                    confidence=0.8, application=parse_app_name(self.last_title),
                    actionable=True, source="accessibility", timestamp=now)]

        seen = []

        def resolver(title):
            seen.append(title)
            return AppContext.BROWSER if "firefox" in (title or "").lower() \
                else AppContext.UNKNOWN

        engine = ScreenPerceptionEngine(1920, 1080, providers=[FakeAX()],
                                        context_resolver=resolver)
        model = engine.update(now=1.0)
        assert seen == ["Mercury Editor — Mozilla Firefox"]
        assert model.context is AppContext.BROWSER
        assert model.active_window_title == "Mercury Editor — Mozilla Firefox"
        assert any(t.type is ScreenTargetType.WINDOW for t in model.targets)
        assert any(t.application == "Mozilla Firefox" for t in model.targets)

    def test_default_resolver_yields_unknown_context(self):
        engine = ScreenPerceptionEngine(800, 600)
        model = engine.update(now=1.0)
        assert model.context is AppContext.UNKNOWN

    def test_resolver_exception_degrades_to_unknown(self):
        def bad_resolver(title):
            raise ValueError("nope")

        engine = ScreenPerceptionEngine(800, 600, context_resolver=bad_resolver)
        model = engine.update(now=1.0)
        assert model.context is AppContext.UNKNOWN


# ---------------------------------------------------------------------------
# Platform providers: graceful degradation
# ---------------------------------------------------------------------------

class TestPlatformProviders:
    def test_accessibility_headless_degrades(self):
        provider = AccessibilityProvider()
        assert isinstance(provider.available, bool)
        result = provider.update(0.0)
        if not provider.available:
            assert result == []
        title = AccessibilityProvider.active_window_title()
        assert isinstance(title, str)  # '' in headless sandboxes

    def test_engine_works_headless_with_defaults(self):
        engine = ScreenPerceptionEngine(1280, 720)
        model = engine.update(now=2.0)  # must never raise
        assert model.screen_w == 1280 and model.screen_h == 720
        assert any(t.id == "geo:center" for t in model.targets)
        assert engine.target_at(640, 360).id == "geo:center"
        assert isinstance(engine.availability(), dict)

    def test_ocr_disabled_by_default(self):
        provider = OCRProvider()
        assert provider.available is False
        assert provider.config["enabled"] is False
        assert provider.update(0.0) == []

    def test_ocr_enabled_without_deps_degrades(self):
        provider = OCRProvider(config={"enabled": True})
        assert provider.config["enabled"] is True
        if not provider.available:  # sandbox lacks pytesseract/tesseract
            assert provider.update(0.0) == []
        # when deps exist it still must not raise:
        try:
            provider.update(0.0)
        except Exception:  # pragma: no cover
            pytest.fail("OCRProvider.update raised")

    def test_engine_default_includes_ocr_disabled_and_geometry(self):
        engine = ScreenPerceptionEngine(800, 600)
        names = [p.name for p in engine.providers]
        assert names == ["accessibility", "ocr", "geometry"]
        ocr = engine.providers[1]
        assert ocr.available is False  # disabled by default


# ---------------------------------------------------------------------------
# describe_target — always a human phrase
# ---------------------------------------------------------------------------

class TestDescribeTarget:
    def setup_method(self):
        self.engine = ScreenPerceptionEngine(800, 600)

    def test_button_phrase(self):
        btn = ScreenTarget(id="b", type=ScreenTargetType.BUTTON, text="Submit",
                           bbox=(0, 0, 10, 10), source="accessibility")
        assert self.engine.describe_target(btn) == "Submit button"

    def test_window_phrase(self):
        win = ScreenTarget(id="w", type=ScreenTargetType.WINDOW,
                           text="Mercury — Mozilla Firefox",
                           application="Mozilla Firefox", bbox=(0, 0, 100, 100),
                           source="accessibility")
        assert self.engine.describe_target(win) == "Window: Mozilla Firefox"

    def test_geometry_phrase(self):
        geo = ScreenTarget(id="geo:center", type=ScreenTargetType.UNKNOWN,
                           bbox=(0, 0, 10, 10), source="geometry")
        assert self.engine.describe_target(geo) == "screen region center"

    def test_generic_text_phrase(self):
        link = ScreenTarget(id="l", type=ScreenTargetType.LINK, text="Docs",
                            bbox=(0, 0, 10, 10), source="dom")
        assert self.engine.describe_target(link) == "Docs link"

    def test_fallback_phrases(self):
        assert self.engine.describe_target(None) == "unknown screen region"
        assert self.engine.describe_target(None, point=(123, 45)) == \
            "screen point (123, 45)"

    def test_describe_from_live_model_never_empty(self):
        self.engine.update(now=1.0)
        phrase = self.engine.describe_target(self.engine.target_at(400, 300))
        assert isinstance(phrase, str) and phrase


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

class TestRefreshCaching:
    def test_refresh_interval_returns_same_model_object(self):
        counter = CountingProvider()
        engine = ScreenPerceptionEngine(640, 480,
                                        config={"refresh_interval": 0.5},
                                        providers=[counter])
        m1 = engine.update(now=10.0)
        m2 = engine.update(now=10.2)          # inside the interval
        assert m1 is m2
        assert counter.calls == 1
        m3 = engine.update(now=10.6)          # outside the interval
        assert m3 is not m1
        assert counter.calls == 2

    def test_force_and_invalidate_bypass_cache(self):
        counter = CountingProvider()
        engine = ScreenPerceptionEngine(640, 480,
                                        config={"refresh_interval": 0.5},
                                        providers=[counter])
        engine.update(now=10.0)
        engine.update(now=10.1, force=True)
        assert counter.calls == 2
        engine.invalidate()
        engine.update(now=10.2)               # cache was dropped
        assert counter.calls == 3

    def test_model_is_screenmodel_instance(self):
        engine = ScreenPerceptionEngine(800, 600)
        model = engine.update(now=1.0)
        assert isinstance(model, ScreenModel)
        assert engine.model is model
