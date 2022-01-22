from typing import Optional
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

# https://discord.com/api/oauth2/authorize?client_id=933882667378827294&permissions=274877941824&scope=bot%20applications.commands
class BeeBot(discord.Bot):
    def __init__(self) -> None:
        super().__init__()
        self.session = Session(engine)
        # TODO: iterate over schedule, execute any outstanding scheduled posts,
        # schedule rest for next execution
    
    async def on_connect(self):
        await super().on_connect()
        print("BeeBot connected")
    
    @property
    def schedule(self) -> list[ScheduledPost]:
        return list(
            x[0] for x in
            self.session.execute(select(ScheduledPost))
        )
    
    def add_scheduled_post(self, new: ScheduledPost) -> str:
        """
        Adds a new ScheduledPost to the internal schedule. If the time of day
        for the given scheduled post has passed and there isn't already an
        active session for this day's puzzle for this channel, the post will be
        immediately sent. Responds with a status update message.
        """
        existed = self.remove_scheduled_post(new.guild_id)
        self.session.add(new)
        self.session.flush()
        self.session.commit()
        
        # TODO: that other stuff

        hours = round(new.seconds_until_next_time()/60/60)
        hours_statement = f"The next puzzle will be in about {hours} hours."
        if existed is not None:
            if existed.channel_id != new.channel_id:
                return (
                    "Great! This channel will now receive puzzle posts instead "+
                    "of that other one. " + hours_statement
                )
            elif existed.timing != new.timing:
                return (
                    f"Great! This channel will now receive puzzles at a new "+
                    "time. "+hours_statement
                )
            else:
                return "Great! Nothing will change."
        else:
            return f"Great! This channel is now On the Schedule. "+hours_statement
    
    def remove_scheduled_post(self, guild_id: int) -> Optional[ScheduledPost]:
        """
        Removes the scheduled post for this guild from the database if it
        exists; returns it, in that case.
        """
        existing = self.session.execute(
            select(ScheduledPost).where(ScheduledPost.guild_id == guild_id)
        ).fetchone()
        if existing is not None:
            self.session.delete(existing[0])
            self.session.flush()
            self.session.commit()
            return existing[0]
        return None


bot = BeeBot()
guild_ids=[708955889276551198]

@bot.slash_command(guild_ids=guild_ids)
async def start_puzzling(
    ctx: ApplicationContext,
    time: Option(
        str, 
        "What time? This is based on the New York Times' time zone.", 
        choices=list(BeeBotConfig.timing_choices.keys()),
        default=list(BeeBotConfig.timing_choices.keys())[0]
    )
):
    "Start receiving Spelling Bees here!"
    await ctx.respond(
        bot.add_scheduled_post(
            ScheduledPost(
                guild_id=ctx.guild_id,
                channel_id=ctx.channel_id,
                timing=BeeBotConfig.timing_choices[time]
            )
        )
    )
    

@bot.slash_command(guild_ids=guild_ids)
async def stop_puzzling(ctx: ApplicationContext):
    "Stop receiving Spelling Bees here!"
    existed = bot.remove_scheduled_post(ctx.guild_id) is not None
    if existed:
        await ctx.respond("This channel was already not receiving Spelling Bee posts!")
    else:
        await ctx.respond("Okay! This channel will no longer receive Spelling Bee posts.")
