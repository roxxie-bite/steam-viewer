import os
import asyncio
import logging
from aiohttp import ClientSession
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, LinkPreviewOptions, InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    ChosenInlineResult
)
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
    "SEPARATOR": "|",
    "SOLO": "🎮",
    "COOP": "🤝",
    "MULTIPLAYER": "👥",
    "LOBBY": "🏠",
    "MATCHMAKING": "🔍",
    "VERSUS": "⚔️",
    "ONLINE": "🌐",
    "INFO": "ℹ️"
}
# ==========================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные состояния
last_game_id = None
last_game_name = ""
last_game_extra = ""  # 🆕 Доп. инфо из Steam (режим, лобби и т.д.)
last_message_id = None
BOT_USERNAME = None 
current_playtime_str = ""

cached_game_details = {
    "developers": "Unknown",
    "publishers": "Unknown",
    "genres": "Unknown",
    "metacritic": None,
    "image_url": ""
}

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def parse_game_extra_info(extra: str) -> tuple[str, str]:
    """
    Определяет режим игры из gameextrainfo.
    Возвращает (emoji, описание).
    """
    if not extra:
        return "", ""
    
    text = extra.lower()
    
    # Одиночный режим
    if any(word in text for word in ("solo", "single", "singleplayer", "campaign", "story")):
        return EMOJIS["SOLO"], f"Одиночная игра ({extra})"
    
    # Кооператив
    if any(word in text for word in ("co-op", "coop", "cooperative", "collaboration")):
        return EMOJIS["COOP"], f"Кооператив ({extra})"
    
    # Мультиплеер / PvP
    if any(word in text for word in ("multi", "multiplayer", "pvp", "versus", "competitive")):
        return EMOJIS["MULTIPLAYER"], f"Мультиплеер ({extra})"
    
    # Лобби
    if "lobby" in text:
        return EMOJIS["LOBBY"], f"В лобби ({extra})"
    
    # Матчмейкинг / поиск
    if any(word in text for word in ("matchmaking", "searching", "queue", "finding")):
        return EMOJIS["MATCHMAKING"], f"Поиск матча ({extra})"
    
    # Онлайн / сервер
    if any(word in text for word in ("online", "server", "dedicated")):
        return EMOJIS["ONLINE"], f"Онлайн-режим ({extra})"
    
    # Всё остальное — выводим как есть
    return EMOJIS["INFO"], extra


# ==========================================
# 🆕 INLINE-РЕЖИМ: Обработчики
# ==========================================

@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    """Обработчик inline-запросов"""
    query = inline_query.query.strip().lower()
    
    logging.info(f"📥 INLINE QUERY получен!")
    logging.info(f"   От: {inline_query.from_user.username or inline_query.from_user.id}")
    logging.info(f"   Запрос: '{query}'")
    
    results = []
    
    # Команда: current - показать текущую игру
    if query == "" or query == "current":
        if last_game_name:
            extra_emoji, extra_desc = parse_game_extra_info(last_game_extra)
            extra_line = f"\n{extra_emoji} <b>Режим:</b> {extra_desc}\n" if extra_desc else "\n"
            
            results.append(
                InlineQueryResultArticle(
                    id="current_game",
                    title=f"🎮 Сейчас играю в: {last_game_name}",
                    description=f"⏱ Время: {current_playtime_str}" + (f" | {extra_desc[:30]}" if extra_desc else ""),
                    input_message_content=InputTextMessageContent(
                        message_text=f"{EMOJIS['GAME']} <b>Сейчас играю в:</b> {last_game_name}\n"
                                   f"{EMOJIS['TIME']} <b>Время:</b> {current_playtime_str}"
                                   f"{extra_line}"
                                   f"🔗 <a href='https://store.steampowered.com/app/{last_game_id}'>Страница в Steam</a>",
                        parse_mode="HTML"
                    ),
                    thumb_url=cached_game_details.get("image_url", "")
                )
            )
        else:
            results.append(
                InlineQueryResultArticle(
                    id="not_playing",
                    title="😴 Сейчас не играю",
                    description="Нажми, чтобы отправить статус",
                    input_message_content=InputTextMessageContent(
                        message_text=f"{EMOJIS['SLEEP']} <b>Сейчас не играю</b>\n\n"
                                   f"{EMOJIS['PLAYER']} <a href='https://steamcommunity.com/profiles/{STEAM_ID}'>Мой профиль в Steam</a>",
                        parse_mode="HTML"
                    )
                )
            )
    
    # Команда: stats
    if query == "stats":
        extra_emoji, extra_desc = parse_game_extra_info(last_game_extra)
        extra_line = f"\n{extra_emoji} <b>Режим:</b> {extra_desc}\n" if extra_desc else "\n"
        
        results.append(
            InlineQueryResultArticle(
                id="stats",
                title="📊 Моя статистика",
                description="Нажми, чтобы отправить статистику",
                input_message_content=InputTextMessageContent(
                    message_text=f"📊 <b>Статистика Steam</b>\n\n"
                               f"{EMOJIS['GAME']} Последняя игра: {last_game_name or 'Нет'}\n"
                               f"{EMOJIS['TIME']} Время: {current_playtime_str or '0 мин.'}"
                               f"{extra_line}"
                               f"🔗 <a href='https://steamcommunity.com/profiles/{STEAM_ID}'>Мой профиль</a>",
                    parse_mode="HTML"
                )
            )
        )
    
    # Команда: help
    if query == "help":
        results.append(
            InlineQueryResultArticle(
                id="help",
                title="❓ Помощь",
                description="Список команд",
                input_message_content=InputTextMessageContent(
                    message_text="<b>📖 Доступные команды:</b>\n\n"
                               "• <code>current</code> - текущая игра\n"
                               "• <code>stats</code> - статистика\n"
                               "• <code>help</code> - эта справка",
                    parse_mode="HTML"
                )
            )
        )
    
    # 🆕 ЕСЛИ СПИСОК ПУСТОЙ - показываем подсказку
    if not results:
        results.append(
            InlineQueryResultArticle(
                id="unknown_command",
                title="❓ Неизвестная команда",
                description="Доступные команды: current, stats, help",
                input_message_content=InputTextMessageContent(
                    message_text="<b>📖 Доступные команды:</b>\n\n"
                               "• <code>current</code> - текущая игра\n"
                               "• <code>stats</code> - статистика\n"
                               "• <code>help</code> - эта справка\n\n"
                               f"💡 <b>Inline-режим:</b> Напиши @{BOT_USERNAME} в любом чате!",
                    parse_mode="HTML"
                )
            )
        )
        logging.info(f"⚠️ Запрос '{query}' не распознан, показываю подсказку")
    
    logging.info(f"📤 Отправляю {len(results)} результатов")
    
    try:
        await inline_query.answer(results[:50], cache_time=1)
        logging.info("✅ Inline результаты успешно отправлены")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки inline: {e}")

@dp.chosen_inline_result()
async def chosen_inline_result_handler(chosen_result: ChosenInlineResult):
    """Inline feedback: Telegram сообщает, какой результат пользователь выбрал"""
    result_id = chosen_result.result_id
    query = chosen_result.query
    user = chosen_result.from_user
    
    logging.info(f"📊 Inline feedback: Пользователь {user.username or user.id} выбрал результат '{result_id}' по запросу '{query}'")

# ==========================================
# ОСНОВНАЯ ЛОГИКА
# ==========================================

def build_game_caption(game_name: str, devs: str, pubs: str, meta, genres: str, playtime: str, store_link: str, extra_info: str = "") -> str:
    extra_emoji, extra_desc = parse_game_extra_info(extra_info)
    
    parts = [
        f"{EMOJIS['GAME']} {EMOJIS['SEPARATOR']} Сейчас играю в: <b>{game_name}</b>",
    ]
    
    # 🆕 Доп. информация о режиме
    if extra_desc:
        parts.append(f"{extra_emoji} {EMOJIS['SEPARATOR']} <b>{extra_desc}</b>")
    
    parts.extend([
        f"{EMOJIS['DEVELOPER']} {EMOJIS['SEPARATOR']} Разработчики: {devs}",
        f"{EMOJIS['PUBLISHER']} {EMOJIS['SEPARATOR']} Издатели: {pubs}",
    ])
    
    if meta:
        parts.append(f"{EMOJIS['RATING']} {EMOJIS['SEPARATOR']} Оценка Metacritic: {meta}/100")
    
    parts.append(f"{EMOJIS['TAG']} {EMOJIS['SEPARATOR']} Жанры: {genres}")
    parts.append(f"{EMOJIS['TIME']} {EMOJIS['SEPARATOR']} Время в игре: {playtime}")
    parts.append("")
    
    return "\n".join(parts)

def build_inline_keyboard(game_id: str) -> InlineKeyboardMarkup:
    store_url = f"https://store.steampowered.com/app/{game_id}"
    profile_url = f"https://steamcommunity.com/profiles/{STEAM_ID}"
    
    keyboard = [
        [
            InlineKeyboardButton(text="🎮 Страница в Steam", url=store_url),
            InlineKeyboardButton(text="👤 Мой профиль", url=profile_url)
        ],
        [
            InlineKeyboardButton(text="🏆 Достижения", url=f"https://steamcommunity.com/profiles/{STEAM_ID}/stats/{game_id}/achievements"),
            InlineKeyboardButton(text="📊 Статистика", url=f"https://steamcommunity.com/profiles/{STEAM_ID}/stats/{game_id}")
        ]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def get_steam_status(session: ClientSession):
    url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={STEAM_ID}"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                players = data.get("response", {}).get("players", [])
                if players:
                    player = players[0]
                    return (
                        player.get("gameid"), 
                        player.get("gameextrainfo"),  # 🆕 Это название игры/режима
                        player.get("gameextrainfo")    # 🆕 Доп. инфо (часто дублируется, но может быть разным)
                    )
    except Exception as e:
        logging.error(f"Ошибка Steam API: {e}")
    return None, None, None

async def get_game_details(session: ClientSession, game_id: str, fallback_name: str = "Unknown") -> dict:
    url = f"https://store.steampowered.com/api/appdetails?appids={game_id}&cc=us"
    
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                app_data = data.get(str(game_id), {})
                
                if app_data.get("success"):
                    game_data = app_data["data"]
                    return {
                        "name": game_data.get("name", fallback_name),
                        "developers": ", ".join(game_data.get("developers", ["Скрыто разработчиком"])),
                        "publishers": ", ".join(game_data.get("publishers", ["Скрыто издателем"])),
                        "genres": ", ".join([genre["description"] for genre in game_data.get("genres", [])]) or "Информация ограничена",
                        "metacritic": game_data.get("metacritic", {}).get("score"),
                        "image_url": game_data.get("header_image", f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game_id}/header.jpg")
                    }
    except Exception as e:
        logging.error(f"Ошибка при получении деталей игры {game_id}: {e}")
    
    return {
        "name": fallback_name,
        "developers": "Скрыто разработчиком",
        "publishers": "Скрыто издателем",
        "genres": "Информация ограничена",
        "metacritic": None,
        "image_url": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game_id}/header.jpg"
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

async def send_game_update(game_id: str, game_name: str, extra_info: str, session: ClientSession):
    global last_message_id, cached_game_details, last_game_extra
    
    store_link = f"https://store.steampowered.com/app/{game_id}"
    
    details = await get_game_details(session, game_id, fallback_name=game_name)
    playtime_minutes = await get_player_game_time(session, game_id)
    playtime_str = format_playtime(playtime_minutes)
    
    cached_game_details = details
    last_game_extra = extra_info  # 🆕 Сохраняем доп. инфо
    
    caption = build_game_caption(
        details["name"], details["developers"], details["publishers"], 
        details["metacritic"], details["genres"], playtime_str, store_link,
        extra_info=extra_info  # 🆕 Передаём доп. инфо
    )
    
    keyboard = build_inline_keyboard(game_id)
    
    try:
        msg = await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=details["image_url"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        last_message_id = msg.message_id
        logging.info(f"✅ Отправлено новое сообщение: {details['name']} | Extra: {extra_info or 'None'}")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки фото: {e}")
        msg = await bot.send_message(
            chat_id=CHANNEL_ID, 
            text=caption, 
            parse_mode="HTML", 
            link_preview_options=NO_PREVIEW, 
            reply_markup=keyboard
        )
        last_message_id = msg.message_id
    
    return details["name"], playtime_str

async def steam_monitor():
    global last_game_id, last_game_name, last_game_extra, last_message_id, current_playtime_str, cached_game_details
    
    logging.info("🚀 Мониторинг Steam запущен...")
    
    async with ClientSession() as session:
        while True:
            try:
                game_id, game_name, extra_info = await get_steam_status(session)
                
                if game_id and game_name:
                    if game_id != last_game_id:
                        logging.info(f"🎯 Обнаружена новая игра: {game_name} (ID: {game_id}) | Extra: {extra_info or 'None'}")
                        await delete_old_message()
                        actual_game_name, playtime_str = await send_game_update(game_id, game_name, extra_info, session)
                        last_game_id = game_id
                        last_game_name = actual_game_name
                        current_playtime_str = playtime_str
                    else:
                        # 🆕 Если изменился режим игры (extra_info), обновляем сообщение
                        if extra_info != last_game_extra:
                            logging.info(f"🔄 Режим игры изменился: '{last_game_extra}' → '{extra_info}'")
                            last_game_extra = extra_info
                            store_link = f"https://store.steampowered.com/app/{game_id}"
                            new_caption = build_game_caption(
                                last_game_name, cached_game_details["developers"], cached_game_details["publishers"],
                                cached_game_details["metacritic"], cached_game_details["genres"], current_playtime_str, 
                                store_link, extra_info=extra_info
                            )
                            keyboard = build_inline_keyboard(game_id)
                            try:
                                await bot.edit_message_caption(
                                    chat_id=CHANNEL_ID,
                                    message_id=last_message_id,
                                    caption=new_caption,
                                    parse_mode="HTML",
                                    reply_markup=keyboard
                                )
                                logging.info("✅ Сообщение обновлено (новый режим)")
                            except Exception as e:
                                logging.error(f"❌ Ошибка редактирования сообщения: {e}")
                        
                        # Обновляем время как раньше
                        new_playtime_minutes = await get_player_game_time(session, game_id)
                        new_playtime_str = format_playtime(new_playtime_minutes)
                        
                        if new_playtime_str != current_playtime_str:
                            logging.info(f"✏️ Время в игре изменилось: {new_playtime_str}. Редактируем сообщение...")
                            store_link = f"https://store.steampowered.com/app/{game_id}"
                            new_caption = build_game_caption(
                                last_game_name, cached_game_details["developers"], cached_game_details["publishers"],
                                cached_game_details["metacritic"], cached_game_details["genres"], new_playtime_str, 
                                store_link, extra_info=last_game_extra
                            )
                            keyboard = build_inline_keyboard(game_id)
                            try:
                                await bot.edit_message_caption(
                                    chat_id=CHANNEL_ID,
                                    message_id=last_message_id,
                                    caption=new_caption,
                                    parse_mode="HTML",
                                    reply_markup=keyboard
                                )
                                current_playtime_str = new_playtime_str
                                logging.info("✅ Сообщение успешно отредактировано")
                            except Exception as e:
                                logging.error(f"❌ Ошибка редактирования сообщения: {e}")
                                last_message_id = None
                else:
                    if last_game_id is not None:
                        logging.info(f"{EMOJIS['STOP']} Игра завершена.")
                        await delete_old_message()
                        await send_idle_message()
                        last_game_id = None
                        last_game_name = ""
                        last_game_extra = ""
                        current_playtime_str = ""
            except Exception as e:
                logging.error(f"Ошибка в цикле мониторинга: {e}")
            
            await asyncio.sleep(CHECK_INTERVAL)

@dp.callback_query(F.data.startswith("steam_"))
async def handle_steam_callback(callback_query: CallbackQuery):
    await callback_query.answer("Открываю ссылку...", show_alert=False)

@dp.message()
async def echo_handler(message: Message):
    status = f"{EMOJIS['GAME']} Играю в <b>{last_game_name}</b>" if last_game_name else f"{EMOJIS['SLEEP']} Не играю"
    
    extra_emoji, extra_desc = parse_game_extra_info(last_game_extra)
    extra_line = f"\n{extra_emoji} <b>Режим:</b> {extra_desc}" if extra_desc else ""
    
    await message.answer(
        f"Бот работает! {EMOJIS['CHECK']}\n\n"
        f"Статус: {status}{extra_line}\n\n"
        f"💡 <b>Inline-режим:</b> Напиши @{BOT_USERNAME} в любом чате!",
        parse_mode="HTML",
        link_preview_options=NO_PREVIEW
    )

async def main():
    global BOT_USERNAME
    
    # Получаем username бота
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
        logging.info(f"✅ Бот запущен: @{BOT_USERNAME}")
    except Exception as e:
        logging.error(f"❌ Не удалось получить username бота: {e}")
        BOT_USERNAME = "Steambotik"
    
    # Запускаем мониторинг Steam в фоне
    asyncio.create_task(steam_monitor())
    
    # Запускаем polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())