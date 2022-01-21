import discord
from discord.commands import ApplicationContext, Option
from sqlalchemy import select
from sqlalchemy.orm import Session
from models import ScheduledPost, engine

class BeeBotConfig:
    # Times from the beginning of the day in US/Eastern in hours
    timing_choices = {
        "Morning": 7,
        "Noon": 12,
        "Afternoon": 16,
        "Evening": 20,
        "ASAP": 3
    }


class BeeBot(discord.Bot):
    def __init__(self) -> None:
        super().__init__()
        self.session = Session(engine)
        # TODO: iterate over schedule, execute any outstanding scheduled posts,
        # schedule rest for next execution
    
    @property
    def schedule(self) -> list[ScheduledPost]:
        return list(
            x[0] for x in
            self.session.execute(select(ScheduledPost))
        )
    
    def add_scheduled_post(self, new: ScheduledPost):
        """Adds a new ScheduledPost to the internal schedule. If there is
        already a scheduled post for this guild, it will be removed from the
        schedule. If the time of day for the given scheduled post has passed and
        there isn't already an active session for this day's puzzle for this
        channel, the post will be immediately sent."""
        existing = self.session.execute(
            select(ScheduledPost).where(ScheduledPost.guild_id == new.guild_id)
        ).fetchone()
        if existing is not None:
            self.session.delete(existing)
        self.session.add(new)
        self.session.flush()


bot = BeeBot()

@bot.slash_command
async def start_puzzling(
    ctx: ApplicationContext,
    time: Option(
        str, 
        "What time? (US/Eastern)", 
        choices=list(BeeBotConfig.timing_choices.keys()),
        default=list(BeeBotConfig.timing_choices.keys())[0]
    )
):
    "Set this channel as the channel in which the SpellingBee bot will "
    "post a Spelling Bee every day."
    new_one = ScheduledPost(
        guild_id=ctx.guild_id,
        channel_id=ctx.channel_id,
        timing=BeeBotConfig[time]
    )
    bot.add_scheduled_post(new_one)
