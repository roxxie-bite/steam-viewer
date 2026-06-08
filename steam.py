import os
import asyncio
import logging
from aiohttp import ClientSession, web
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

# Настройка логирования (важно для Render, чтобы видеть логи в дашборде)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Получаем данные из окружения (Render автоматически подставляет переменные)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
STEAM_ID = os.getenv("STEAM_ID")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

last_game_id = None
last_game_name = ""

async def get_steam_status(session: ClientSession):
    """Получает текущий статус игрока из Steam API с обработкой таймаутов"""
    url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={STEAM_ID}"
    
    try:
        # Добавляем таймаут, чтобы запрос не зависал навсегда
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                players = data.get("response", {}).get("players", [])
                if players:
                    player = players[0]
                    return player.get("gameid"), player.get("gameextrainfo")
            else:
                logging.warning(f"Steam API вернул статус: {response.status}")
    except asyncio.TimeoutError:
        logging.error("Таймаут при запросе к Steam API")
    except Exception as e:
        logging.error(f"Ошибка при запросе к Steam API: {e}")
    
    return None, None

async def send_game_update(game_id: str, game_name: str):
    """Отправляет красиво оформленное сообщение в канал"""
    global last_game_id, last_game_name
    
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
            disable_web_page_preview=False
        )
        logging.info(f"✅ Успешно отправлено: {game_name}")
        
        last_game_id = game_id
        last_game_name = game_name
        
    except Exception as e:
        logging.error(f"❌ Ошибка отправки в Telegram: {e}")

async def steam_monitor():
    """Фоновая задача для периодической проверки статуса Steam"""
    global last_game_id
    
    logging.info("🚀 Мониторинг Steam запущен...")
    
    async with ClientSession() as session:
        while True:
            try:
                game_id, game_name = await get_steam_status(session)
                
                if game_id and game_name:
                    if game_id != last_game_id:
                        logging.info(f"🎯 Обнаружена новая игра: {game_name} (ID: {game_id})")
                        await send_game_update(game_id, game_name)
                else:
                    if last_game_id is not None:
                        logging.info("🛑 Игра завершена.")
                        last_game_id = None
                        last_game_name = ""
            except Exception as e:
                logging.error(f"Критическая ошибка в цикле мониторинга: {e}")
            
            await asyncio.sleep(CHECK_INTERVAL)

# --- Web Server для Render (Health Check) ---
async def handle_health(request):
    """Простой эндпоинт, чтобы Render знал, что бот жив"""
    status = "Играет" if last_game_id else "Не играет"
    return web.Response(text=f"Bot is alive! Current status: {status} ({last_game_name or 'Nothing'})")

async def start_web_server():
    """Запускает легкий веб-сервер на порту, который требует Render"""
    app = web.Application()
    app.router.add_get('/', handle_health)
    app.router.add_get('/health', handle_health)
    
    # Render передает порт через переменную окружения PORT, по умолчанию 8080
    port = int(os.getenv("PORT", 8080))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"🌐 Web-сервер для health-check запущен на порту {port}")

@dp.message()
async def echo_handler(message: Message):
    await message.answer(f"Бот работает! 🟢\nПоследняя игра: {last_game_name or 'Не играет'}")

async def main():
    # Запускаем мониторинг и веб-сервер параллельно
    await asyncio.gather(
        steam_monitor(),
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    # Обработка корректного завершения работы (Ctrl+C)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем.")