import os
import asyncio
import logging
from aiohttp import ClientSession
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получаем данные из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
STEAM_ID = os.getenv("STEAM_ID")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальная переменная для хранения последней известной игры
last_game_id = None
last_game_name = ""

async def get_steam_status(session: ClientSession):
    """Получает текущий статус игрока из Steam API"""
    url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={STEAM_ID}"
    
    try:
        async with session.get(url) as response:
            data = await response.json()
            players = data.get("response", {}).get("players", [])
            
            if not players:
                return None
            
            player = players[0]
            game_id = player.get("gameid")
            game_name = player.get("gameextrainfo")
            
            return game_id, game_name
    except Exception as e:
        logging.error(f"Ошибка при запросе к Steam API: {e}")
        return None

async def send_game_update(game_id: str, game_name: str):
    """Отправляет красиво оформленное сообщение в канал"""
    global last_game_id, last_game_name
    
    # Формируем красивое сообщение с использованием HTML-разметки
    # Ссылка на страницу игры в Steam Store
    store_link = f"https://store.steampowered.com/app/{game_id}"
    
    message = (
        "🎮 <b>Обновление статуса!</b>\n\n"
        f"👤 <b>Игрок:</b> <a href='https://steamcommunity.com/profiles/{STEAM_ID}'>Мой Steam профиль</a>\n"
        f"🕹 <b>Начал играть в:</b> <a href='{store_link}'>{game_name}</a>\n\n"
        "🔥 <i>Приятной игры!</i>"
    )
    
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=False # Можно включить True, чтобы была карточка ссылки
        )
        logging.info(f"Отправлено сообщение об игре: {game_name}")
        
        # Обновляем состояние
        last_game_id = game_id
        last_game_name = game_name
        
    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения в Telegram: {e}")

async def steam_monitor():
    """Фоновая задача для периодической проверки статуса Steam"""
    global last_game_id
    
    logging.info("Мониторинг Steam запущен...")
    
    async with ClientSession() as session:
        while True:
            game_id, game_name = await get_steam_status(session)
            
            if game_id and game_name:
                # Если игра изменилась или это первая проверка
                if game_id != last_game_id:
                    logging.info(f"Обнаружена новая игра: {game_name} (ID: {game_id})")
                    await send_game_update(game_id, game_name)
            else:
                # Если игрок перестал играть, сбрасываем состояние
                if last_game_id is not None:
                    logging.info("Игра завершена.")
                    last_game_id = None
                    last_game_name = ""
                    # Опционально: можно отправлять сообщение "Закончил играть в X"
            
            # Ждем перед следующей проверкой
            await asyncio.sleep(CHECK_INTERVAL)

@dp.message()
async def echo_handler(message: Message):
    """Простой обработчик для проверки того, что бот жив (отвечает в личку)"""
    await message.answer(f"Бот работает! Последняя известная игра: {last_game_name or 'Не играет'}")

async def main():
    # Запускаем фоновую задачу мониторинга Steam
    asyncio.create_task(steam_monitor())
    
    # Запускаем бота
    logging.info("Запуск бота...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())