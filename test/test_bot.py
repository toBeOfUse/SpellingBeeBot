import asyncio
from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, AsyncMock, patch
from pathlib import Path
from bot import BeeBot, SpellingBee, et
import bot
import discord

from models import ScheduledPost, hourable

test_post_data = {"guild_id": -1, "channel_id": -1}


class BotTest(IsolatedAsyncioTestCase):

    def setUp(self):
        bot.bee_db = "data/mock_puzzles.db"
        bot.schedule_db = "data/mock_schedule.db"
        self.bot = BeeBot()
        discord.Bot.on_connect = AsyncMock(name="Bot.on_connect")
        discord.Bot.get_guild = Mock(name="get_guild")
        typing_manager = Mock(name="Channel.typing() result")
        typing_manager.__aenter__ = AsyncMock()
        typing_manager.__aexit__ = AsyncMock()
        channel = Mock(name="Channel")
        channel.typing = Mock(name="Channel.typing()",
                              return_value=typing_manager)
        message = Mock()
        message.id = -1
        channel.send = AsyncMock(name="Channel.send", return_value=message)
        discord.Bot.get_channel = Mock(name="Bot.get_channel",
                                       return_value=channel)

    async def asyncSetUp(self) -> None:
        await self.bot.on_connect()

    def tearDown(self) -> None:
        self.bot.session.close()
        self.bot.db_engine.dispose()
        Path("data/mock_puzzles.db").unlink(missing_ok=True)
        Path("data/mock_schedule.db").unlink(missing_ok=True)

    async def test_date_string(self):
        with patch("bot.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2022, 1, 1)
            self.assertEqual(BeeBot.get_current_date(), "2022-01-01")

    async def test_ensure_puzzle(self):
        self.assertIsNone(SpellingBee.retrieve_saved(db_path=bot.bee_db))
        await self.bot.todays_puzzle_ready
        created = SpellingBee.retrieve_saved(db_path=bot.bee_db)
        self.assertIsNotNone(created)
        self.assertIsInstance(created.image, bytes)
        self.assertEqual(created.day, BeeBot.get_current_date())
        self.bot.ensure_todays_puzzle = Mock()
        await self.bot.todays_puzzle_ready
        self.bot.ensure_todays_puzzle.assert_not_called()

    async def test_loop_fires(self):
        test_post = ScheduledPost(**test_post_data,
                                  timing=hourable.now(tz=et).decimal_hours +
                                  1 / 60 / 60)
        self.bot.send_scheduled_post = AsyncMock()
        await self.bot.add_scheduled_post(test_post)
        await asyncio.sleep(2)
        self.bot.send_scheduled_post.assert_called_once_with(test_post)

    async def test_loop_cancels(self):
        test_post = ScheduledPost(**test_post_data,
                                  timing=hourable.now(tz=et).decimal_hours +
                                  1 / 60 / 60)
        self.bot.send_scheduled_post = AsyncMock()
        await self.bot.add_scheduled_post(test_post)
        self.bot.remove_scheduled_post(test_post.guild_id)
        await self.bot.todays_puzzle_ready
        await asyncio.sleep(2)
        self.bot.send_scheduled_post.assert_not_called()
        self.bot.send_scheduled_post.assert_not_awaited()

    async def test_schedule_attr(self):
        test_post = ScheduledPost(**test_post_data, timing=0)
        await self.bot.add_scheduled_post(test_post)
        self.assertEqual(len(self.bot.schedule), 1)
        retrieved = self.bot.schedule[0]
        self.assertEqual(test_post.id, retrieved.id)
        self.assertEqual(test_post.guild_id, retrieved.guild_id)
        self.assertEqual(test_post.channel_id, retrieved.channel_id)
        self.assertEqual(test_post.timing, test_post.timing)
        self.bot.remove_scheduled_post(test_post.guild_id)
        self.assertEqual(len(self.bot.schedule), 0)

    async def test_add_post(self):
        pass

    async def test_remove_post(self):
        pass