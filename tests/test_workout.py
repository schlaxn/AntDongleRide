import unittest

from workout import Workout, parse_shorthand


class DefaultWorkoutTests(unittest.TestCase):
    def setUp(self):
        self.w = parse_shorthand("3-13-30-15")

    def test_total_is_59_minutes_with_last_recovery_skipped(self):
        # 59:15 with every recovery (3555 s); the default skips the last recovery (15 s) of each
        # block, so the between-block rest or the cool-down follows directly: 3 x 15 s less
        self.assertEqual(self.w.total_s, 59 * 60 + 15 - 3 * 15)

    def test_segment_order_starts_with_warmup_ends_with_cooldown(self):
        segs = self.w.segments()
        self.assertEqual(segs[0].kind, "warmup")
        self.assertEqual(segs[-1].kind, "cooldown")

    def test_segments_are_contiguous(self):
        segs = self.w.segments()
        for a, b in zip(segs, segs[1:]):
            self.assertEqual(a.end, b.start)

    def test_rep_counts(self):
        segs = self.w.segments()
        work = [s for s in segs if s.kind == "work"]
        recover = [s for s in segs if s.kind == "recover"]
        between = [s for s in segs if s.kind == "between"]
        self.assertEqual(len(work), 39)
        self.assertEqual(len(recover), 36)  # 39 reps, the last recovery of each block (3) is skipped
        self.assertEqual(len(between), 2)
        self.assertEqual({s.block for s in work}, {1, 2, 3})

    def test_phase_lookup(self):
        self.assertEqual(self.w.phase_at(0).kind, "warmup")
        self.assertEqual(self.w.phase_at(600).kind, "work")       # first work interval starts
        self.assertEqual(self.w.phase_at(629).kind, "work")
        self.assertEqual(self.w.phase_at(630).kind, "recover")
        self.assertIsNone(self.w.phase_at(self.w.total_s))
        self.assertIsNone(self.w.phase_at(-1))


class GradientTests(unittest.TestCase):
    def test_profile_length_matches_total(self):
        w = parse_shorthand("1-2-10-5", warmup_s=20, cooldown_s=20, between_s=0)
        self.assertEqual(len(w.gradient_per_second()), w.total_s)

    def test_positive_in_work_negative_in_recovery_after_smoothing(self):
        w = parse_shorthand("1-2-40-40", warmup_s=0, cooldown_s=0)
        g = w.gradient_per_second()
        mid_work = w.segments()[0].start + 20
        mid_recover = w.segments()[1].start + 20
        self.assertGreater(g[mid_work], 0)
        self.assertLess(g[mid_recover], 0)

    def test_transition_is_smoothed(self):
        w = parse_shorthand("1-1-10-10", warmup_s=0, cooldown_s=0, skip_last_recover=False)
        g = w.gradient_per_second()
        jump = abs(g[10] - g[9])
        self.assertLess(jump, w.gradients["work"] - w.gradients["recover"])


class ShorthandTests(unittest.TestCase):
    def test_parses_four_fields(self):
        w = parse_shorthand("3-13-30-15")
        self.assertEqual((w.blocks, w.reps, w.on_s, w.off_s), (3, 13, 30, 15))

    def test_bad_input_rejected(self):
        for bad in ("3-13-30", "a-b-c-d", "3-0-30-15", "3-13-30-15-1"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_shorthand(bad)

    def test_skip_last_recover(self):
        w = Workout(blocks=1, reps=2, on_s=10, off_s=5, warmup_s=0, cooldown_s=0,
                    skip_last_recover=True)
        kinds = [s.kind for s in w.segments()]
        self.assertEqual(kinds, ["work", "recover", "work"])

    def test_to_dict_includes_shorthand_and_profile(self):
        d = parse_shorthand("3-13-30-15").to_dict()
        self.assertEqual(d["shorthand"], "3-13-30-15")
        self.assertEqual(len(d["gradient_per_second"]), d["total_s"])


if __name__ == "__main__":
    unittest.main()
