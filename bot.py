import asyncio
from datetime import datetime
from io import BytesIO
from pprint import pformat
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


def get_message_log(message: discord.Message):
    return pformat(
        {
            "time": str(datetime.now(tz=et)),
            "guild": str(message.guild),
            "channel": str(message.channel),
            "message": message.content,
        },
        sort_dicts=False)


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

    internal_logger = getLogger("BeeBot.Internal")
    internal_logger.setLevel(logging.DEBUG)
    streamhandler = StreamHandler(sys.stdout)
    streamhandler.setLevel(logging.DEBUG)
    internal_logger.addHandler(streamhandler)
    filehandler = FileHandler("logs/BeeBot.log", mode="a+", encoding="utf-8")
    filehandler.setLevel(logging.DEBUG)
    filehandler.setFormatter(file_formatter)
    internal_logger.addHandler(filehandler)

    external_logger = getLogger("BeeBot.External")
    external_logger.setLevel(logging.DEBUG)
    external_file_handler = FileHandler("logs/communication.log",
                                        mode="a+",
                                        encoding="utf-8")
    external_file_handler.setLevel(logging.DEBUG)
    external_file_handler.setFormatter(file_formatter)
    external_logger.addHandler(external_file_handler)

    return internal_logger, external_logger


internal_logger, external_logger = logger_setup()


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

        internal_logger.info("constructing new BeeBot!")

        self.initialized = False
        self.scheduled_jobs: dict[int, aiocron.Cron] = {}

        aiocron.crontab("0 3 * * *", tz=et, func=self.get_new_puzzle)

    async def get_new_puzzle(self):
        self.todays_puzzle_ready = asyncio.create_task(
            self.ensure_todays_puzzle())

    async def on_connect(self):
        """Overriding this to keep pycord from trying to register slash commands
        before they're created in on_ready"""
        pass

    async def on_ready(self):
        internal_logger.info(f"BeeBot ready. In {len(self.guilds)} guilds:")
        internal_logger.info(self.guilds)
        if not self.initialized:
            await self.get_new_puzzle()
            in_guilds = set(x.id for x in self.guilds)
            for scheduled in self.schedule:
                # TODO: execute outstanding posts, if any
                if scheduled.guild_id in in_guilds:
                    self.add_to_cron(scheduled)
                else:
                    internal_logger.warn(
                        "scheduled post for guild that bot is not in!"
                        f" guild id is {scheduled.guild_id}")
                    # TODO: delete ScheduledPost when brave enough
            self.init_responses()
            self.initialized = True

    async def on_guild_join(self, guild: discord.Guild):
        internal_logger.info(f"Added to guild \"{guild}\"!")

    async def on_guild_remove(self, guild: discord.Guild):
        internal_logger.info(f"removed from guild \"{guild}\"")
        self.remove_scheduled_post(guild.id)

    @staticmethod
    def get_current_date():
        return datetime.now(tz=et).strftime("%Y-%m-%d")

    @property
    def guild_ids(self):
        # (self.user is None when running tests that don't connect the bot)
        if self.user and "Test" in self.user.name:
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
            internal_logger.info("retrieving new puzzle...")
            while True:
                try:
                    new_bee = await SpellingBee.fetch_from_nyt()
                    break
                except:
                    await asyncio.sleep(5)
            internal_logger.info("retrieved puzzle from NYT")
            new_bee.persist_to(bee_db)
            internal_logger.info("rendering graphic...")
            await new_bee.render()
            internal_logger.info("rendered graphic for today's puzzle")

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
            internal_logger.info(f"replacing post for guild {new.guild_id}")
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
        # channel
        sending_now = ""
        if hourable.now(tz=et).decimal_hours >= new.timing:
            hadnt_sent_yet = (not existed or not existed.current_session
                              or existed.channel_id != new.channel_id)
            not_up_to_date = hadnt_sent_yet or (SessionBee.retrieve_saved(
                existed.current_session, bee_db).day !=
                                                self.get_current_date())
            if not_up_to_date:
                asyncio.create_task(self.send_scheduled_post(new))
                sending_now = "now and "

        self.add_to_cron(new)

        hours = round(new.seconds_until_next_time() / 60 / 60)
        hours_statement = f"There will be a new puzzle {sending_now}in about {hours} hours."
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
        prefix += f" Current ranking: {bee.get_ranking()}!"
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
            old_session_id = scheduled.current_session
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
            puzzle_message = await channel.send(
                content,
                file=discord.File(
                    BytesIO(bee.image),
                    filename="bee." + bee.image_file_type,
                    description=
                    f"Spelling Bee Puzzle. Center Letter: {bee.center}. " +
                    f"Outside letters: {', '.join(bee.outside)}."))
            external_logger.info(
                f"Outgoing puzzle message:\n{get_message_log(puzzle_message)}")
        status_message = await channel.send(self.get_status_message(bee))
        bee.metadata = {"status_message_id": status_message.id}
        external_logger.info(
            f"Outgoing status message:\n{get_message_log(status_message)}")
        if old_session_id:
            old_session = SessionBee.retrieve_saved(old_session_id, bee_db)
            if old_session and old_session.day != self.get_current_date():
                ungotten = old_session.get_unguessed_words()
                if len(ungotten) >= 2:
                    yesterday_message = (
                        f"(The most common word no one got yesterday was "
                        f"\"{ungotten[-1]};\" the least common word was \"{ungotten[0]}.\")"
                    )
                elif len(ungotten) == 1:
                    yesterday_message = (
                        f"(The only word that no one got yesterday was \"{ungotten[0]}.\")"
                    )
                yesterday_message = await channel.send(yesterday_message)
                external_logger.info(
                    f"Outgoing yesterday message:\n{get_message_log(yesterday_message)}"
                )

    def add_to_cron(self, scheduled: ScheduledPost) -> aiocron.Cron:
        hours = int(scheduled.timing)
        minutes = int(scheduled.timing % 1 * 60)
        seconds = int(scheduled.timing % 1 * 60 % 1 * 60)
        internal_logger.info(
            f"using aiocron to schedule posting job "
            f"for \"{self.get_guild(scheduled.guild_id)}\" "
            f"at {hours:02}:{minutes:02}:{seconds:02} US/Eastern")
        job = aiocron.crontab(f"{minutes} {hours} * * * {seconds}",
                              tz=et,
                              func=self.send_scheduled_post,
                              args=(scheduled, ))
        self.scheduled_jobs[scheduled.guild_id] = job
        return job

    async def respond_to_guesses(self, message: discord.Message):
        guild_id = message.guild.id
        channel_id = message.channel.id
        guessing_session_id = self.session.execute(
            select(ScheduledPost.current_session).where(
                ScheduledPost.guild_id == guild_id
                and ScheduledPost.channel_id == channel_id)).first()
        if guessing_session_id is None or guessing_session_id[0] is None:
            internal_logger.warn(
                f"tried to respond to message attached to no active session: "
                f"guild {message.guild} ({message.guild.id}), "
                f"channel {message.channel} ({message.channel.id}), "
                f"message {message.content} ({message.id})")
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
        if guild_id in self.scheduled_jobs:
            internal_logger.info(
                f"cancelling aiocron job for \"{self.get_guild(guild_id)}\"")
            self.scheduled_jobs[guild_id].stop()
            del self.scheduled_jobs[guild_id]
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
            required=True)):
            "Start receiving Spelling Bees here!"
            response = await self.add_scheduled_post(
                ScheduledPost(guild_id=ctx.guild_id,
                              channel_id=ctx.channel_id,
                              timing=BeeBotConfig.get_hour_for_choice(time)))
            internal_logger.info(
                f"starting to send bees to channel {ctx.channel.name} in {ctx.guild.name}"
            )
            external_logger.info(
                "Incoming command: /start_puzzling\n" +
                f"Responding to /start_puzzling with \"{response}\"")
            await ctx.respond(response)

        @self.slash_command(guild_ids=self.guild_ids)
        async def stop_puzzling(ctx: ApplicationContext):
            "Stop receiving Spelling Bees in this server!"
            existed = self.remove_scheduled_post(ctx.guild_id) is not None
            if not existed:
                response = (
                    "This server was already not receiving Spelling Bee posts!"
                )
            else:
                response = (
                    "Okay! This server will no longer receive Spelling Bee posts."
                )
            external_logger.info(
                "Incoming command: /stop_puzzling\n" +
                f"Responding to /start_puzzling with \"{response}\"")
            await ctx.respond(response)

        @self.slash_command(guild_ids=self.guild_ids)
        async def obtain_hint(ctx: ApplicationContext):
            "Get an up-to-date Spelling Bee hint chart!"
            scheduled: ScheduledPost = self.session.execute(
                select(ScheduledPost).where(
                    ScheduledPost.guild_id == ctx.guild_id)).first()
            if not scheduled:
                response = (
                    "Before using this slash command in this server, use "
                    "/start_puzzling to start getting puzzles!")
                await ctx.respond(response, ephemeral=True)
            elif scheduled[0].channel_id != ctx.channel_id:
                response = (
                    "This slash command is intended for the channel where "
                    f"the Spelling Bees are posted (<#{scheduled[0].channel_id}>)!"
                )
                await ctx.respond(response, ephemeral=True)
            else:
                bee = SessionBee.retrieve_saved(scheduled[0].current_session,
                                                bee_db)
                if bee is None:
                    response = "Wait until a puzzle is posted here first!"
                    await ctx.respond(response, ephemeral=True)
                else:
                    response = bee.get_unguessed_hints(
                    ).format_all_for_discord()
                    await ctx.respond(response)
            external_logger.info(
                "Incoming command: /obtain_hint\n" +
                f"Responding to /obtain_hint with:\n{response}")

        @self.slash_command(guild_ids=self.guild_ids)
        async def explain_rules(ctx: ApplicationContext):
            "Learn the rules of the Spelling Bee!"
            with open("rules-explanation.txt",
                      encoding="utf-8") as explanation_file:
                explanation = explanation_file.read()
                scheduled: ScheduledPost = self.session.execute(
                    select(ScheduledPost).where(
                        ScheduledPost.guild_id == ctx.guild_id)).first()
                if (scheduled is not None
                        and scheduled[0].channel_id != ctx.channel_id):
                    explanation += (
                        "\n(This server is already receiving Spelling Bee posts "
                        f"in the <#{scheduled[0].channel_id}> channel!)")
                external_logger.info(
                    "Incoming command: /explain_rules\n" +
                    f"responding to /explain_rules with: \n{explanation}")
                await ctx.respond(explanation)

        @self.slash_command(guild_ids=self.guild_ids)
        async def help(ctx: ApplicationContext):
            "Have the slash commands explained!"
            with open("commands-explanation.txt",
                      encoding="utf-8") as explanation_file:
                help_message = explanation_file.read()
                external_logger.info(
                    "Incoming command: /help\n" +
                    f"responding to /explain_rules with: \n{help_message}")
                await ctx.respond(help_message)

        @self.event
        async def on_message(message: discord.Message):
            if (not message.author.bot and not message.mention_everyone
                    and message.guild.me.mentioned_in(message)):
                await self.respond_to_guesses(message)
                external_logger.info("Incoming message:\n" +
                                     get_message_log(message))

        asyncio.create_task(self.register_commands())
