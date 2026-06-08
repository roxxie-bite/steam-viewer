import os
import asyncio
import logging
from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher
from aiogram.types import Message, LinkPreviewOptions
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
STEAM_ID = os.getenv("STEAM_ID")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")

# ==========================================
# 🎨 НАСТРОЙКА ЭМОДЗИ
# ==========================================
EMOJIS = {
    "GAME": "🎮", 
    "PLAYER": "👤",
    "DEVELOPER": "👨‍💻",
    "PUBLISHER": "🏢",
    "RATING": "⭐",
    "TAG": "🏷",
    "TIME": "⏱",
    "LINK": "📱",
    "SPARKLES": "✨",
    "TARGET": "🎯",
    "CHECK": "✅",
    "SLEEP": "😴",
    "STOP": "🛑",
    "SEPARATOR": "|"
}
# ==========================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные состояния
last_game_id = None
last_game_name = ""
last_message_id = None
current_playtime_str = ""

# Кэш для хранения деталей текущей игры (чтобы не запрашивать их при каждом обновлении времени)
cached_game_details = {
    "developers": "Unknown",
    "publishers": "Unknown",
    "genres": "Unknown",
    "metacritic": None,
    "image_url": ""
}

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

def build_game_caption(game_name: str, devs: str, pubs: str, meta, genres: str, playtime: str, store_link: str) -> str:
    """Собирает текст сообщения из компонентов"""
    parts = [
        f"{EMOJIS['GAME']} {EMOJIS['SEPARATOR']} Сейчас играю в: <b>{game_name}</b>",
        f"{EMOJIS['DEVELOPER']} {EMOJIS['SEPARATOR']} Разработчики: {devs}",
        f"{EMOJIS['PUBLISHER']} {EMOJIS['SEPARATOR']} Издатели: {pubs}",
    ]
    if meta:
        parts.append(f"{EMOJIS['RATING']} {EMOJIS['SEPARATOR']} Оценка Metacritic: {meta}/100")
    
    parts.append(f"{EMOJIS['TAG']} {EMOJIS['SEPARATOR']} Жанры: {genres}")
    parts.append(f"{EMOJIS['TIME']} {EMOJIS['SEPARATOR']} Время в игре: {playtime}")
    parts.append("")
    parts.append(f"{EMOJIS['LINK']} {EMOJIS['SEPARATOR']} <a href='{store_link}'>Ссылка на игру</a>")
    
    return "\n".join(parts)

async def get_steam_status(session: ClientSession):
    url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={STEAM_ID}"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                players = data.get("response", {}).get("players", [])
                if players:
                    player = players[0]
                    return player.get("gameid"), player.get("gameextrainfo")
    except Exception as e:
        logging.error(f"Ошибка Steam API: {e}")
    return None, None

async def get_game_details(session: ClientSession, game_id: str) -> dict:
    url = f"https://store.steampowered.com/api/appdetails?appids={game_id}"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                if data.get(str(game_id), {}).get("success"):
                    game_data = data[str(game_id)]["data"]
                    return {
                        "developers": ", ".join(game_data.get("developers", ["Unknown"])),
                        "publishers": ", ".join(game_data.get("publishers", ["Unknown"])),
                        "genres": ", ".join([genre["description"] for genre in game_data.get("genres", [])]),
                        "metacritic": game_data.get("metacritic", {}).get("score"),
                        "image_url": game_data.get("header_image", f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game_id}/header.jpg")
                    }
    except Exception as e:
        logging.error(f"Ошибка при получении деталей игры {game_id}: {e}")
    
    # Fallback
    return {
        "developers": "Unknown", "publishers": "Unknown", "genres": "Unknown",
        "metacritic": None, "image_url": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game_id}/header.jpg"
    }

async def get_player_game_time(session: ClientSession, game_id: str) -> int:
    url = f"http://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?key={STEAM_API_KEY}&steamid={STEAM_ID}&include_appinfo=1"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                games = data.get("response", {}).get("games", [])
                for game in games:
                    if str(game.get("appid")) == str(game_id):
                        return game.get("playtime_forever", 0)
    except Exception as e:
        logging.error(f"Ошибка при получении времени в игре: {e}")
    return 0

def format_playtime(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0 and mins > 0:
        return f"{hours} ч. {mins} мин."
    elif hours > 0:
        return f"{hours} ч."
    else:
        return f"{mins} мин."

async def delete_old_message():
    global last_message_id
    if last_message_id:
        try:
            await bot.delete_message(chat_id=CHANNEL_ID, message_id=last_message_id)
            logging.info(f"🗑️ Старое сообщение {last_message_id} удалено")
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение {last_message_id}: {e}")
        finally:
            last_message_id = None

async def send_idle_message():
    global last_message_id
    message = (
        f"{EMOJIS['SLEEP']} {EMOJIS['SEPARATOR']} <b>Сейчас не играю</b>\n\n"
        f"{EMOJIS['PLAYER']} {EMOJIS['SEPARATOR']} <a href='https://steamcommunity.com/profiles/{STEAM_ID}'>Мой профиль в Steam</a>"
    )
    try:
        msg = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode="HTML",
            link_preview_options=NO_PREVIEW
        )
        last_message_id = msg.message_id
        logging.info("✅ Отправлено сообщение о простое")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки сообщения о простое: {e}")

async def steam_monitor():
    global last_game_id, last_game_name, last_message_id, current_playtime_str, cached_game_details
    
    logging.info("🚀 Мониторинг Steam запущен...")
    
    async with ClientSession() as session:
        while True:
            try:
                game_id, game_name = await get_steam_status(session)
                
                if game_id and game_name:
                    if game_id != last_game_id:
                        # 🆕 СЦЕНАРИЙ 1: Начал играть в НОВУЮ игру
                        logging.info(f"🎯 Обнаружена новая игра: {game_name} (ID: {game_id})")
                        await delete_old_message()
                        
                        # Получаем полные данные
                        details = await get_game_details(session, game_id)
                        playtime_minutes = await get_player_game_time(session, game_id)
                        playtime_str = format_playtime(playtime_minutes)
                        
                        # Сохраняем в кэш
                        cached_game_details = details
                        
                        store_link = f"https://store.steampowered.com/app/{game_id}"
                        caption = build_game_caption(
                            game_name, details["developers"], details["publishers"], 
                            details["metacritic"], details["genres"], playtime_str, store_link
                        )
                        
                        try:
                            msg = await bot.send_photo(
                                chat_id=CHANNEL_ID,
                                photo=details["image_url"],
                                caption=caption,
                                parse_mode="HTML"
                            )
                            last_message_id = msg.message_id
                            logging.info(f"✅ Отправлено новое сообщение: {game_name}")
                        except Exception as e:
                            logging.error(f"❌ Ошибка отправки фото: {e}")
                            # Fallback на текст
                            msg = await bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode="HTML", link_preview_options=NO_PREVIEW)
                            last_message_id = msg.message_id

                        # Обновляем глобальные переменные
                        last_game_id = game_id
                        last_game_name = game_name
                        current_playtime_str = playtime_str
                        
                    else:
                        # 🔄 СЦЕНАРИЙ 2: Игра та же, проверяем, обновилось ли время
                        new_playtime_minutes = await get_player_game_time(session, game_id)
                        new_playtime_str = format_playtime(new_playtime_minutes)
                        
                        if new_playtime_str != current_playtime_str:
                            logging.info(f"✏️ Время в игре изменилось: {new_playtime_str}. Редактируем сообщение...")
                            
                            store_link = f"https://store.steampowered.com/app/{game_id}"
                            new_caption = build_game_caption(
                                last_game_name, cached_game_details["developers"], cached_game_details["publishers"],
                                cached_game_details["metacritic"], cached_game_details["genres"], new_playtime_str, store_link
                            )
                            
                            try:
                                await bot.edit_message_caption(
                                    chat_id=CHANNEL_ID,
                                    message_id=last_message_id,
                                    caption=new_caption,
                                    parse_mode="HTML"
                                )
                                current_playtime_str = new_playtime_str
                                logging.info("✅ Сообщение успешно отредактировано")
                            except Exception as e:
                                logging.error(f"❌ Ошибка редактирования сообщения (возможно, оно было удалено вручную): {e}")
                                # Если редактирование не удалось (сообщение удалено), сбрасываем ID и отправим новое при следующей итерации
                                last_message_id = None
                else:
                    # 🛑 СЦЕНАРИЙ 3: Перестал играть
                    if last_game_id is not None:
                        logging.info(f"{EMOJIS['STOP']} Игра завершена.")
                        await delete_old_message()
                        await send_idle_message()
                        
                        # Сброс состояния
                        last_game_id = None
                        last_game_name = ""
                        current_playtime_str = ""
                        
            except Exception as e:
                logging.error(f"Ошибка в цикле мониторинга: {e}")
            
            await asyncio.sleep(CHECK_INTERVAL)

@dp.message()
async def echo_handler(message: Message):
    status = f"{EMOJIS['GAME']} Играю в <b>{last_game_name}</b>" if last_game_name else f"{EMOJIS['SLEEP']} Не играю"
    await message.answer(
        f"Бот работает! {EMOJIS['CHECK']}\n\nСтатус: {status}",
        parse_mode="HTML",
        link_preview_options=NO_PREVIEW
    )

async def on_startup(dispatcher: Dispatcher, bot: Bot):
    webhook_url = f"{WEBHOOK_BASE_URL.rstrip('/')}{WEBHOOK_PATH}"
    logging.info(f"🔗 Установка webhook на: {webhook_url}")
    try:
        await bot.set_webhook(webhook_url)
        logging.info(f"✅ Webhook успешно установлен")
    except Exception as e:
        logging.error(f"❌ ОШИБКА Webhook: {e}")
        raise
    asyncio.create_task(steam_monitor())

async def on_shutdown(dispatcher: Dispatcher, bot: Bot):
    await bot.delete_webhook()
    logging.info("✅ Webhook удален")

async def health_handler(request):
    status = f"{EMOJIS['GAME']} {last_game_name}" if last_game_name else f"{EMOJIS['SLEEP']} Idle"
    return web.Response(text=f"Bot is alive! Status: {status}")

async def main():
    if not WEBHOOK_BASE_URL:
        logging.error("❌ WEBHOOK_BASE_URL не установлен!")
        return
    
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.router.add_get('/health', health_handler)
    app.router.add_get('/', health_handler)
    
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    setup_application(app, dp, bot=bot)
    
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌐 Сервер запущен на порту {port}")
    
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())