import os
import asyncio
import logging
import time
import json
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
OWNER_ID = os.getenv("OWNER_ID")  # <-- Твой Telegram ID
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))


EMOJIS = {
    "GAME": "🎮", 
    "PLAYER": "👤",
    "DEVELOPER": "👨‍💻",
    "PUBLISHER": "🏢",
    "RATING": "⭐",
    "TAG": "🏷",
    "TIME": "⏱",
    "LINK": "📱",
    "CHECK": "✅",
    "SLEEP": "😴",
    "STOP": "🛑",
    "SEPARATOR": "|",
    "COOP": "👥",
}

# ID категорий Steam, указывающих на кооп/мультиплеер
COOP_CATEGORY_IDS = {1, 9, 24, 36, 37, 38, 39}

def has_coop_support(categories: list) -> bool:
    """Проверяет, поддерживает ли игра кооп/мультиплеер по категориям Steam"""
    if not categories:
        return False
    return any(cat.get("id") in COOP_CATEGORY_IDS for cat in categories)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

last_game_id = None
last_game_name = ""
last_message_id = None
BOT_USERNAME = None 
current_playtime_str = ""
last_coop_friends = []
pending_coop_friends = {}  # {f"{OWNER_ID}:{game_id}": {"friends": [...], "msg_id": int}}

cached_friends = []
last_friends_update = 0
FRIENDS_CACHE_TTL = 600

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
            text = (
                f"{EMOJIS['GAME']} <b>Сейчас играю в:</b> {last_game_name}"
                + "\n"
                + f"{EMOJIS['TIME']} <b>Время:</b> {current_playtime_str}"
                + "\n\n"
                + f"🔗 <a href='https://store.steampowered.com/app/{last_game_id}'>Страница в Steam</a>"
            )
            results.append(
                InlineQueryResultArticle(
                    id="current_game",
                    title=f"🎮 Сейчас играю в: {last_game_name}",
                    description=f"⏱ Время: {current_playtime_str}",
                    input_message_content=InputTextMessageContent(
                        message_text=text,
                        parse_mode="HTML"
                    ),
                    thumb_url=cached_game_details.get("image_url", "")
                )
            )
        else:
            text = (
                f"{EMOJIS['SLEEP']} <b>Сейчас не играю</b>"
                + "\n\n"
                + f"{EMOJIS['PLAYER']} <a href='https://steamcommunity.com/profiles/{STEAM_ID}'>Мой профиль в Steam</a>"
            )
            results.append(
                InlineQueryResultArticle(
                    id="not_playing",
                    title="😴 Сейчас не играю",
                    description="Нажми, чтобы отправить статус",
                    input_message_content=InputTextMessageContent(
                        message_text=text,
                        parse_mode="HTML"
                    )
                )
            )

    if query == "stats":
        text = (
            f"📊 <b>Статистика Steam</b>"
            + "\n\n"
            + f"{EMOJIS['GAME']} Последняя игра: {last_game_name or 'Нет'}"
            + "\n"
            + f"{EMOJIS['TIME']} Время: {current_playtime_str or '0 мин.'}"
            + "\n\n"
            + f"🔗 <a href='https://steamcommunity.com/profiles/{STEAM_ID}'>Мой профиль</a>"
        )
        results.append(
            InlineQueryResultArticle(
                id="stats",
                title="📊 Моя статистика",
                description="Нажми, чтобы отправить статистику",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode="HTML"
                )
            )
        )

    if query == "help":
        text = (
            "<b>📖 Доступные команды:</b>"
            + "\n\n"
            + "• <code>current</code> — текущая игра"
            + "\n"
            + "• <code>stats</code> — статистика"
            + "\n"
            + "• <code>help</code> — эта справка"
        )
        results.append(
            InlineQueryResultArticle(
                id="help",
                title="❓ Помощь",
                description="Список команд",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode="HTML"
                )
            )
        )

    if not results:
        text = (
            "<b>📖 Доступные команды:</b>"
            + "\n\n"
            + "• <code>current</code> — текущая игра"
            + "\n"
            + "• <code>stats</code> — статистика"
            + "\n"
            + "• <code>help</code> — эта справка"
            + "\n\n"
            + f"💡 <b>Inline-режим:</b> Напиши @{BOT_USERNAME} в любом чате!"
        )
        results.append(
            InlineQueryResultArticle(
                id="unknown_command",
                title="❓ Неизвестная команда",
                description="Доступные команды: current, stats, help",
                input_message_content=InputTextMessageContent(
                    message_text=text,
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

async def get_friend_list(session: ClientSession) -> list:
    global cached_friends, last_friends_update

    now = time.time()
    if cached_friends and (now - last_friends_update) < FRIENDS_CACHE_TTL:
        return cached_friends

    url = f"http://api.steampowered.com/ISteamUser/GetFriendList/v1/?key={STEAM_API_KEY}&steamid={STEAM_ID}&relationship=friend"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                friends = data.get("friendslist", {}).get("friends", [])
                cached_friends = [f["steamid"] for f in friends]
                last_friends_update = now
                logging.info(f"👥 Список друзей обновлён: {len(cached_friends)} друзей")
                return cached_friends
            elif response.status == 401:
                logging.warning("⚠️ Профиль Steam приватный — список друзей недоступен")
            else:
                logging.warning(f"⚠️ GetFriendList вернул {response.status}")
    except Exception as e:
        logging.error(f"❌ Ошибка получения списка друзей: {e}")

    return cached_friends


async def get_friends_playing_same_game(session: ClientSession, game_id: str) -> list:
    friends = await get_friend_list(session)
    if not friends:
        return []

    chunks = [friends[i:i+100] for i in range(0, len(friends), 100)]
    playing_friends = []

    for chunk in chunks:
        steamids = ",".join(chunk)
        url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={steamids}"
        try:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    players = data.get("response", {}).get("players", [])
                    for player in players:
                        if str(player.get("gameid")) == str(game_id):
                            name = player.get("personaname", "Неизвестный")
                            playing_friends.append(name)
        except Exception as e:
            logging.error(f"❌ Ошибка получения статусов друзей: {e}")

    if playing_friends:
        logging.info(f"👥 Найдено {len(playing_friends)} друзей в игре {game_id}")

    return playing_friends


def build_game_caption(game_name: str, devs: str, pubs: str, meta, genres: str, playtime: str, store_link: str, coop_friends: list = None) -> str:
    parts = [
        f"{EMOJIS['GAME']} {EMOJIS['SEPARATOR']} Сейчас играю в: <b>{game_name}</b>",
        f"{EMOJIS['DEVELOPER']} {EMOJIS['SEPARATOR']} Разработчики: {devs}",
        f"{EMOJIS['PUBLISHER']} {EMOJIS['SEPARATOR']} Издатели: {pubs}",
    ]
    if meta:
        parts.append(f"{EMOJIS['RATING']} {EMOJIS['SEPARATOR']} Оценка Metacritic: {meta}/100")

    parts.append(f"{EMOJIS['TAG']} {EMOJIS['SEPARATOR']} Жанры: {genres}")

    if coop_friends:
        friends_str = ", ".join(coop_friends)
        parts.append(f"{EMOJIS['COOP']} {EMOJIS['SEPARATOR']} Вместе с: <b>{friends_str}</b>")

    parts.append(f"{EMOJIS['TIME']} {EMOJIS['SEPARATOR']} Всего наиграно: {playtime}")
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
                    return player.get("gameid"), player.get("gameextrainfo")
    except Exception as e:
        logging.error(f"Ошибка Steam API: {e}")
    return None, None


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
                        "developers": ", ".join(game_data.get("developers", ["Данные не получены"])),
                        "publishers": ", ".join(game_data.get("publishers", ["Данные не получены"])),
                        "genres": ", ".join([genre["description"] for genre in game_data.get("genres", [])]) or "Данные не получены",
                        "metacritic": game_data.get("metacritic", {}).get("score"),
                        "image_url": game_data.get("header_image", f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game_id}/header.jpg"),
                        "categories": game_data.get("categories", [])
                    }
    except Exception as e:
        logging.error(f"Ошибка при получении деталей игры {game_id}: {e}")

    return {
        "name": fallback_name,
        "developers": "Данные не получены",
        "publishers": "Данные не получены",
        "genres": "Данные не получены",
        "metacritic": None,
        "image_url": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game_id}/header.jpg",
        "categories": []
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
        f"{EMOJIS['SLEEP']} {EMOJIS['SEPARATOR']} <b>Сейчас не играю</b>"
        + "\n\n"
        + f"{EMOJIS['PLAYER']} {EMOJIS['SEPARATOR']} <a href='https://steamcommunity.com/profiles/{STEAM_ID}'>Мой профиль в Steam</a>"
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


async def send_game_update(game_id: str, game_name: str, session: ClientSession):
    global last_message_id, cached_game_details, last_coop_friends

    store_link = f"https://store.steampowered.com/app/{game_id}"

    details = await get_game_details(session, game_id, fallback_name=game_name)
    playtime_minutes = await get_player_game_time(session, game_id)
    playtime_str = format_playtime(playtime_minutes)

    # По умолчанию НЕ ищем друзей — ждём ответа из ЛС
    coop_friends = []
    last_coop_friends = []

    cached_game_details = details

    caption = build_game_caption(
        details["name"], details["developers"], details["publishers"], 
        details["metacritic"], details["genres"], playtime_str, store_link,
        coop_friends=coop_friends
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
        logging.info(f"✅ Отправлено новое сообщение: {details['name']}")
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

    # Проверяем, есть ли друзья в игре и поддерживает ли игра кооп
    friends_in_game = await get_friends_playing_same_game(session, game_id)
    is_coop = has_coop_support(details.get("categories", []))

    if friends_in_game and is_coop:
        # Есть друзья и игра кооп — спрашиваем
        if OWNER_ID:
            try:
                await bot.send_message(
                    chat_id=OWNER_ID,
                    text=f"🎮 <b>Начал играть в:</b> {details['name']}\nИграешь с кем-то?",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(text="Да 👥", callback_data=f"coop_yes:{game_id}:{last_message_id}"),
                            InlineKeyboardButton(text="Нет 🚫", callback_data=f"coop_no:{game_id}:{last_message_id}")
                        ]
                    ])
                )
                logging.info(f"📨 Вопрос в ЛС отправлен (msg_id: {last_message_id}, друзей: {len(friends_in_game)})")
            except Exception as e:
                logging.error(f"❌ Не удалось отправить вопрос в ЛС: {e}")
    elif friends_in_game and not is_coop:
        logging.info(f"ℹ️ Друзей в игре {len(friends_in_game)}, но игра одиночная — вопрос не отправляем")
    else:
        logging.info(f"ℹ️ Друзей в игре нет — вопрос не отправляем")

    return details["name"], playtime_str



# МОНИТОРИНГ БЕСПЛАТНЫХ ИГР (FREE TO KEEP)
# ==========================================

FREE_GAMES_CHECK_INTERVAL = int(os.getenv("FREE_GAMES_CHECK_INTERVAL", 3600))
FREE_GAMES_CACHE_FILE = "free_games_cache.json"

# Кэш ID игр, о которых уже успешно уведомили.
# Важно: ID добавляется в кэш только ПОСЛЕ успешной отправки сообщения.
notified_free_games = set()


def load_free_games_cache():
    """Загружает кэш успешно отправленных уведомлений."""
    global notified_free_games
    try:
        if os.path.exists(FREE_GAMES_CACHE_FILE):
            with open(FREE_GAMES_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                notified_free_games = set(str(x) for x in data.get("notified", []))
                logging.info(
                    f"🎁 Кэш бесплатных игр загружен: {len(notified_free_games)} записей"
                )
    except Exception as e:
        logging.warning(f"⚠️ Не удалось загрузить кэш бесплатных игр: {e}")
        notified_free_games = set()


def save_free_games_cache():
    """Сохраняет кэш успешно отправленных уведомлений."""
    try:
        with open(FREE_GAMES_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"notified": sorted(notified_free_games)},
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения кэша бесплатных игр: {e}")


async def get_steam_free_games_search(session: ClientSession) -> list:
    """
    Получает список игр, которые Steam Search показывает как бесплатные со скидкой.
    Дополнительная проверка через Steam AppDetails ниже отсекает обычные F2P-игры.
    """
    url = "https://store.steampowered.com/search/results/"
    params = {
        "maxprice": "free",
        "specials": "1",
        "json": "1",
        "count": "50",
        "start": "0",
        "category1": "998",
        "hidef2p": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US,en;q=0.8",
    }

    games = []
    try:
        async with session.get(url, params=params, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                logging.warning(f"⚠️ Steam Search вернул статус {resp.status}")
                return games

            data = await resp.json(content_type=None)
            items = data.get("items", [])

            for item in items:
                logo_url = item.get("logo", "")
                match = re.search(r"/apps/(\d+)/", logo_url)
                if not match:
                    continue

                appid = match.group(1)
                games.append({
                    "id": appid,
                    "name": item.get("name", "Unknown"),
                })

            logging.info(
                f"🔍 Steam Search: найдено {len(games)} кандидатов на бесплатную раздачу"
            )

    except Exception as e:
        logging.error(f"❌ Ошибка Steam Search: {e}")

    return games


async def get_gamerpower_steam_games(session: ClientSession) -> list:
    """
    Получает активные игровые Steam-раздачи через GamerPower API.

    Для кнопки «Claim Game» используется open_giveaway_url.
    Это важно: gamerpower_url — страница самой записи GamerPower,
    а open_giveaway_url — ссылка открытия/получения раздачи.
    """
    url = "https://www.gamerpower.com/api/giveaways"
    params = {
        "platform": "steam",
        "type": "game",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    games = []

    try:
        async with session.get(
            url,
            params=params,
            headers=headers,
            timeout=15,
        ) as resp:
            if resp.status != 200:
                logging.warning(f"⚠️ GamerPower вернул статус {resp.status}")
                return games

            data = await resp.json(content_type=None)

            if not isinstance(data, list):
                logging.warning("⚠️ GamerPower вернул неожиданный формат данных")
                return games

            for item in data:
                gp_id = str(item.get("id", "")).strip()
                if not gp_id:
                    continue

                # Именно эта ссылка соответствует действию Claim Game.
                claim_url = str(item.get("open_giveaway_url", "")).strip()

                # Запасной вариант на случай, если API не отдаст open_giveaway_url.
                if not claim_url:
                    claim_url = str(item.get("gamerpower_url", "")).strip()

                if not claim_url:
                    logging.warning(
                        f"⚠️ GamerPower: у раздачи {gp_id} нет ссылки Claim Game"
                    )
                    continue

                title = str(item.get("title", "Unknown"))
                title = (
                    title.replace(" (Steam) Giveaway", "")
                    .replace(" (Steam)", "")
                    .strip()
                )

                # API GamerPower может возвращать статус active/expired.
                # На всякий случай не добавляем явно завершённые раздачи.
                status = str(item.get("status", "active")).lower().strip()
                if status and status not in {"active", "ongoing"}:
                    logging.info(
                        f"⏭️ GamerPower: пропускаю неактивную раздачу {title} (status={status})"
                    )
                    continue

                games.append({
                    "gp_id": gp_id,
                    "name": title,
                    "url": claim_url,
                    "gamerpower_url": str(item.get("gamerpower_url", "")).strip(),
                    "end_date": item.get("end_date", "N/A"),
                    "worth": item.get("worth", "???"),
                    "image": item.get("image", ""),
                    "source": "gamerpower",
                })

            logging.info(f"🔍 GamerPower: найдено {len(games)} активных Steam-раздач")

    except Exception as e:
        logging.error(f"❌ Ошибка GamerPower API: {e}")

    return games


async def is_real_freebie(session: ClientSession, appid: str) -> dict:
    """
    Проверяет через Steam AppDetails, является ли игра временно бесплатной,
    а не обычной free-to-play игрой.
    """
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=us"

    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status != 200:
                logging.warning(
                    f"⚠️ Steam AppDetails для {appid} вернул статус {resp.status}"
                )
                return None

            data = await resp.json(content_type=None)
            app_data = data.get(str(appid), {})

            if not app_data.get("success"):
                return None

            game_data = app_data.get("data", {})

            # Только полноценная игра, не DLC/саундтрек/ПО.
            if game_data.get("type") != "game":
                return None

            if not game_data.get("is_free"):
                return None

            price = game_data.get("price_overview")

            # Настоящий Free to Keep:
            # раньше игра стоила > 0, сейчас финальная цена = 0.
            if price and price.get("initial", 0) > 0 and price.get("final", 0) == 0:
                return {
                    "id": str(appid),
                    "name": game_data.get("name", "Unknown"),
                    "header_image": game_data.get("header_image", ""),
                    "store_url": f"https://store.steampowered.com/app/{appid}",
                    "original_price": price.get(
                        "initial_formatted",
                        price.get("final_formatted", "???"),
                    ),
                    "discount": price.get("discount_percent", 100),
                }

    except Exception as e:
        logging.error(f"❌ Ошибка проверки AppDetails для {appid}: {e}")

    return None


async def check_and_notify_free_games(session: ClientSession):
    """
    Проверяет бесплатные Steam-раздачи из двух источников:
    1. Steam Store — временный Free to Keep.
    2. GamerPower — активные Steam game giveaways.

    ID раздачи записывается в кэш только после успешной отправки уведомления.
    """
    global notified_free_games

    if not OWNER_ID:
        logging.warning(
            "⚠️ OWNER_ID не задан, уведомления о бесплатных играх отключены"
        )
        return

    new_notifications = []

    # ------------------------------------------
    # ИСТОЧНИК 1: прямые Free to Keep в Steam
    # ------------------------------------------
    steam_games = await get_steam_free_games_search(session)

    for game in steam_games:
        appid = str(game["id"])

        if appid in notified_free_games:
            continue

        details = await is_real_freebie(session, appid)
        if not details:
            continue

        new_notifications.append({
            "cache_id": appid,
            "type": "steam_direct",
            "name": details["name"],
            "url": details["store_url"],
            "image": details["header_image"],
            "extra": (
                f"💰 Обычная цена: {details['original_price']} | "
                f"📉 Скидка: {details['discount']}%"
            ),
        })

        logging.info(f"🆓 Найдена временная Steam-раздача: {details['name']}")

    # ------------------------------------------
    # ИСТОЧНИК 2: GamerPower
    # ------------------------------------------
    gp_games = await get_gamerpower_steam_games(session)

    for game in gp_games:
        cache_id = f"gp_{game['gp_id']}"

        if cache_id in notified_free_games:
            continue

        new_notifications.append({
            "cache_id": cache_id,
            "type": "gamerpower",
            "name": game["name"],
            # ВАЖНО: open_giveaway_url / Claim Game.
            "url": game["url"],
            "image": game["image"],
            "extra": (
                f"💰 Стоимость: {game['worth']} | "
                f"⏳ До: {game['end_date']}"
            ),
        })

        logging.info(
            f"🆓 Найдена GamerPower-раздача: {game['name']} → {game['url']}"
        )

    # ------------------------------------------
    # ОТПРАВКА УВЕДОМЛЕНИЙ
    # ------------------------------------------
    if not new_notifications:
        logging.info("ℹ️ Новых бесплатных раздач не найдено")
        return

    for game in new_notifications:
        # Экранируем текст для HTML Telegram.
        safe_name = (
            str(game["name"])
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

        text = (
            f"🎁 <b>Бесплатная игра!</b>\n\n"
            f"🎮 <b>{safe_name}</b>\n"
            f"{game['extra']}\n\n"
            f"🔗 <a href=\"{game['url']}\">Забрать игру — Claim Game</a>"
        )

        try:
            if game.get("image"):
                await bot.send_photo(
                    chat_id=OWNER_ID,
                    photo=game["image"],
                    caption=text,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=OWNER_ID,
                    text=text,
                    parse_mode="HTML",
                    link_preview_options=NO_PREVIEW,
                )

            # КРИТИЧНО: считаем раздачу отправленной только после успеха Telegram API.
            notified_free_games.add(game["cache_id"])
            save_free_games_cache()

            logging.info(
                f"📨 Уведомление успешно отправлено: {game['name']}"
            )
            await asyncio.sleep(0.5)

        except Exception as e:
            # ID НЕ попадает в кэш, поэтому следующая проверка попробует отправить снова.
            logging.error(
                f"❌ Ошибка отправки уведомления '{game['name']}': {e}"
            )


async def free_games_monitor():
    """Фоновая задача: мониторинг бесплатных игр."""
    load_free_games_cache()
    logging.info(
        f"🎁 Мониторинг бесплатных игр запущен "
        f"(интервал: {FREE_GAMES_CHECK_INTERVAL} сек.)"
    )

    async with ClientSession() as session:
        while True:
            try:
                await check_and_notify_free_games(session)
            except Exception as e:
                logging.error(
                    f"❌ Ошибка в цикле мониторинга бесплатных игр: {e}"
                )

            await asyncio.sleep(FREE_GAMES_CHECK_INTERVAL)


# ==========================================
# КОМАНДА /free — ручная проверка
# ==========================================

@dp.message(F.text.in_({"/free", "/бесплатно", "!free"}))
async def free_games_command(message: Message):
    """Ручная проверка бесплатных игр (только для владельца)."""
    if not OWNER_ID or str(message.from_user.id) != OWNER_ID:
        await message.answer("⛔ Эта команда только для владельца бота.")
        return

    await message.answer("🔍 Проверяю бесплатные игры, подожди...")

    try:
        async with ClientSession() as session:
            await check_and_notify_free_games(session)
        await message.answer(
            "✅ Проверка завершена! Если найдены новые раздачи — "
            "я уже отправил их в ЛС."
        )
    except Exception as e:
        logging.error(f"❌ Ошибка ручной проверки /free: {e}")
        await message.answer("❌ При проверке произошла ошибка. Подробности в логах.")


async def steam_monitor():
    global last_game_id, last_game_name, last_message_id, current_playtime_str, cached_game_details, last_coop_friends

    logging.info("🚀 Мониторинг Steam запущен...")

    async with ClientSession() as session:
        while True:
            try:
                game_id, game_name = await get_steam_status(session)

                if game_id and game_name:
                    if game_id != last_game_id:
                        logging.info(f"🎯 Обнаружена новая игра: {game_name} (ID: {game_id})")
                        await delete_old_message()
                        actual_game_name, playtime_str = await send_game_update(game_id, game_name, session)
                        last_game_id = game_id
                        last_game_name = actual_game_name
                        current_playtime_str = playtime_str
                    else:
                        new_playtime_minutes = await get_player_game_time(session, game_id)
                        new_playtime_str = format_playtime(new_playtime_minutes)

                        # Убрано авто-обновление друзей — только время
                        time_changed = new_playtime_str != current_playtime_str

                        if time_changed:
                            logging.info(f"✏️ Обновление времени: {new_playtime_str}")
                            store_link = f"https://store.steampowered.com/app/{game_id}"
                            new_caption = build_game_caption(
                                last_game_name, cached_game_details["developers"], cached_game_details["publishers"],
                                cached_game_details["metacritic"], cached_game_details["genres"], new_playtime_str, store_link,
                                coop_friends=last_coop_friends  # оставляем тех, что были добавлены через ЛС
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
                        current_playtime_str = ""
                        last_coop_friends = []
            except Exception as e:
                logging.error(f"Ошибка в цикле мониторинга: {e}")

            await asyncio.sleep(CHECK_INTERVAL)


async def _update_post_with_friends(game_id: str, msg_id: int, friends: list):
    """Редактирует пост в канале, добавляя строку с друзьями"""
    store_link = f"https://store.steampowered.com/app/{game_id}"
    new_caption = build_game_caption(
        last_game_name or cached_game_details.get("name", "Игра"),
        cached_game_details["developers"],
        cached_game_details["publishers"],
        cached_game_details["metacritic"],
        cached_game_details["genres"],
        current_playtime_str,
        store_link,
        coop_friends=friends
    )
    keyboard = build_inline_keyboard(game_id)
    try:
        await bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=msg_id,
            caption=new_caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        logging.info(f"👥 Добавлены друзья в пост: {friends}")
    except Exception as e:
        logging.error(f"❌ Ошибка добавления друзей: {e}")
        raise


@dp.callback_query(F.data.startswith("coop_"))
async def handle_coop_callback(callback_query: CallbackQuery):
    """Обработка ответа из ЛС: играем ли с кем-то"""
    if not OWNER_ID or str(callback_query.from_user.id) != OWNER_ID:
        await callback_query.answer("Не для тебя кнопка 😏", show_alert=True)
        return

    data = callback_query.data.split(":")
    if len(data) < 3:
        await callback_query.answer("Ошибка данных", show_alert=True)
        return

    action = data[0]
    game_id = data[1]
    msg_id = int(data[2])

    if action == "coop_yes":
        async with ClientSession() as session:
            friends = await get_friends_playing_same_game(session, game_id)
            if not friends:
                await callback_query.answer("Друзей в игре не найдено 🤷")
                return

            if len(friends) == 1:
                # Один друг — сразу добавляем
                global last_coop_friends
                last_coop_friends = friends
                await _update_post_with_friends(game_id, msg_id, friends)
                await callback_query.answer(f"👥 Добавлен: {friends[0]}")
                return

            # Несколько друзей — предлагаем выбор
            key = f"{OWNER_ID}:{game_id}"
            pending_coop_friends[key] = {"friends": friends, "msg_id": msg_id}

            buttons = []
            for idx, name in enumerate(friends):
                display_name = name if len(name) <= 20 else name[:17] + "..."
                buttons.append([InlineKeyboardButton(
                    text=display_name,
                    callback_data=f"coop_pick:{game_id}:{msg_id}:{idx}"
                )])

            buttons.append([
                InlineKeyboardButton(text="👥 Все", callback_data=f"coop_all:{game_id}:{msg_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"coop_cancel:{game_id}:{msg_id}")
            ])

            await bot.send_message(
                chat_id=OWNER_ID,
                text=f"🎮 <b>{last_game_name or cached_game_details.get('name', 'Игра')}</b>\nВыбери, с кем играешь:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )
            await callback_query.answer("Выбери друзей 👇")

    elif action == "coop_no":
        await callback_query.answer("Ок, строка с друзьями не будет добавлена ✅")

    elif action == "coop_pick":
        if len(data) != 4:
            await callback_query.answer("Ошибка данных", show_alert=True)
            return
        idx = int(data[3])
        key = f"{OWNER_ID}:{game_id}"
        stored = pending_coop_friends.pop(key, None)
        if not stored or idx >= len(stored["friends"]):
            await callback_query.answer("Данные устарели, начни заново", show_alert=True)
            return
        selected = [stored["friends"][idx]]
        last_coop_friends = selected
        await _update_post_with_friends(game_id, msg_id, selected)
        await callback_query.answer(f"👥 Добавлен: {selected[0]}")

    elif action == "coop_all":
        key = f"{OWNER_ID}:{game_id}"
        stored = pending_coop_friends.pop(key, None)
        if not stored:
            async with ClientSession() as session:
                friends = await get_friends_playing_same_game(session, game_id)
                if not friends:
                    await callback_query.answer("Данные устарели, друзей не найдено", show_alert=True)
                    return
                stored = {"friends": friends}
        last_coop_friends = stored["friends"]
        await _update_post_with_friends(game_id, msg_id, stored["friends"])
        await callback_query.answer(f"👥 Добавлены все: {len(stored['friends'])} друзей")

    elif action == "coop_cancel":
        key = f"{OWNER_ID}:{game_id}"
        pending_coop_friends.pop(key, None)
        await callback_query.answer("Отменено ✅")


@dp.callback_query(F.data.startswith("steam_"))
async def handle_steam_callback(callback_query: CallbackQuery):
    await callback_query.answer("Открываю ссылку...", show_alert=False)


@dp.message()
async def echo_handler(message: Message):
    status = f"{EMOJIS['GAME']} Играю в <b>{last_game_name}</b>" if last_game_name else f"{EMOJIS['SLEEP']} Не играю"
    await message.answer(
        f"Бот работает! {EMOJIS['CHECK']}"
        + "\n\nСтатус: "
        + status
        + "\n\n"
        + f"💡 <b>Inline-режим:</b> Напиши @{BOT_USERNAME} в любом чате!",
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

    # Загружаем кэш бесплатных игр
    load_free_games_cache()

    # Запускаем все фоновые задачи
    asyncio.create_task(steam_monitor())
    asyncio.create_task(free_games_monitor())  # <-- НОВАЯ СТРОКА

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())