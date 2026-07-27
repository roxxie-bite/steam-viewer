import os
import asyncio
import logging
import time
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
OWNER_ID = os.getenv("OWNER_ID")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))

# === YANDEX MUSIC CONFIG ===
YANDEX_MUSIC_TOKEN = os.getenv("YANDEX_MUSIC_TOKEN")
YANDEX_API_URL = os.getenv("YANDEX_API_URL", "https://track.mipoh.ru")
MUSIC_CHANNEL_ID = os.getenv("MUSIC_CHANNEL_ID", CHANNEL_ID)  # можно задать отдельный канал для музыки
# =============================


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
    "MUSIC": "🎵",
    "ARTIST": "🎤",
    "ALBUM": "💿",
    "YANDEX": "🎧",
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

# Steam globals
last_game_id = None
last_game_name = ""
last_message_id = None
BOT_USERNAME = None 
current_playtime_str = ""
last_coop_friends = []
pending_coop_friends = {}

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

# Yandex Music globals
last_track_id = None
last_track_name = ""
last_music_message_id = None
current_track_info = {
    "title": "",
    "artists": [],
    "album": "",
    "thumb": "",
    "duration": 0,
    "progress": 0,
    "link": "",
}

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


# ==========================================
# YANDEX MUSIC FUNCTIONS
# ==========================================

async def get_yandex_track(session: ClientSession) -> dict | None:
    """Получает текущий трек из Яндекс.Музыки (адаптировано из reSwaga)"""
    if not YANDEX_MUSIC_TOKEN:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "ya-token": YANDEX_MUSIC_TOKEN,
    }

    try:
        async with session.get(
            f"{YANDEX_API_URL}/get_current_track_beta",
            headers=headers,
            timeout=10,
            ssl=False
        ) as response:
            if response.status != 200:
                logging.warning(f"⚠️ Yandex API вернул {response.status}")
                return None

            data = await response.json()
            if "track" not in data:
                return None

            t = data["track"]
            raw_artist = t.get("artist", "")
            if isinstance(raw_artist, str):
                artists = [x.strip() for x in raw_artist.split(",") if x.strip()]
            elif isinstance(raw_artist, list):
                artists = raw_artist
            else:
                artists = []

            return {
                "id": t.get("track_id"),
                "title": t.get("title", "Unknown"),
                "artists": artists,
                "album": t.get("album", ""),
                "thumb": t.get("img", ""),
                "duration": int(t.get("duration", 0)),
                "progress": int(data.get("progress_ms", 0)) // 1000,
                "link": f"https://music.yandex.ru/track/{t.get('track_id')}",
                "download_url": t.get("download_link"),
            }
    except Exception as e:
        logging.error(f"❌ Ошибка Yandex Music API: {e}")
        return None


def format_track_caption(track: dict) -> str:
    """Форматирует подпись для трека"""
    artists_str = ", ".join(track["artists"]) if track["artists"] else "Неизвестный артист"
    duration_str = ""
    if track["duration"]:
        mins = track["duration"] // 60
        secs = track["duration"] % 60
        duration_str = f"{mins}:{secs:02d}"

    parts = [
        f"{EMOJIS['YANDEX']} {EMOJIS['SEPARATOR']} <b>Сейчас слушаю:</b> {track['title']}",
        f"{EMOJIS['ARTIST']} {EMOJIS['SEPARATOR']} {artists_str}",
    ]
    if track.get("album"):
        parts.append(f"{EMOJIS['ALBUM']} {EMOJIS['SEPARATOR']} {track['album']}")
    if duration_str:
        parts.append(f"{EMOJIS['TIME']} {EMOJIS['SEPARATOR']} Длительность: {duration_str}")
    parts.append("")
    parts.append(f"🔗 <a href='{track['link']}'>Открыть в Яндекс.Музыке</a>")

    return "\n".join(parts)


def build_music_keyboard(track: dict) -> InlineKeyboardMarkup:
    """Клавиатура для трека"""
    keyboard = [
        [
            InlineKeyboardButton(text="🎧 Яндекс.Музыка", url=track["link"]),
        ]
    ]
    if track.get("download_url"):
        keyboard[0].append(
            InlineKeyboardButton(text="⬇️ Скачать", url=track["download_url"])
        )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def delete_old_music_message():
    global last_music_message_id
    if last_music_message_id:
        try:
            await bot.delete_message(chat_id=MUSIC_CHANNEL_ID, message_id=last_music_message_id)
            logging.info(f"🗑️ Старое музыкальное сообщение {last_music_message_id} удалено")
        except Exception as e:
            logging.warning(f"Не удалось удалить муз. сообщение {last_music_message_id}: {e}")
        finally:
            last_music_message_id = None


async def send_idle_music_message():
    global last_music_message_id
    message = (
        f"{EMOJIS['SLEEP']} {EMOJIS['SEPARATOR']} <b>Музыка не играет</b>"
        + "\n\n"
        + f"{EMOJIS['YANDEX']} <a href='https://music.yandex.ru'>Яндекс.Музыка</a>"
    )
    try:
        msg = await bot.send_message(
            chat_id=MUSIC_CHANNEL_ID,
            text=message,
            parse_mode="HTML",
            link_preview_options=NO_PREVIEW
        )
        last_music_message_id = msg.message_id
        logging.info("✅ Отправлено сообщение о простое (музыка)")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки idle music: {e}")


async def send_music_update(track: dict):
    global last_music_message_id, current_track_info, last_track_id, last_track_name

    current_track_info = track
    caption = format_track_caption(track)
    keyboard = build_music_keyboard(track)

    try:
        if track.get("thumb"):
            msg = await bot.send_photo(
                chat_id=MUSIC_CHANNEL_ID,
                photo=track["thumb"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            msg = await bot.send_message(
                chat_id=MUSIC_CHANNEL_ID,
                text=caption,
                parse_mode="HTML",
                link_preview_options=NO_PREVIEW,
                reply_markup=keyboard
            )
        last_music_message_id = msg.message_id
        last_track_id = track["id"]
        last_track_name = track["title"]
        logging.info(f"✅ Отправлен трек: {track['title']}")
    except Exception as e:
        logging.error(f"❌ Ошибка отправки трека: {e}")


async def yandex_monitor():
    """Фоновый мониторинг Яндекс.Музыки (по аналогии с Steam)"""
    global last_track_id, last_track_name, last_music_message_id

    if not YANDEX_MUSIC_TOKEN:
        logging.info("ℹ️ YANDEX_MUSIC_TOKEN не задан, мониторинг музыки отключён")
        return

    logging.info("🚀 Мониторинг Яндекс.Музыки запущен...")

    async with ClientSession() as session:
        while True:
            try:
                track = await get_yandex_track(session)

                if track and track.get("id"):
                    if track["id"] != last_track_id:
                        logging.info(f"🎵 Новый трек: {track['title']}")
                        await delete_old_music_message()
                        await send_music_update(track)
                    else:
                        # Можно добавить обновление прогресса, если нужно
                        pass
                else:
                    if last_track_id is not None:
                        logging.info(f"{EMOJIS['STOP']} Музыка остановлена.")
                        await delete_old_music_message()
                        await send_idle_music_message()
                        last_track_id = None
                        last_track_name = ""

            except Exception as e:
                logging.error(f"Ошибка в цикле мониторинга музыки: {e}")

            await asyncio.sleep(CHECK_INTERVAL)


# ==========================================
# INLINE-РЕЖИМ (обновлённый)
# ==========================================

@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    query = inline_query.query.strip().lower()
    results = []

    # --- YANDEX MUSIC ---
    if query in ["music", "ym", "yandex", "track"]:
        if current_track_info and current_track_info.get("id"):
            track = current_track_info
            artists_str = ", ".join(track["artists"]) if track["artists"] else ""
            text = format_track_caption(track)
            results.append(
                InlineQueryResultArticle(
                    id="current_music",
                    title=f"🎵 {track['title']}",
                    description=f"🎤 {artists_str}" if artists_str else "Текущий трек",
                    input_message_content=InputTextMessageContent(
                        message_text=text,
                        parse_mode="HTML"
                    ),
                    thumb_url=track.get("thumb", ""),
                    reply_markup=build_music_keyboard(track)
                )
            )
        else:
            text = (
                f"{EMOJIS['SLEEP']} <b>Ничего не играет</b>"
                + "\n\n"
                + f"{EMOJIS['YANDEX']} <a href='https://music.yandex.ru'>Яндекс.Музыка</a>"
            )
            results.append(
                InlineQueryResultArticle(
                    id="no_music",
                    title="😴 Ничего не играет",
                    description="Яндекс.Музыка не активна",
                    input_message_content=InputTextMessageContent(
                        message_text=text,
                        parse_mode="HTML"
                    )
                )
            )

    # --- STEAM CURRENT ---
    if query in ["", "current", "steam"]:
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
            + "\n"
            + f"{EMOJIS['MUSIC']} Трек: {last_track_name or 'Нет'}"
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
            + "• <code>music</code> — текущий трек (Я.Музыка)"
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
            + "• <code>music</code> — текущий трек"
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
                description="Доступные: current, music, stats, help",
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
# ОСНОВНАЯ ЛОГИКА STEAM (без изменений)
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

    friends_in_game = await get_friends_playing_same_game(session, game_id)
    is_coop = has_coop_support(details.get("categories", []))

    if friends_in_game and is_coop:
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

                        time_changed = new_playtime_str != current_playtime_str

                        if time_changed:
                            logging.info(f"✏️ Обновление времени: {new_playtime_str}")
                            store_link = f"https://store.steampowered.com/app/{game_id}"
                            new_caption = build_game_caption(
                                last_game_name, cached_game_details["developers"], cached_game_details["publishers"],
                                cached_game_details["metacritic"], cached_game_details["genres"], new_playtime_str, store_link,
                                coop_friends=last_coop_friends
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
                global last_coop_friends
                last_coop_friends = friends
                await _update_post_with_friends(game_id, msg_id, friends)
                await callback_query.answer(f"👥 Добавлен: {friends[0]}")
                return

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


# ==========================================
# ОБРАБОТКА СООБЩЕНИЙ
# ==========================================

@dp.message(F.text == "/music")
async def cmd_music(message: Message):
    """Ручная проверка текущего трека"""
    if not YANDEX_MUSIC_TOKEN:
        await message.answer(
            "❌ <b>YANDEX_MUSIC_TOKEN не задан в .env</b>",
            parse_mode="HTML"
        )
        return

    async with ClientSession() as session:
        track = await get_yandex_track(session)

    if track:
        caption = format_track_caption(track)
        keyboard = build_music_keyboard(track)
        if track.get("thumb"):
            await message.answer_photo(photo=track["thumb"], caption=caption, parse_mode="HTML", reply_markup=keyboard)
        else:
            await message.answer(caption, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(
            f"{EMOJIS['SLEEP']} <b>Сейчас ничего не играет</b> или не удалось получить данные от Я.Музыки.",
            parse_mode="HTML"
        )


@dp.message()
async def echo_handler(message: Message):
    steam_status = f"{EMOJIS['GAME']} Играю в <b>{last_game_name}</b>" if last_game_name else f"{EMOJIS['SLEEP']} Не играю"
    music_status = f"{EMOJIS['MUSIC']} Слушаю <b>{last_track_name}</b>" if last_track_name else f"{EMOJIS['SLEEP']} Не слушаю"

    await message.answer(
        f"Бот работает! {EMOJIS['CHECK']}"
        + "\n\n"
        + f"Steam: {steam_status}"
        + "\n"
        + f"Music: {music_status}"
        + "\n\n"
        + f"💡 <b>Inline-режим:</b> Напиши @{BOT_USERNAME} в любом чате!"
        + "\n"
        + f"🎵 <b>Команда:</b> /music",
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
    if YANDEX_MUSIC_TOKEN:
        asyncio.create_task(yandex_monitor())
    else:
        logging.info("ℹ️ Мониторинг Яндекс.Музыки пропущен (нет токена)")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())