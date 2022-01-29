from datetime import timedelta
from models import hourable, ScheduledPost, tz
import unittest
from unittest import TestCase


class hourableTest(TestCase):

    def test_zero(self):
        self.assertEqual(hourable(2022, 1, 1, 7).decimal_hours, 7.0)

    def test_half(self):
        self.assertEqual(hourable(2022, 1, 1, 7, 30).decimal_hours, 7.5)

    def test_half_plus_one(self):
        self.assertAlmostEqual(
            hourable(2022, 1, 1, 7, 31).decimal_hours, 7.516666667)


class ScheduledPostTest(TestCase):

    def setUp(self):
        self.morning_post = ScheduledPost(guild_id=-1,
                                          channel_id=-1,
                                          timing=3.5)
        self.dst_post = ScheduledPost(guild_id=-1, channel_id=-1, timing=1)

    def test_get_next_time(self):
        next_morning = self.morning_post.get_next_time()
        self.assertEqual(next_morning.hour, 3)
        self.assertEqual(next_morning.minute, 30)
        now = hourable.now(tz=tz)
        # both of these cases are explicitly tested below... only so much you
        # can do when using the real .now()
        if now.decimal_hours > self.morning_post.timing:
            self.assertEqual(now.day + 1, next_morning.day)
        else:
            self.assertEqual(now.day, next_morning.day)

        other_morning = self.morning_post.get_next_time(
            hourable(2022, 1, 1, 0, 0, 0, 0, tzinfo=tz))
        self.assertEqual(other_morning.hour, 3)
        self.assertEqual(other_morning.minute, 30)
        self.assertEqual(other_morning.year, 2022)
        self.assertEqual(other_morning.month, 1)
        self.assertEqual(other_morning.day, 1)

        last_morning = self.morning_post.get_next_time(
            hourable(2022, 1, 1, 3, 35, tzinfo=tz))
        self.assertEqual(last_morning.day, 2)

    def test_seconds_until(self):
        real_time = self.morning_post.seconds_until_next_time()
        now = hourable.now(tz=tz)
        mock_real_time = 0
        if now.decimal_hours > self.morning_post.timing:
            mock_real_time = (24 - now.decimal_hours) * 60 * 60
        else:
            mock_real_time = -now.decimal_hours * 60 * 60
        mock_real_time += self.morning_post.timing * 60 * 60
        self.assertLessEqual(abs(mock_real_time - real_time), 1)

    def test_dst(self):
        before_leap_ahead = hourable(2022, 3, 12, 3, 30, tzinfo=tz)
        self.assertEqual(
            self.morning_post.seconds_until_next_time(before_leap_ahead),
            23 * 60 * 60)

        before_fall_back = hourable(2022, 11, 5, 3, 30, tzinfo=tz)
        self.assertEqual(
            self.morning_post.seconds_until_next_time(before_fall_back),
            25 * 60 * 60)

        # make sure that we never end up with two posts on the same day, even if
        # the time (1am, in the case of self.dst_post) occurs twice a day (as it
        # does in America/New_York on November 6th 2022)
        after_fall_back = self.dst_post.get_next_time(before_fall_back)
        after_after_fall_back = self.dst_post.get_next_time(after_fall_back)
        self.assertNotEqual(after_fall_back.day, after_after_fall_back.day)

        # one more overkill test to make sure for a whole year and then some
        # that ScheduledPost.get_next_time yields one result for each day
        prev = hourable(2022, 1, 1, 3, tzinfo=tz)
        for _ in range(400):
            next = self.dst_post.get_next_time(prev)
            self.assertEqual((prev + timedelta(days=1)).day, next.day)
            prev = next


if __name__ == "__main__":
    unittest.main()
