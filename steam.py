import os
import asyncio
import logging
import time
import re
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

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 15))
STATUS_UPDATE_INTERVAL = int(os.getenv("STATUS_UPDATE_INTERVAL", 30))
EXTRA_INFO_INTERVAL = int(os.getenv("EXTRA_INFO_INTERVAL", 10))

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
    "INFO": "📋",
    "REFRESH": "🔄",
    "CLOCK": "⏰",
    "MAP": "🗺",
    "CLASS": "⚔️",
    "DIFFICULTY": "💀"
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

last_game_id = None
last_game_name = ""
last_game_extra = ""      # gameextrainfo из API
last_rich_presence = ""   # 🆕 Rich Presence из HTML профиля
last_message_id = None
BOT_USERNAME = None 
current_playtime_str = ""

last_status_update_time = 0
last_extra_update_time = 0
last_playtime_update_time = 0

cached_game_details = {
    "developers": "Unknown",
    "publishers": "Unknown",
    "genres": "Unknown",
    "metacritic": None,
    "image_url": ""
}

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


# ==========================================
# INLINE-РЕЖИМ
# ==========================================

@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    query = inline_query.query.strip().lower()
    results = []
    
    if query == "" or query == "current":
        if last_game_name:
            rp_line = ""
            display_rp = last_rich_presence or (last_game_extra if last_game_extra != last_game_name else "")
            if display_rp:
                rp_line = f"\n{EMOJIS['INFO']} <b>{display_rp}</b>\n"
            
            results.append(
                InlineQueryResultArticle(
                    id="current_game",
                    title=f"🎮 Сейчас играю в: {last_game_name}",
                    description=f"⏱ {current_playtime_str}" + (f" | {display_rp[:40]}" if display_rp else ""),
                    input_message_content=InputTextMessageContent(
                        message_text=f"{EMOJIS['GAME']} <b>Сейчас играю в:</b> {last_game_name}\n"
                                   f"{EMOJIS['TIME']} <b>Время:</b> {current_playtime_str}"
                                   f"{rp_line}"
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
    
    if query == "stats":
        display_rp = last_rich_presence or (last_game_extra if last_game_extra != last_game_name else "")
        rp_line = ""
        if display_rp:
            rp_line = f"\n{EMOJIS['INFO']} <b>{display_rp}</b>\n"
        
        results.append(
            InlineQueryResultArticle(
                id="stats",
                title="📊 Моя статистика",
                description="Нажми, чтобы отправить статистику",
                input_message_content=InputTextMessageContent(
                    message_text=f"📊 <b>Статистика Steam</b>\n\n"
                               f"{EMOJIS['GAME']} Последняя игра: {last_game_name or 'Нет'}\n"
                               f"{EMOJIS['TIME']} Время: {current_playtime_str or '0 мин.'}"
                               f"{rp_line}"
                               f"🔗 <a href='https://steamcommunity.com/profiles/{STEAM_ID}'>Мой профиль</a>",
                    parse_mode="HTML"
                )
            )
        )
    
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
                               "• <code>help</code> - эта справка\n\n"
                               f"⏱ Проверка каждые <b>{CHECK_INTERVAL}</b> сек\n"
                               f"🔄 Обновление статуса каждые <b>{EXTRA_INFO_INTERVAL}</b> сек",
                    parse_mode="HTML"
                )
            )
        )
    
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
    
    try:
        await inline_query.answer(results[:50], cache_time=1)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки inline: {e}")

@dp.chosen_inline_result()
async def chosen_inline_result_handler(chosen_result: ChosenInlineResult):
    pass

# ==========================================
# ОСНОВНАЯ ЛОГИКА
# ==========================================

def build_game_caption(game_name: str, devs: str, pubs: str, meta, genres: str, playtime: str, store_link: str, rich_presence: str = "") -> str:
    parts = [
        f"{EMOJIS['GAME']} {EMOJIS['SEPARATOR']} Сейчас играю в: <b>{game_name}</b>",
    ]
    
    # 🆕 Rich Presence — выводим как есть, если есть
    if rich_presence and rich_presence.strip():
        parts.append(f"{EMOJIS['INFO']} {EMOJIS['SEPARATOR']} <b>{rich_presence.strip()}</b>")
    
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
    """Получаем gameid и gameextrainfo из Steam Web API."""
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
                        player.get("gameextrainfo", ""),
                    )
    except Exception as e:
        logging.error(f"Ошибка Steam API: {e}")
    return None, ""

async def get_rich_presence_from_profile(session: ClientSession) -> str:
    """
    🆕 Парсим HTML профиля Steam для получения rich presence.
    Ищем блок с классом .rich_presence или текст под названием игры.
    """
    profile_url = f"https://steamcommunity.com/profiles/{STEAM_ID}"
    try:
        async with session.get(profile_url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
        }) as response:
            if response.status == 200:
                html = await response.text()
                
                # 🆕 Паттерн 1: Ищем rich presence в профиле
                # Структура: <div class="profile_in_game_name">Игра</div>
                #           <div class="profile_in_game_additional">Rich Presence</div>
                additional_pattern = r'<div[^>]*class="[^"]*profile_in_game_additional[^"]*"[^>]*>(.*?)</div>'
                match = re.search(additional_pattern, html, re.DOTALL | re.IGNORECASE)
                if match:
                    rp_text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
                    if rp_text and rp_text != "In-Game":
                        return rp_text
                
                # 🆕 Паттерн 2: Ищем в блоке rich_presence
                rp_pattern = r'<span[^>]*class="[^"]*rich_presence[^"]*"[^>]*>(.*?)</span>'
                match = re.search(rp_pattern, html, re.DOTALL | re.IGNORECASE)
                if match:
                    rp_text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
                    if rp_text:
                        return rp_text
                
                # 🆕 Паттерн 3: Ищем в минипрофиле (для публичных профилей)
                mini_pattern = r'<div class="miniprofile_gamesection".*?<div class="[^"]*game_name[^"]*">.*?</div>\s*<div[^>]*>(.*?)</div>'
                match = re.search(mini_pattern, html, re.DOTALL | re.IGNORECASE)
                if match:
                    rp_text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
                    if rp_text:
                        return rp_text
                
                # 🆕 Паттерн 4: Ищем в блоке игры на странице профиля
                # <div class="profile_in_game">...<div class="profile_in_game_name">Game</div>...<div>Rich Presence</div>
                game_block = r'<div[^>]*class="[^"]*profile_in_game[^"]*"[^>]*>.*?<div[^>]*class="[^"]*profile_in_game_name[^"]*"[^>]*>.*?</div>(.*?)</div>'
                match = re.search(game_block, html, re.DOTALL | re.IGNORECASE)
                if match:
                    inner = match.group(1)
                    # Ищем текст после названия игры
                    text_match = re.search(r'<div[^>]*>([^<]+)</div>', inner)
                    if text_match:
                        rp_text = text_match.group(1).strip()
                        if rp_text and rp_text not in ("In-Game", "В игре", "Online", "В сети"):
                            return rp_text
                
    except Exception as e:
        logging.error(f"Ошибка парсинга rich presence: {e}")
    
    return ""

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

async def send_game_update(game_id: str, game_name: str, extra_info: str, rich_presence: str, session: ClientSession):
    global last_message_id, cached_game_details, last_game_extra, last_rich_presence
    
    store_link = f"https://store.steampowered.com/app/{game_id}"
    
    details = await get_game_details(session, game_id, fallback_name=game_name)
    playtime_minutes = await get_player_game_time(session, game_id)
    playtime_str = format_playtime(playtime_minutes)
    
    cached_game_details = details
    last_game_extra = extra_info or ""
    last_rich_presence = rich_presence or ""
    
    # Определяем что показывать: rich presence приоритетнее
    display_rp = rich_presence or (extra_info if extra_info != details["name"] else "")
    
    caption = build_game_caption(
        details["name"], details["developers"], details["publishers"], 
        details["metacritic"], details["genres"], playtime_str, store_link,
        rich_presence=display_rp
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
        logging.info(f"✅ Отправлено: {details['name']} | RP: '{display_rp or 'None'}'")
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

async def update_message_caption(game_id: str, game_name: str, extra_info: str, rich_presence: str, playtime_str: str, reason: str):
    global last_message_id, last_game_extra, last_rich_presence, current_playtime_str
    
    store_link = f"https://store.steampowered.com/app/{game_id}"
    
    # Определяем отображаемый rich presence
    display_rp = rich_presence or (extra_info if extra_info != game_name else "")
    
    new_caption = build_game_caption(
        game_name, 
        cached_game_details["developers"], 
        cached_game_details["publishers"],
        cached_game_details["metacritic"], 
        cached_game_details["genres"], 
        playtime_str, 
        store_link, 
        rich_presence=display_rp
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
        last_game_extra = extra_info or ""
        last_rich_presence = rich_presence or ""
        current_playtime_str = playtime_str
        logging.info(f"🔄 Обновлено ({reason}): RP='{display_rp or 'None'}' | playtime='{playtime_str}'")
        return True
    except Exception as e:
        error_str = str(e).lower()
        if "message is not modified" in error_str:
            logging.debug(f"⏸️ Без изменений ({reason})")
            return True
        logging.error(f"❌ Ошибка обновления ({reason}): {e}")
        return False

async def steam_monitor():
    global last_game_id, last_game_name, last_game_extra, last_rich_presence, last_message_id
    global last_status_update_time, last_extra_update_time, last_playtime_update_time
    global current_playtime_str, cached_game_details
    
    logging.info(f"🚀 Мониторинг Steam запущен...")
    logging.info(f"   ⏱ Проверка Steam API: каждые {CHECK_INTERVAL} сек")
    logging.info(f"   🔄 Обновление статуса: каждые {EXTRA_INFO_INTERVAL} сек")
    
    async with ClientSession() as session:
        while True:
            now = time.time()
            
            try:
                # 🆕 Получаем базовый статус из API
                game_id, game_extra = await get_steam_status(session)
                
                # 🆕 Пытаемся получить rich presence из HTML профиля
                rich_presence = ""
                if game_id:
                    rich_presence = await get_rich_presence_from_profile(session)
                    if rich_presence:
                        logging.info(f"📝 Rich Presence из профиля: '{rich_presence}'")
                
                if game_id:
                    details = await get_game_details(session, game_id)
                    game_name = details["name"]
                    
                    # Определяем финальный rich presence
                    final_rp = rich_presence
                    if not final_rp and game_extra and game_extra != game_name:
                        final_rp = game_extra
                    
                    if game_id != last_game_id:
                        logging.info(f"🎯 Новая игра: {game_name} (ID: {game_id}) | RP: '{final_rp or 'None'}'")
                        await delete_old_message()
                        actual_game_name, playtime_str = await send_game_update(
                            game_id, game_name, game_extra, rich_presence, session
                        )
                        last_game_id = game_id
                        last_game_name = actual_game_name
                        current_playtime_str = playtime_str
                        last_status_update_time = now
                        last_extra_update_time = now
                        last_playtime_update_time = now
                    
                    else:
                        needs_update = False
                        update_reasons = []
                        
                        # Проверяем rich presence
                        current_display_rp = last_rich_presence or (last_game_extra if last_game_extra != last_game_name else "")
                        new_display_rp = rich_presence or (game_extra if game_extra != game_name else "")
                        
                        if new_display_rp != current_display_rp and (now - last_extra_update_time) >= EXTRA_INFO_INTERVAL:
                            needs_update = True
                            update_reasons.append(f"RP '{current_display_rp or 'None'}'→'{new_display_rp or 'None'}'")
                            last_extra_update_time = now
                        
                        # Проверяем время
                        new_playtime_minutes = await get_player_game_time(session, game_id)
                        new_playtime_str = format_playtime(new_playtime_minutes)
                        
                        if new_playtime_str != current_playtime_str and (now - last_playtime_update_time) >= STATUS_UPDATE_INTERVAL:
                            needs_update = True
                            update_reasons.append(f"playtime '{current_playtime_str}'→'{new_playtime_str}'")
                            last_playtime_update_time = now
                        
                        # Принудительное обновление
                        if (now - last_status_update_time) >= STATUS_UPDATE_INTERVAL:
                            needs_update = True
                            update_reasons.append("forced")
                            last_status_update_time = now
                        
                        if needs_update and update_reasons:
                            logging.info(f"🔄 Обновление: {', '.join(update_reasons)}")
                            await update_message_caption(
                                game_id, last_game_name, game_extra, rich_presence, new_playtime_str,
                                reason=" | ".join(update_reasons)
                            )
                
                else:
                    if last_game_id is not None:
                        logging.info(f"{EMOJIS['STOP']} Игра завершена.")
                        await delete_old_message()
                        await send_idle_message()
                        last_game_id = None
                        last_game_name = ""
                        last_game_extra = ""
                        last_rich_presence = ""
                        current_playtime_str = ""
                        last_status_update_time = 0
                        last_extra_update_time = 0
                        last_playtime_update_time = 0
            
            except Exception as e:
                logging.error(f"Ошибка в цикле мониторинга: {e}")
            
            await asyncio.sleep(CHECK_INTERVAL)

@dp.callback_query(F.data.startswith("steam_"))
async def handle_steam_callback(callback_query: CallbackQuery):
    await callback_query.answer("Открываю ссылку...", show_alert=False)

@dp.message()
async def echo_handler(message: Message):
    status = f"{EMOJIS['GAME']} Играю в <b>{last_game_name}</b>" if last_game_name else f"{EMOJIS['SLEEP']} Не играю"
    
    display_rp = last_rich_presence or (last_game_extra if last_game_extra != last_game_name else "")
    rp_line = ""
    if display_rp:
        rp_line = f"\n{EMOJIS['INFO']} <b>{display_rp}</b>"
    
    await message.answer(
        f"Бот работает! {EMOJIS['CHECK']}\n\n"
        f"Статус: {status}{rp_line}\n\n"
        f"⏱ Проверка: <b>{CHECK_INTERVAL}</b> сек\n"
        f"🔄 Обновление статуса: <b>{EXTRA_INFO_INTERVAL}</b> сек\n\n"
        f"💡 <b>Inline-режим:</b> Напиши @{BOT_USERNAME} в любом чате!",
        parse_mode="HTML",
        link_preview_options=NO_PREVIEW
    )

async def main():
    global BOT_USERNAME
    
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
        logging.info(f"✅ Бот запущен: @{BOT_USERNAME}")
    except Exception as e:
        logging.error(f"❌ Не удалось получить username бота: {e}")
        BOT_USERNAME = "Steambotik"
    
    asyncio.create_task(steam_monitor())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())