from bot import bot

if __name__ == "__main__":
    with open("login_token.txt") as token_file:
        token = token_file.read()
    bot.run(token=token)
