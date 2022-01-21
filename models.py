import logging
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Float, Sess
from sqlalchemy.orm import registry

logging.basicConfig()
sqlEngineLog = logging.getLogger('sqlalchemy.engine')
sqlEngineLog.setLevel(logging.INFO)
sqlEngineLog.addHandler(logging.FileHandler("sql.log"))

engine = create_engine("sqlite+pysqlite:///data/schedule.db", future=True)

mapper_registry = registry()
Base = mapper_registry.generate_base()

class ScheduledPost(Base):
    __tablename__ = "schedule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, unique=True, nullable=False)
    channel_id = Column(BigInteger, nullable=False)
    current_session = Column(String)
    timing = Column(Float, nullable=False)

    def __repr__(self):
        return (f"Posting in channel {self.channel_id} (guild {self.guild_id}) at "+
            f"{self.timing} hours. Current session ID is {self.current_session}.")

Base.metadata.create_all(engine)
