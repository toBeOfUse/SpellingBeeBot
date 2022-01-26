from bot import BeeBot

if __name__ == "__main__":
    with open("login_token.txt") as token_file:
        token = token_file.read()
    bot = BeeBot()
    bot.run(token=token)
