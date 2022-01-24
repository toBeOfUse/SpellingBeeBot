from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, AsyncMock
from pathlib import Path
from bot import BeeBot, SpellingBee
import bot
import discord


class BotTest(IsolatedAsyncioTestCase):

    def setUp(self):
        bot.bee_db = "data/mock.db"
        self.bot = BeeBot()
        self.bot.get_channel = Mock()
        discord.Bot.on_connect = AsyncMock()

    async def asyncSetUp(self) -> None:
        await self.bot.on_connect()

    def tearDown(self) -> None:
        Path("data/mock.db").unlink(missing_ok=True)

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
