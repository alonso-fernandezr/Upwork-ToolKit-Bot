import telegram
import asyncio
import os
# Load environment variables
# Use os.getenv to retrieve environment variables
TELEGRAM_TOKEN = "8585630120:AAE61pxR7UQafiB8upZG4HVXfmEHCPp9gVA"
async def send_mail():
    print('send_mail')
    bot = telegram.Bot(TELEGRAM_TOKEN)
    async with bot:
        print(await bot.get_me())
        chat_id = (await bot.get_updates())
        print(chat_id)
if __name__ == "__main__":
    asyncio.run(send_mail())