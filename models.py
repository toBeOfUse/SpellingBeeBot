from datetime import datetime, timedelta
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Float
from sqlalchemy.orm import registry

sqlEngineLog = logging.getLogger('sqlalchemy.engine')
sqlEngineLog.setLevel(logging.INFO)
sqlEngineLog.addHandler(logging.FileHandler("sql.log"))

mapper_registry = registry()
Base = mapper_registry.generate_base()

tz = ZoneInfo("America/New_York")


class hourable(datetime):

    @property
    def decimal_hours(self):
        return self.hour + self.minute / 60 + self.second / 3600


class ScheduledPost(Base):
    __tablename__ = "schedule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False)
    channel_id = Column(BigInteger, nullable=False)
    current_session = Column(String)
    timing = Column(Float, nullable=False)

    def __repr__(self):
        return (
            f"Posting in channel {self.channel_id} (guild {self.guild_id}) at "
            +
            f"{self.timing} hours. Current session ID is {self.current_session}."
        )

    def get_next_time(self,
                      starting_from: Optional[datetime] = None) -> datetime:
        if starting_from is None:
            base = hourable.now(tz=tz)
        else:
            base = hourable.fromtimestamp(starting_from.timestamp(), tz=tz)
        baseHours = base.decimal_hours
        if baseHours >= self.timing:
            base += timedelta(days=1)
        return datetime(year=base.year,
                        month=base.month,
                        day=base.day,
                        hour=int(self.timing),
                        minute=int((self.timing % 1) * 60),
                        tzinfo=tz)

    def seconds_until_next_time(self,
                                starting_from: Optional[datetime] = None
                                ) -> float:
        base = starting_from or datetime.now(tz=tz)
        return (self.get_next_time(starting_from).astimezone(ZoneInfo("UTC")) -
                base.astimezone(ZoneInfo("UTC"))).total_seconds()


def create_db(db_path: str):
    engine = create_engine("sqlite+pysqlite:///" + db_path, future=True)
Base.metadata.create_all(engine)
    return engine


if __name__ == "__main__":
    print("Current Time:")
    print(datetime.now(tz=tz))
    test = ScheduledPost(guild_id=-1, channel_id=-1, timing=7)
    print("7:00 Eastern:")
    print(test.get_next_time())
    print("which is in:")
    print(test.get_next_time() - datetime.now(tz=tz))

    print("From March 12th, 2022:")
    dst_base = datetime(2022, 3, 12, 7, tzinfo=tz)
    dst_result = test.get_next_time(dst_base)
    print(dst_result)
    print("which is this much later:")
    print(timedelta(seconds=test.seconds_until_next_time(dst_base)))

    print("From 7am on November 5th, 2022:")
    dst_base = datetime(2022, 11, 5, 7, tzinfo=tz)
    print(test.get_next_time(dst_base))
    print("which is this much later:")
    print(timedelta(seconds=test.seconds_until_next_time(dst_base)))

    print("or, from 3am on November 5th:")
    test = ScheduledPost(guild_id=-1, channel_id=-1, timing=3)
    dst_base = datetime(2022, 11, 5, 3, tzinfo=tz)
    print(test.get_next_time(dst_base))
    print("which is this much later:")
    print(timedelta(seconds=test.seconds_until_next_time(dst_base)))
