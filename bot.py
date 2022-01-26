import asyncio
from datetime import datetime
from io import BytesIO
import random
from typing import Optional
import sys
import logging
from logging import FileHandler, getLogger, StreamHandler
from zoneinfo import ZoneInfo

import aiocron
import discord
from bee_engine import SessionBee, SpellingBee
from discord.commands import ApplicationContext, Option
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import ScheduledPost, create_db, hourable

bee_db = "data/bee.db"
schedule_db = "data/schedule.db"
et = ZoneInfo("America/New_York")


def logger_setup():
    file_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                       "%Y-%m-%d %H:%M:%S")

    discord_logger = getLogger("discord")
    for handler in discord_logger.handlers:
        discord_logger.removeHandler(handler)
    discord_logger.setLevel(logging.DEBUG)
    discord_file_handler = FileHandler("logs/discord.log",
                                       mode="a+",
                                       encoding="utf-8")
    discord_file_handler.setFormatter(file_formatter)
    discord_logger.addHandler(discord_file_handler)
    discord_stream_handler = StreamHandler(sys.stdout)
    discord_stream_handler.setLevel(logging.WARNING)
    discord_logger.addHandler(discord_stream_handler)

    logger = getLogger("BeeBot")
    logger.setLevel(logging.DEBUG)
    streamhandler = StreamHandler(sys.stdout)
    streamhandler.setLevel(logging.DEBUG)
    logger.addHandler(streamhandler)
    filehandler = (FileHandler("logs/BeeBot.log", mode="a+", encoding="utf-8"))
    filehandler.setLevel(logging.DEBUG)
    filehandler.setFormatter(file_formatter)
    logger.addHandler(filehandler)
    return logger


logger = logger_setup()


class BeeBotConfig:
    # Times from the beginning of the day in US/Eastern in hours
    timing_choices = {
        "Morning": 7,
        "Noon": 12,
        "Afternoon": 16,
        "Evening": 20,
        "As soon as a new puzzle is available": 3,
        "Now, and 24 hours from now, and so on": -1
    }

    @classmethod
    def get_timing_choices(cls) -> list[str]:
        return list(cls.timing_choices.keys())

    @classmethod
    def get_hour_for_choice(cls, choice: str) -> float:
        assert choice in cls.timing_choices
        hour = cls.timing_choices[choice]
        if hour == -1:
            return hourable.now(tz=et).decimal_hours
        else:
            return hour


class BeeBot(discord.Bot):

    def __init__(self) -> None:
        super().__init__()
        self.db_engine = create_db(schedule_db)
        self.session = Session(self.db_engine)
        self.todays_puzzle_ready: Optional[asyncio.Task] = None
        """Must be awaited to be sure that today's puzzle is available. The Task
        is created in on_connect; thus, no puzzles can be sent before on_connect
        runs (which makes sense anyway.)"""

        logger.info("constructing new BeeBot!")

        self.initialized = False

        aiocron.crontab("0 3 * * *", tz=et, func=self.get_new_puzzle)

    async def get_new_puzzle(self):
        self.todays_puzzle_ready = asyncio.create_task(
            self.ensure_todays_puzzle())

    async def on_connect(self):
        logger.info("BeeBot connected")
        if not self.initialized:
            await self.get_new_puzzle()
            for scheduled in self.schedule:
                # TODO: execute outstanding posts
                asyncio.create_task(self.daily_loop(scheduled))
            self.init_responses()
            self.initialized = True

    @staticmethod
    def get_current_date():
        return datetime.now(tz=et).strftime("%Y-%m-%d")

    @property
    def guild_ids(self):
        if "Test" in self.user.name:
            return [708955889276551198]
        else:
            return None

    async def ensure_todays_puzzle(self):
        """
        If a SpellingBee for the current puzzle doesn't exist, retrieve it
        and render the image. This method only needs to be called once a day, to
        avoid fetching or rendering the same puzzle multiple times
        simultaneously; the coroutine object it returns can be stored and
        awaited to ensure the day's puzzle is available subsequently.
        """
        if SpellingBee.retrieve_saved(self.get_current_date(), bee_db) is None:
            logger.info("retrieving new puzzle...")
            new_bee = await SpellingBee.fetch_from_nyt()
            logger.info("retrieved puzzle from NYT")
            new_bee.persist_to(bee_db)
            logger.info("rendering graphic...")
            await new_bee.render()
            logger.info("rendered graphic for today's puzzle")

    @property
    def schedule(self) -> list[ScheduledPost]:
        return list(x[0] for x in self.session.execute(select(ScheduledPost)))

    async def add_scheduled_post(self, new: ScheduledPost) -> str:
        """
        Adds a new ScheduledPost to the internal schedule. If the time of day
        for the given scheduled post has passed and there isn't already an
        active session for this day's puzzle for this channel, a post will be
        immediately sent. Responds with a status update message.
        """
        existed = self.remove_scheduled_post(new.guild_id)
        if existed is not None:
            logger.info(f"replacing post for guild {new.guild_id}")
        # if we're replacing an old scheduled post, the new one inherits the
        # current_session of the old one so that people can keep making guesses
        # in the channel with the same session (albeit possibly only Very
        # briefly)
        if existed is not None and existed.current_session is not None:
            new.current_session = existed.current_session
        self.session.add(new)
        self.session.flush()
        self.session.commit()

        # immediately send a puzzle if the time for the puzzle to be sent today
        # has passed and there wasn't already a puzzle for this day in this
        # channel already
        if hourable.now(tz=et).decimal_hours >= new.timing:
            hadnt_sent_yet = (not existed or not existed.current_session
                              or existed.channel_id != new.channel_id)
            not_up_to_date = hadnt_sent_yet or (SessionBee.retrieve_saved(
                existed.current_session, bee_db).day !=
                                                self.get_current_date())
            if not_up_to_date:
                await self.send_scheduled_post(new)

        asyncio.create_task(self.daily_loop(new))

        hours = round(new.seconds_until_next_time() / 60 / 60)
        # TODO: use timestamp embedding
        hours_statement = f"There will be a new puzzle in about {hours} hours."
        if existed is not None:
            if existed.channel_id != new.channel_id:
                return (
                    "Great! This channel will now receive puzzle posts instead "
                    + "of that other one. " + hours_statement)
            elif existed.timing != new.timing:
                return (
                    f"Great! This channel will now receive puzzles at a new " +
                    "time. " + hours_statement)
            else:
                return "Great! Nothing will change."
        else:
            return f"Great! This channel is now On the Schedule. " + hours_statement

    @staticmethod
    def get_status_message(bee: SessionBee):
        prefix = "Words found so far: "
        prefix += bee.list_gotten_words(enclose_with=["||", "||"])
        return prefix

    async def send_scheduled_post(self, scheduled: ScheduledPost):
        """
        Creates a new SessionBee with the latest SpellingBee puzzle; persists
        it, sends a message with its graphic, creates a status message, and
        stores the ID of that so it can be updated later.
        """
        channel = self.get_channel(scheduled.channel_id)
        async with channel.typing():
            await self.todays_puzzle_ready
            bee_base = SpellingBee.retrieve_saved(db_path=bee_db)
            while bee_base.day != self.get_current_date():
                await asyncio.sleep(5)
                await self.todays_puzzle_ready
                bee_base = SpellingBee.retrieve_saved(db_path=bee_db)
            bee = SessionBee(bee_base)
            bee.persist_to(bee_db)
            scheduled.current_session = bee.session_id
            self.session.add(scheduled)
            self.session.flush()
            self.session.commit()
            await asyncio.sleep(1)

            def datesuffix(d: int):
                return str(d) + ('th' if 11 <= d <= 13 else {
                    1: 'st',
                    2: 'nd',
                    3: 'rd'
                }.get(d % 10, 'th'))

            def dateformat(d: datetime):
                return d.strftime("%A, %B ") + datesuffix(d.day)

            sentiments = [
                "and the Spelling Bee's gears are a-grinding.",
                "for better or worse!",
                "and tri-axle trucks are triangulating your location.",
                "and today's quotidian bread is seeming a little more daily than usual.",
                "and yet the world spins on.", "and don't they know it.",
                "and the sky is taking the day off today.",
                "and the sky is looking a little bluer today.",
                "and \"unmute\" and \"echolocate\" are still words.",
                "despite our best efforts.",
                "and I still don't have a real job."
            ]
            content = (
                f"Good morning. It's {dateformat(datetime.now(tz=et))} in "
                f"New York City, {random.choice(sentiments)} Reply to "
                "this message with words that fit to help complete today's puzzle."
            )
            await channel.send(content,
                               file=discord.File(BytesIO(bee.image),
                                                 "bee." + bee.image_file_type))
        status_message = await channel.send(self.get_status_message(bee))
        bee.metadata = {"status_message_id": status_message.id}

    async def daily_loop(self, scheduled: ScheduledPost):
        while True:
            seconds = scheduled.seconds_until_next_time()
            logger.info(
                f"sending puzzle to guild {self.get_guild(scheduled.guild_id).name} "
                f"in {seconds/60/60} hours")
            await asyncio.sleep(seconds)
            # exit the loop if the timing has changed for this scheduled post
            # while we were sleeping
            live_scheduled_post_row = self.session.execute(
                select(ScheduledPost.timing).where(
                    ScheduledPost.id == scheduled.id)).first()
            if (live_scheduled_post_row is None
                    or live_scheduled_post_row[0] != scheduled.timing):
                break
            await self.send_scheduled_post(scheduled)
            logger.info(
                f"sent puzzle to guild {self.get_guild(scheduled.guild_id).name}"
            )
            # just to make completely sure we won't double post
            await asyncio.sleep(1)

    async def respond_to_guesses(self, message: discord.Message):
        guild_id = message.guild.id
        channel_id = message.channel.id
        guessing_session_id = self.session.execute(
            select(ScheduledPost.current_session).where(
                ScheduledPost.guild_id == guild_id
                and ScheduledPost.channel_id == channel_id)).first()
        if guessing_session_id is None or guessing_session_id[0] is None:
            logger.warn(
                f"tried to respond to message attached to no active session "
                "(guild: {message.guild.id}, channel: {message.channel.id}, "
                "message: {message.id}})")
            return
        bee = SessionBee.retrieve_saved(guessing_session_id[0], bee_db)
        bee.persist_to(bee_db)
        reactions = bee.respond_to_guesses(message.content)
        for reaction in reactions:
            await message.add_reaction(reaction)
        status_message = await message.channel.fetch_message(
            bee.metadata["status_message_id"])
        await status_message.edit(content=self.get_status_message(bee))

    def remove_scheduled_post(self, guild_id: int) -> Optional[ScheduledPost]:
        """
        Removes the scheduled post for this guild from the database if it
        exists; returns it, in that case (the current_session field may need to
        be copied to a new scheduled post for this channel.)
        """
        existing = self.session.execute(
            select(ScheduledPost).where(
                ScheduledPost.guild_id == guild_id)).fetchone()
        if existing is not None:
            self.session.delete(existing[0])
            self.session.flush()
            self.session.commit()
            return existing[0]
        return None

    def init_responses(self):

        @self.slash_command(guild_ids=self.guild_ids)
async def start_puzzling(ctx: ApplicationContext, time: Option(
            str,
            "Time of day in NYC. If the time has passed today, you'll also "
    "receive a puzzle immediately.",
    choices=BeeBotConfig.get_timing_choices(),
    default=BeeBotConfig.get_timing_choices()[0])):
    "Start receiving Spelling Bees here!"
            response = await self.add_scheduled_post(
        ScheduledPost(guild_id=ctx.guild_id,
                      channel_id=ctx.channel_id,
                      timing=BeeBotConfig.get_hour_for_choice(time)))
    logger.info(
        f"starting to send bees to channel {ctx.channel.name} in {ctx.guild.name}"
    )
    logger.info(f"notifying with response {response}")
    await ctx.respond(response)

        @self.slash_command(guild_ids=self.guild_ids)
async def stop_puzzling(ctx: ApplicationContext):
    "Stop receiving Spelling Bees here!"
            existed = self.remove_scheduled_post(ctx.guild_id) is not None
    if not existed:
        await ctx.respond(
                    "This channel was already not receiving Spelling Bee posts!"
                )
    else:
        await ctx.respond(
                    "Okay! This channel will no longer receive Spelling Bee posts."
                )

        @self.slash_command(guild_ids=self.guild_ids)
async def obtain_hint(ctx: ApplicationContext):
            "Get an up-to-date Spelling Bee hint chart!"
            scheduled: ScheduledPost = self.session.execute(
        select(ScheduledPost).where(
            ScheduledPost.guild_id == ctx.guild_id)).first()
            if not scheduled:
                await ctx.respond(
                    "Before using this slash command in this server, use "
                    "/start_puzzling to start getting puzzles!",
                    ephemeral=True)
            elif scheduled.channel_id != ctx.channel_id:
        await ctx.respond(
            "This slash command is intended for the channel where "
            "the Spelling Bees are posted (<#{scheduled.channel_id}>)!",
            ephemeral=True)
    else:
                bee = SessionBee.retrieve_saved(scheduled.current_session,
                                                bee_db)
        hints = bee.get_unguessed_hints().format_all_for_discord()
        await ctx.respond(hints)

        @self.slash_command(guild_ids=self.guild_ids)
        async def explain_rules(ctx: ApplicationContext):
            "Learn the rules of the Spelling Bee!"
            with open("explanation.txt", encoding="utf-8") as explanation_file:
                await ctx.respond(explanation_file.read())

        @self.event
async def on_message(message: discord.Message):
            if not message.mention_everyone and message.guild.me.mentioned_in(
                    message):
                await self.respond_to_guesses(message)

        asyncio.create_task(self.register_commands())
