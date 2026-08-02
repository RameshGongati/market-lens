"""Tests for zone-confirmation selection.

A confirmation is price entering a zone and closing back out through the
PROXIMAL side — evidence the orders were real, and the point at which a trade
becomes worth taking. It is a different question from the one ``filter_zones``
answers, which is "is price approaching a zone".

The two cannot share a code path, and the reason is arithmetic. Confirmation
forces freshness off its top value: a once-tested zone scores 1.5 + strength
{1,2} + time {0,1,2}, reaching only 2.5/3.5/4.5/5.5, and a zone tested 2+
times scores 0.0 + the same, reaching only 1.0/2.0/3.0/4.0. Neither ladder
contains 5.0, so ``filter_zones``'s 5.0 cutoff cannot grade a confirmed zone.
Measured over 8 NIFTY stocks: 60 confirmed zones detected, 17 of them sitting
at 4.5, and only 2 reaching the screener.
"""

from __future__ import annotations

import pandas as pd

from analysis.zone_engine.filters import (
    _CONFIRMATION_MAX_DISTANCE_PCT,
    filter_confirmation_zones,
    filter_zones,
)
from analysis.zone_engine.models import Zone


def _zone(
    category: str = "demand",
    proximal: float = 1000.0,
    distal: float = 985.0,
    *,
    times_tested: int = 1,
    odd_score: float = 4.5,
    zone_strength: str = "Normal",
    created_at_index: int = 10,
) -> Zone:
    """Build a Zone directly — the selection logic takes zones, not candles."""
    return Zone(
        zone_type="DBR" if category == "demand" else "RBD",
        category=category,
        proximal=proximal,
        distal=distal,
        proximal_exceptional=proximal,
        distal_exceptional=distal,
        base_start_idx=8,
        base_end_idx=9,
        legout_idx=10,
        num_base_candles=2,
        odd_score=odd_score,
        freshness_points=1.5 if times_tested == 1 else (3.0 if times_tested == 0 else 0.0),
        strength_points=2.0,
        time_points=2.0,
        times_tested=times_tested,
        zone_strength=zone_strength,
        entry_recommendation="Watch",
        created_at_index=created_at_index,
        is_fresh=times_tested == 0,
        activation_touch=times_tested > 0,
    )


# ---------------------------------------------------------------------------
# What qualifies
# ---------------------------------------------------------------------------

def test_confirmed_demand_zone_with_price_just_above_qualifies():
    """The setup being screened for: price dipped in, closed back out, and is
    still near enough that the move has not already played out."""
    z = _zone("demand", proximal=1000.0, distal=985.0, times_tested=1)
    assert filter_confirmation_zones([z], current_price=1020.0) == [z]


def test_confirmed_supply_zone_with_price_just_below_qualifies():
    """Supply mirrors: price rallied into the zone and closed back BELOW."""
    z = _zone("supply", proximal=1000.0, distal=1015.0, times_tested=1)
    assert filter_confirmation_zones([z], current_price=980.0) == [z]


def test_a_zone_tested_more_than_once_still_qualifies():
    """Repeat reactions are the premise of the setup, not a disqualifier —
    price commonly returns to a proven zone and reacts again. The score
    filter, not this one, decides whether an over-tested zone is worth it."""
    z = _zone("demand", times_tested=3, odd_score=4.0)
    assert filter_confirmation_zones([z], current_price=1020.0) == [z]


# ---------------------------------------------------------------------------
# What does not
# ---------------------------------------------------------------------------

def test_untested_zone_never_qualifies():
    """A zone price has never reached has confirmed nothing, however close
    price is. This is the case a distance filter alone cannot separate — an
    untested zone and a confirmed one sit on the SAME side at the SAME
    distance, and only the history distinguishes them."""
    z = _zone("demand", times_tested=0, odd_score=7.0)
    assert filter_confirmation_zones([z], current_price=1020.0) == []


def test_price_still_inside_the_zone_does_not_qualify():
    """Price between distal and proximal has not left yet, so there is no
    confirmed reaction to trade."""
    z = _zone("demand", proximal=1000.0, distal=985.0, times_tested=1)
    assert filter_confirmation_zones([z], current_price=992.0) == []


def test_price_beyond_the_distance_cap_does_not_qualify():
    """Past the cap the reaction has already run and the entry has gone.

    1000 -> 1090 is 8.26% of price, just over the 8% limit.
    """
    z = _zone("demand", proximal=1000.0, distal=985.0, times_tested=1)
    assert filter_confirmation_zones([z], current_price=1090.0) == []


def test_price_on_the_wrong_side_does_not_qualify():
    """A demand zone confirms by price closing back ABOVE the proximal. Price
    below it has broken through, not bounced."""
    z = _zone("demand", proximal=1000.0, distal=985.0, times_tested=1)
    assert filter_confirmation_zones([z], current_price=970.0) == []


def test_score_below_the_floor_is_excluded():
    """2.5 is a reachable total for a confirmed zone (1.5 + 1 + 0) but sits
    below the floor — a weak departure off a slow base is not a setup."""
    weak = _zone("demand", odd_score=2.5, times_tested=1)
    assert filter_confirmation_zones([weak], current_price=1020.0) == []


def test_empty_input_and_no_price_are_handled():
    assert filter_confirmation_zones([], current_price=1000.0) == []
    assert filter_confirmation_zones([_zone()], current_price=0.0) == []


# ---------------------------------------------------------------------------
# Ordering and threshold
# ---------------------------------------------------------------------------

def test_only_the_nearest_zone_on_a_side_is_returned():
    """Two confirmed demand zones both qualify, but only the nearer is the
    level price would actually meet next."""
    far = _zone("demand", proximal=960.0, distal=950.0, times_tested=1)
    near = _zone("demand", proximal=1000.0, distal=985.0, times_tested=1)
    assert filter_confirmation_zones([far, near], current_price=1010.0) == [near]


def test_both_sides_are_kept_and_ordered_nearest_first():
    """Demand below and supply above are different trades, not duplicates —
    one zone from each side survives the per-side cap."""
    demand = _zone("demand", proximal=980.0, distal=970.0, times_tested=1)
    supply = _zone("supply", proximal=1015.0, distal=1030.0, times_tested=1)
    got = filter_confirmation_zones([demand, supply], current_price=1000.0)
    assert got == [supply, demand]  # supply is 1.5% away, demand 2.0%


def test_the_distance_cap_is_the_documented_eight_percent():
    """Pinned so the constant cannot drift from what the UI help text says."""
    assert _CONFIRMATION_MAX_DISTANCE_PCT == 8.0


# ---------------------------------------------------------------------------
# Recency — near in price is not the same as recent in time
# ---------------------------------------------------------------------------

def _frame(closes: list[float], *, low: float, high: float) -> pd.DataFrame:
    """Build a frame whose every bar shares one low/high, so which bars enter
    the zone is controlled purely by that pair and the exit purely by close."""
    return pd.DataFrame({
        "Open": closes, "High": [high] * len(closes),
        "Low": [low] * len(closes), "Close": closes,
        "Volume": [1000] * len(closes),
    })


def test_a_stale_confirmation_is_rejected():
    """The RELIANCE case: a zone 3.4% from price whose reaction was 44 bars
    ago. Distance alone waved it through; it is not a live signal.

    Bar 1 dips into the zone and closes back above it — the confirmation.
    The next 40 bars stay above and never touch it again.
    """
    z = _zone("demand", proximal=1000.0, distal=985.0, times_tested=1,
              created_at_index=0)
    df = _frame([1020.0] * 42, low=999.0, high=1030.0)
    df.loc[2:, "Low"] = 1010.0          # only bars 0-1 can reach the zone
    assert filter_confirmation_zones([z], current_price=1020.0, df=df) == []


def test_a_recent_confirmation_is_kept():
    """The LUPIN case: same shape, but the reaction is 2 bars back."""
    z = _zone("demand", proximal=1000.0, distal=985.0, times_tested=1,
              created_at_index=0)
    df = _frame([1020.0] * 42, low=1010.0, high=1030.0)
    df.loc[40:, "Low"] = 999.0          # dips into the zone at the very end
    assert filter_confirmation_zones([z], current_price=1020.0, df=df) == [z]


def test_recency_is_skipped_when_no_frame_is_supplied():
    """The df argument is optional so the check degrades to distance-only
    rather than silently rejecting everything."""
    z = _zone("demand", times_tested=1)
    assert filter_confirmation_zones([z], current_price=1020.0) == [z]


# ---------------------------------------------------------------------------
# The promise that existing behaviour is untouched
# ---------------------------------------------------------------------------

def test_filter_zones_still_drops_confirmed_zones_below_five():
    """Confirmation selection must NOT have loosened the display filter.

    A 4.5 confirmed zone is exactly what the new selection exists to surface,
    and it must still be absent from the charts and alerts.
    """
    z = _zone("demand", proximal=1000.0, distal=985.0, times_tested=1, odd_score=4.5)
    assert filter_zones([z], current_price=1020.0) == []
    # ...while the confirmation selection does surface it.
    assert filter_confirmation_zones([z], current_price=1020.0) == [z]


def test_filter_zones_still_keeps_a_fresh_high_scoring_zone():
    """The ordinary path is unchanged for the zones it already showed."""
    z = _zone("demand", proximal=1000.0, distal=985.0, times_tested=0, odd_score=7.0)
    assert filter_zones([z], current_price=1020.0) == [z]
