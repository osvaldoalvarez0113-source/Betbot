"""
Test suite for kelly_odds.py — pytest.
Rules:
  - No network calls.
  - All env vars set in conftest.py before import.
  - Never fix a test to hide a bug; document unexpected behaviour.
"""
import json
import math
import os
import sys
from datetime import datetime, timedelta, date
from unittest.mock import patch

import pytest

# ── Import guard: empty env vars must already be set by conftest ──────────────
import kelly_odds as ko


# ═══════════════════════════════════════════════════════════════════════════════
# A) kelly_stake
# ═══════════════════════════════════════════════════════════════════════════════

class TestKellyStake:
    def test_positive_edge_returns_stake(self):
        """prob=0.60, odds=2.00 → clear edge, stake > 0."""
        r = ko.kelly_stake(0.60, 2.00)
        assert r["stake"] > 0
        assert r["edge"] > 0
        assert r["has_value"] is True

    def test_negative_edge_returns_zero(self):
        """prob=0.40, odds=2.00 → Kelly is negative → stake=0."""
        r = ko.kelly_stake(0.40, 2.00)
        assert r["stake"] == 0

    def test_odds_one_returns_zero(self):
        """b = fair_odd - 1 = 0 → undefined Kelly → stake=0, no crash."""
        r = ko.kelly_stake(0.60, 1.0)
        assert r["stake"] == 0
        assert r["edge"] == 0

    def test_odds_below_one_returns_zero(self):
        """b = fair_odd - 1 < 0 → negative b → stake=0, no crash."""
        r = ko.kelly_stake(0.60, 0.5)
        assert r["stake"] == 0
        assert r["edge"] == 0

    def test_prob_cap_applied(self):
        """
        prob=0.95 > PROB_CAP (0.85) → must be clamped to PROB_CAP_CEIL (0.80).
        We verify this by checking that the returned edge equals the value
        computed with 0.80, not 0.95.
        """
        r_high = ko.kelly_stake(0.95, 2.00)
        r_cap  = ko.kelly_stake(ko.PROB_CAP_CEIL, 2.00)
        # Edge should match the capped value (0.80-based), not raw 0.95-based
        assert r_high["edge"] == pytest.approx(r_cap["edge"], abs=0.01)
        # Sanity: if cap was NOT applied, edge would be ~45%; capped → ~30%
        assert r_high["edge"] < 40.0

    def test_stake_never_exceeds_hard_cap(self):
        """Stake must never exceed BANKROLL * MAX_SINGLE_BET_PCT regardless of input."""
        max_allowed = ko.BANKROLL * ko.MAX_SINGLE_BET_PCT
        for prob in [0.55, 0.70, 0.80, 0.85, 0.90, 0.99]:
            for odds in [1.50, 2.00, 3.00, 5.00, 10.00]:
                r = ko.kelly_stake(prob, odds)
                assert r["stake"] <= max_allowed + 0.01, (
                    f"Stake cap violated: prob={prob}, odds={odds}, stake={r['stake']}"
                )

    def test_below_min_bet_zeroed(self):
        """
        Kelly raw < MIN_BET ($10) → stake=0 (no inflation to minimum).
        prob=0.51, odds=2.00: k=0.02, raw = 1000 * 0.02 * 0.25 = $5 < $10.
        """
        r = ko.kelly_stake(0.51, 2.00)
        assert r["stake"] == 0, (
            f"Expected stake=0 when Kelly raw < MIN_BET, got {r['stake']}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# B) remove_vig
# ═══════════════════════════════════════════════════════════════════════════════

class TestRemoveVig:
    def test_balanced_two_way(self):
        """[1.91, 1.91] → each prob ≈ 0.50, sum = 1.0."""
        probs = ko.remove_vig([1.91, 1.91])
        assert sum(probs) == pytest.approx(1.0, abs=0.001)
        assert probs[0] == pytest.approx(0.50, abs=0.01)
        assert probs[1] == pytest.approx(0.50, abs=0.01)

    def test_asymmetric_two_way(self):
        """[1.50, 2.80] → sum=1.0, favourite prob > underdog prob."""
        probs = ko.remove_vig([1.50, 2.80])
        assert sum(probs) == pytest.approx(1.0, abs=0.001)
        assert probs[0] > probs[1]

    def test_three_way_soccer(self):
        """3-outcome market → probabilities still sum to 1.0."""
        probs = ko.remove_vig([2.40, 3.30, 2.80])
        assert sum(probs) == pytest.approx(1.0, abs=0.001)
        assert len(probs) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# C) poisson_ou_prob
# ═══════════════════════════════════════════════════════════════════════════════

class TestPoissonOuProb:
    def test_half_line_sums_to_one(self):
        """Half-line (no push possible): p_over + p_under = 1.0."""
        p_over  = ko.poisson_ou_prob(8.5, 8.5, True)
        p_under = ko.poisson_ou_prob(8.5, 8.5, False)
        assert p_over + p_under == pytest.approx(1.0, abs=0.001)

    def test_expected_above_line_favours_over(self):
        """expected=10.0 > line=8.5 → p_over > 0.5."""
        assert ko.poisson_ou_prob(10.0, 8.5, True) > 0.5

    def test_expected_below_line_favours_under(self):
        """expected=7.0 < line=8.5 → p_over < 0.5."""
        assert ko.poisson_ou_prob(7.0, 8.5, True) < 0.5

    def test_whole_number_line_push_handled(self):
        """Whole-number line: push prob split evenly → both p_over and p_under in (0.01, 0.99)."""
        p_over  = ko.poisson_ou_prob(9.0, 9.0, True)
        p_under = ko.poisson_ou_prob(9.0, 9.0, False)
        assert 0.01 <= p_over  <= 0.99
        assert 0.01 <= p_under <= 0.99

    def test_zero_expected_total_no_crash(self):
        """expected_total=0 → function must not raise, must return >= 0.01."""
        result = ko.poisson_ou_prob(0, 8.5, True)
        assert result >= 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# D) poisson_match_probs
# ═══════════════════════════════════════════════════════════════════════════════

class TestPoissonMatchProbs:
    def test_probabilities_sum_to_one(self):
        p_win, p_draw, p_loss = ko.poisson_match_probs(1.5, 1.2)
        assert p_win + p_draw + p_loss == pytest.approx(1.0, abs=0.001)

    def test_dominant_home_wins_more(self):
        """avg_h=2.5 >> avg_a=0.8 → p_win > p_loss."""
        p_win, _, p_loss = ko.poisson_match_probs(2.5, 0.8)
        assert p_win > p_loss

    def test_equal_averages_symmetric(self):
        """Equal averages → p_win ≈ p_loss (tolerance 0.02)."""
        p_win, _, p_loss = ko.poisson_match_probs(1.5, 1.5)
        assert abs(p_win - p_loss) < 0.02


# ═══════════════════════════════════════════════════════════════════════════════
# E) _check_arb2
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckArb2:
    def test_true_arb_detected(self):
        """
        2.10 / 2.10 → genuine arb.
        margin = 2/2.10 ≈ 0.9524; profit_pct = (1-margin)/margin × 100 = 5.0%
        Stakes must both be > 0 and total ≤ $110 (arb_budget cap).
        """
        r = ko._check_arb2("Home", "Away", "Home", 2.10, "FD", "Away", 2.10, "DK")
        assert r is not None
        assert r["profit_pct"] == pytest.approx(5.0, abs=0.05)
        assert r["stake_a"] > 0
        assert r["stake_b"] > 0
        assert r["stake_a"] + r["stake_b"] <= 110.0

    def test_no_arb_when_margin_above_one(self):
        """Standard vig (1.91 / 1.91): margin > 1 → None."""
        r = ko._check_arb2("Home", "Away", "Home", 1.91, "FD", "Away", 1.91, "DK")
        assert r is None

    def test_profit_above_8pct_filtered(self):
        """Profit > 8% likely a data error → filtered out (returns None)."""
        r = ko._check_arb2("Home", "Away", "Home", 3.00, "FD", "Away", 3.00, "DK")
        assert r is None


# ═══════════════════════════════════════════════════════════════════════════════
# F) _check_arb3
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckArb3:
    def test_true_three_way_arb_detected(self):
        """
        3 equal odds of 3.125 → margin ≈ 0.96, profit ≈ 4.17%.
        All stakes > 0 and total ≤ $110.
        """
        r = ko._check_arb3(
            "H", "A",
            "H", 3.125, "B1",
            "D", 3.125, "B2",
            "A", 3.125, "B3",
        )
        assert r is not None
        assert r["stake_a"] > 0
        assert r["stake_b"] > 0
        assert r["stake_c"] > 0
        assert r["stake_a"] + r["stake_b"] + r["stake_c"] <= 110.0

    def test_normal_soccer_odds_no_arb(self):
        """Typical soccer market (2.5/3.3/2.9): margin > 1 → None."""
        r = ko._check_arb3(
            "H", "A",
            "H", 2.5, "B1",
            "D", 3.3, "B2",
            "A", 2.9, "B3",
        )
        assert r is None


# ═══════════════════════════════════════════════════════════════════════════════
# G) _cap_prob
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapProb:
    def test_above_single_cap_clamped(self):
        """0.90 > PROB_CAP (0.85) → returns PROB_CAP_CEIL (0.80)."""
        assert ko._cap_prob(0.90) == ko.PROB_CAP_CEIL

    def test_below_single_cap_unchanged(self):
        """0.70 < PROB_CAP → returned as-is."""
        assert ko._cap_prob(0.70) == pytest.approx(0.70)

    def test_parlay_leg_capped(self):
        """0.75 > PROB_CAP_PARLAY (0.68) → returns PROB_CAP_PARLAY."""
        assert ko._cap_prob(0.75, is_parlay_leg=True) == ko.PROB_CAP_PARLAY


# ═══════════════════════════════════════════════════════════════════════════════
# H) pythagorean_win_prob
# ═══════════════════════════════════════════════════════════════════════════════

class TestPythagoreanWinProb:
    def test_better_offence_wins_more(self):
        """rs=5.0, ra=4.0 → prob > 0.5."""
        assert ko.pythagorean_win_prob(5.0, 4.0) > 0.5

    def test_none_rs_returns_half(self):
        """rs=None → 0.5 (data unavailable)."""
        assert ko.pythagorean_win_prob(None, 4.0) == 0.5

    def test_zero_zero_returns_half(self):
        """rs=0, ra=0 → 0.5 (avoids ZeroDivisionError)."""
        assert ko.pythagorean_win_prob(0, 0) == 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# I) elo_win_prob
# ═══════════════════════════════════════════════════════════════════════════════

class TestEloWinProb:
    def test_equal_elo_returns_half(self):
        """
        Two unknown teams both resolve to ELO=1400 (unknown floor) → prob ≈ 0.50.
        Use names guaranteed NOT to be in _elo_ratings or _WC2026_ELO_SEED.
        """
        p = ko.elo_win_prob("__unknown_team_X__", "__unknown_team_Y__")
        assert p == pytest.approx(0.50, abs=0.001)

    def test_dominant_elo_advantage(self):
        """
        ELO 2000 vs 1400 → win prob > 0.85.
        We inject the ratings directly into the module's dict.
        """
        original = dict(ko._elo_ratings)
        try:
            ko._elo_ratings["__strong_team__"] = 2000.0
            ko._elo_ratings["__weak_team__"]   = 1400.0
            p = ko.elo_win_prob("__strong_team__", "__weak_team__")
            assert p > 0.85
        finally:
            ko._elo_ratings.clear()
            ko._elo_ratings.update(original)


# ═══════════════════════════════════════════════════════════════════════════════
# J) _is_us_book
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsUsBook:
    def test_bovada_is_us(self):
        assert ko._is_us_book("Bovada") is True

    def test_fanduel_is_us(self):
        assert ko._is_us_book("FanDuel") is True

    def test_pinnacle_not_us(self):
        assert ko._is_us_book("Pinnacle") is False

    def test_pointsbet_au_not_us(self):
        assert ko._is_us_book("PointsBet AU") is False

    def test_bet365_not_us(self):
        assert ko._is_us_book("bet365") is False


# ═══════════════════════════════════════════════════════════════════════════════
# K) _game_already_started
# ═══════════════════════════════════════════════════════════════════════════════

class TestGameAlreadyStarted:
    def _fmt(self, dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_started_ten_minutes_ago(self):
        ts = self._fmt(datetime.utcnow() - timedelta(minutes=10))
        assert ko._game_already_started(ts) is True

    def test_starts_in_two_hours(self):
        ts = self._fmt(datetime.utcnow() + timedelta(hours=2))
        assert ko._game_already_started(ts) is False

    def test_empty_string_returns_false(self):
        assert ko._game_already_started("") is False

    def test_garbage_string_returns_false(self):
        assert ko._game_already_started("not-a-date") is False

    def test_none_like_string_returns_false(self):
        assert ko._game_already_started("None") is False


# ═══════════════════════════════════════════════════════════════════════════════
# L) queue_for_confirmation
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueForConfirmation:
    """
    Uses tmp_path to isolate file I/O.
    Monkeypatches ntfy_post and _save_daily_exposure to avoid network/disk side-effects.
    """

    def _reset_exposure(self, monkeypatch):
        monkeypatch.setattr(ko, "_daily_exposure",      0.0)
        monkeypatch.setattr(ko, "_daily_exposure_date", date(2000, 1, 1))

    def test_valid_pick_queued_correctly(self, tmp_path, monkeypatch):
        """
        Single valid pick → file created with stake=0.0, suggested_stake=original,
        _status='pending'.
        """
        confirm_file = str(tmp_path / "confirm.json")
        monkeypatch.setattr(ko, "CONFIRM_FILE", confirm_file)
        monkeypatch.setattr(ko, "ntfy_post", lambda *a, **kw: None)
        monkeypatch.setattr(ko, "_save_daily_exposure", lambda: None)
        self._reset_exposure(monkeypatch)

        pick = {"match": "TeamA vs TeamB", "team": "TeamA", "stake": 25.0, "odds": 2.10}
        ko.queue_for_confirmation([pick], "baseball_mlb")

        assert os.path.exists(confirm_file), "confirm.json was not created"
        with open(confirm_file) as f:
            queue = json.load(f)

        assert len(queue) == 1
        entry = queue[0]
        assert entry["stake"] == 0.0,             "stake must be $0 until user confirms"
        assert entry["suggested_stake"] == 25.0,  "suggested_stake must preserve original"
        assert entry["_status"] == "pending",      "_status must be 'pending'"

    def test_pick_exceeding_daily_limit_not_queued(self, tmp_path, monkeypatch):
        """
        stake > BANKROLL * MAX_DAILY_EXPO_PCT (15%) → pick must NOT be queued.
        With BANKROLL=1000, max_daily=$150; stake=200 exceeds it.
        """
        confirm_file = str(tmp_path / "confirm_cap.json")
        monkeypatch.setattr(ko, "CONFIRM_FILE", confirm_file)
        monkeypatch.setattr(ko, "ntfy_post", lambda *a, **kw: None)
        monkeypatch.setattr(ko, "_save_daily_exposure", lambda: None)
        self._reset_exposure(monkeypatch)

        oversized_pick = {
            "match": "TeamC vs TeamD",
            "team":  "TeamC",
            "stake": ko.BANKROLL * ko.MAX_DAILY_EXPO_PCT + 1.0,  # e.g. $151
        }
        ko.queue_for_confirmation([oversized_pick], "baseball_mlb")

        if os.path.exists(confirm_file):
            with open(confirm_file) as f:
                queue = json.load(f)
            assert len(queue) == 0, (
                "Over-limit pick must NOT appear in the confirmation queue"
            )
