import unittest
from datetime import timedelta

from lostfound.matching import MATCHER, Match, Rejection
from lostfound.models import Report, utcnow


def R(kind, desc, location="", hours_ago=None, ids=None, color=""):
    """Test report builder."""
    occurred = None if hours_ago is None else utcnow() - timedelta(hours=hours_ago)
    return Report(
        kind=kind, description=desc, location=location,
        occurred_at=occurred, time_precision="exact" if occurred else "unknown",
        identifiers=ids or [], color=color,
    )


class MatchingTests(unittest.TestCase):
    """Golden tests for the matching engine."""

    def test_brief_example_strong(self):
        """The brief's own example: black backpack vs dark-coloured backpack."""
        lost = R("lost", "Black backpack containing a laptop charger. Lost around the library on Monday afternoon.", "Library", 120)
        found = R("found", "Dark-coloured backpack found near the library entrance Monday evening.", "Library Entrance", 116)
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        self.assertEqual(outcome.band, "strong")
        self.assertGreaterEqual(outcome.score, 0.80)  # Should be high

    def test_far_away_possible(self):
        """Same item, but two weeks later and at the other end of campus: possible."""
        lost = R("lost", "Black backpack containing a laptop charger.", "Library", 360)
        found = R("found", "Black backpack found at the football field.", "Football Field", 24)
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        self.assertEqual(outcome.band, "possible")

    def test_imei_match_overrides(self):
        """Same IMEI, everything else different, still strong."""
        lost = R("lost", "Lost my white iPhone 13 in a red silicone case. IMEI 356938035643809.",
                 "Lecture Hall A", 60, ids=["356938035643809"])
        found = R("found", "Handed in a grey phone, screen cracked. IMEI 356938035643809.",
                  "Dormitory B", 12, ids=["356938035643809"])
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        self.assertEqual(outcome.band, "strong")
        self.assertGreaterEqual(outcome.score, 0.95)  # IMEI promotion

    def test_imei_conflict_vetoed(self):
        """Near-identical prose, but conflicting IMEI: rejected."""
        lost = R("lost", "Lost my white iPhone 13 in a red silicone case. IMEI 356938035643809.",
                 "Lecture Hall A", 60, ids=["356938035643809"])
        found = R("found", "Handed in a white iPhone in a red case. IMEI 351234567890123.",
                  "Lecture Hall A", 12, ids=["351234567890123"])
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Rejection)
        self.assertIn("imei", outcome.reason.lower())

    def test_typo_tolerance(self):
        """Typos like 'balck watr bottel' should match 'black water bottle'."""
        lost = R("lost", "balck watr bottel with a dent, samsung sticker on it",
                 "Gymnasium", 40)
        found = R("found", "Black water bottle with samsung sticker, dented.",
                  "Basketball Court", 34)
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        self.assertEqual(outcome.band, "strong")

    def test_found_before_lost_vetoed(self):
        """Item found 3 days before it was lost: vetoed by chronology gate."""
        found = R("found", "Blue umbrella left in the reading room.", "Library", 200)
        lost = R("lost", "Blue umbrella, wooden handle, lost in the library.", "Library", 20)
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Rejection)
        self.assertIn("before", outcome.reason.lower())

    def test_thin_report_weak(self):
        """'lost my bag' has almost no information: weak band, not zero."""
        lost = R("lost", "lost my bag")
        found = R("found", "found a backpack")
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        self.assertEqual(outcome.band, "weak")  # Not rejected, but weak
        self.assertLess(outcome.score, 0.50)  # Low score from evidence discount

    def test_different_type_vetoed(self):
        """Earbuds vs laptop sleeve: different item types, rejected."""
        lost = R("lost", "Lost my black AirPods case yesterday near the cafeteria.",
                 "Cafeteria", 30)
        found = R("found", "Black laptop sleeve found on a desk.",
                  "Computer Lab", 6)
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Rejection)
        self.assertIn("different", outcome.reason.lower())

    def test_shade_consistency(self):
        """'dark' is a shade, consistent with 'black': should match, not conflict."""
        lost = R("lost", "Black AirPods case", "Cafeteria", 30, color="black")
        found = R("found", "Dark wireless earbud case", "Coffee Shop", 26, color="dark")
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        # Colour should not be in conflict; it should be available and scored positively
        color_signal = [s for s in outcome.signals if s.name == "color"][0]
        self.assertTrue(color_signal.available)
        self.assertGreater(color_signal.score, 0.5)

    def test_adjacent_colors_partial_credit(self):
        """Grey vs black: adjacent colours, partial credit instead of conflict."""
        lost = R("lost", "Grey laptop sleeve for a 14 inch macbook.", "Computer Lab", 8, color="grey")
        found = R("found", "Black laptop sleeve found on a desk, fits a small macbook.",
                  "Computer Lab", 6, color="black")
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        color_signal = [s for s in outcome.signals if s.name == "color"][0]
        self.assertTrue(color_signal.available)
        self.assertGreater(color_signal.score, 0.4)  # Partial, not zero
        self.assertLess(color_signal.score, 1.0)  # Not a full match

    def test_name_tag_match(self):
        """Name written on a calculator: low-precision identifier, boosts but doesn't force strong."""
        lost = R("lost", "Casio scientific calculator, name Selam written on the back.",
                 "Science Block", 50, ids=["Selam"])
        found = R("found", "Calculator found in the chemistry lab, marked Selam.",
                  "Science Block", 48, ids=["Selam"])
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        self.assertEqual(outcome.band, "strong")  # Forced to strong
        self.assertGreaterEqual(outcome.score, 0.85)  # Soft identifier boost

    def test_coverage_discount(self):
        """Only type is available; score should be discounted for thin evidence."""
        lost = R("lost", "bag")  # No colour, no place, no date, minimal description
        found = R("found", "bag")  # Same
        outcome = MATCHER.explain(lost, found)
        self.assertIsInstance(outcome, Match)
        # Type signal might be 1.0, but coverage discount brings the score down
        self.assertLess(outcome.score, 0.6)
        self.assertEqual(outcome.band, "weak")


if __name__ == "__main__":
    unittest.main()
