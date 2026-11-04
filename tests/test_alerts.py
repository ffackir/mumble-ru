from pathlib import Path
import sys
import unittest
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from alerts import WordAlerts, parse_words

class AlertTests(unittest.TestCase):
    def test_words_phrases_case_and_boundaries(self):
        alerts = WordAlerts(['warp', 'на ворота', 'C++'])
        self.assertTrue(alerts.matches('WARP now!'))
        self.assertTrue(alerts.matches('Разгон НА   ВОРОТА.'))
        self.assertTrue(alerts.matches('Use C++ here'))
        self.assertFalse(alerts.matches('warping or prewarp'))
        self.assertEqual(parse_words('warp, WARP; gate\nна ворота'), ['warp', 'gate', 'на ворота'])

    def test_no_sound_without_words_or_when_disabled(self):
        self.assertFalse(WordAlerts().should_play('1', 'warp', now=0))
        self.assertFalse(WordAlerts(['warp'], enabled=False).should_play('1', 'warp', now=0))

    def test_one_signal_for_original_and_translation_with_cooldown(self):
        alerts = WordAlerts(['warp', 'прыжок'])
        self.assertTrue(alerts.should_play('a', 'warp', now=0))
        self.assertFalse(alerts.should_play('a', 'прыжок', now=3))
        self.assertFalse(alerts.should_play('b', 'warp', now=1))
        self.assertFalse(alerts.should_play('b', 'прыжок', now=4))
        self.assertTrue(alerts.should_play('c', 'прыжок', now=4))

if __name__ == '__main__': unittest.main()
