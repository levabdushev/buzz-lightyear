

import asyncio
import aiohttp
import logging
import html
import re
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F ### <--- ИЗМЕНЕНИЕ: Добавлен импорт F для фильтрации текста
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.filters.command import CommandObject
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- КОНФИГУРАЦИЯ БОТА ---
TOKEN = "8055308157:AAFyJoxu8mExTPanr1WtxwgRiGdv_zzNM6A"  # ЗАМЕНИТЕ НА ВАШ ТОКЕН
BOT_DATA_FILE = "bot_data.json"

# --- КОНСТАНТЫ ДЛЯ PVB ---
PVB_STOCK_API_URL = "https://plantsvsbrainrots.com/api/latest-message"

PVB_SEEDS = [
    "cactusseed", "strawberryseed", "pumkinseed", "sunflowerseed",
    "dragonfruitseed", "eggplantseed", "watermelonseed", "grapeseed",
    "cocotankseed", "carnivorousplantseed", "mrcarrotseed", "tomatrioseed",
    "shroombinoseed", "mangoseed"
]
PVB_GEAR = ["carrotlauncher", "frostblower", "bananagun", "frostgrenade", "waterbucket"]
ALL_ITEMS = PVB_SEEDS + PVB_GEAR # <-- Объединенный список для индексации

EMOJI_MAP = {
    "eggplantseed": "🍆", "dragonfruitseed": "🐉", "sunflowerseed": "🌻",
    "pumpkinseed": "🎃", "strawberryseed": "🍓", "cactusseed": "🌵",
    "watermelonseed": "🍉", "grapeseed": "🍇", "cocotankseed": "🥥",
    "carnivorousplantseed": "🪴", "mrcarrotseed": "🥕", "tomatrioseed": "🍅",
    "shroombinoseed": "🍄", "mangoseed": "🥭", "carrotlauncher": "🥕", "frostblower": "💨",
    "bananagun": "🍌", "frostgrenade": "❄️", "waterbucket": "💧",
    "clock": "⏰"
}

# --- НАСТРОЙКА ЛОГГИРОВАНИЯ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- ИНИЦИАЛИЗАЦИЯ БОТА И ДИСПЕТЧERA ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
bot_data_lock = asyncio.Lock()
last_pvb_stock_data = {"id": None, "notified_users_targets": []}
bot_data = {}
PENDING_EDITS = {}  # 🚀 Словарь для задач анти-флуда

# --- ФУНКЦИИ СОХРАНЕНИЯ И ЗАГРУЗКИ ДАННЫХ ---
def load_data():
    global bot_data
    if os.path.exists(BOT_DATA_FILE):
        try:
            with open(BOT_DATA_FILE, 'r', encoding='utf-8') as f:
                bot_data = json.load(f)
                bot_data['user_configs'] = {int(k): v for k, v in bot_data.get('user_configs', {}).items()}
                for user_id, config in bot_data['user_configs'].items():
                    config['targets'] = {int(k): v for k, v in config.get('targets', {}).items()}
                logging.info(f"Data successfully loaded from {BOT_DATA_FILE}")
        except (json.JSONDecodeError, TypeError) as e:
            logging.error(f"Could not load data from {BOT_DATA_FILE}: {e}. Starting with empty data.")
            bot_data = {"user_configs": {}}
    else:
        logging.info(f"{BOT_DATA_FILE} not found. Starting with empty data.")
        bot_data = {"user_configs": {}}

def save_data():
    asyncio.create_task(save_data_async())

async def save_data_async():
    async with bot_data_lock:
        try:
            with open(BOT_DATA_FILE, 'w', encoding='utf-8') as f:
                data_to_save = deepcopy(bot_data)
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)
                logging.info(f"Data successfully saved to {BOT_DATA_FILE}")
        except Exception as e:
            logging.error(f"Failed to save data: {e}", exc_info=True)

# --- ФУНКЦИЯ ФОРМАТИРОВАНИЯ ---
### <--- ИЗМЕНЕНИЕ: Функция теперь принимает необязательный аргумент `highlight_items` для подсветки
def format_pvb_description(description: str, highlight_items: set = None) -> str:
    if highlight_items is None:
        highlight_items = set()

    ITEM_ORDER = {
        "cactusseed": 1, "strawberryseed": 2, "pumpkinseed": 3, "sunflowerseed": 4,
        "dragonfruitseed": 5, "eggplantseed": 6, "watermelonseed": 7, "grapeseed": 8,
        "cocotankseed": 9, "carnivorousplantseed": 10, "mrcarrotseed": 11,
        "tomatrioseed": 12, "shroombinoseed": 13, "mangoseed": 14, "waterbucket": 15, "frostgrenade": 16,
        "bananagun": 17, "frostblower": 18, "carrotlauncher": 19
    }
    formatted_lines, raw_lines = [], description.split('\n')
    seeds_items, gear_items, current_section = [], [], None
    
    for line in raw_lines:
        cleaned_line = line.replace('**', '')
        if match := re.search(r'<t:(\d+):R>', cleaned_line):
            change_time = datetime.fromtimestamp(int(match.group(1))).strftime('%H:%M:%S')
            cleaned_line = cleaned_line.replace(match.group(0), f"{change_time} по МСК")
        
        if cleaned_line.strip().lower() == "seeds":
            current_section = "seeds"
            continue
        if cleaned_line.strip().lower() == "gear":
            current_section = "gear"
            continue

        if match := re.search(r'<:(\w+):\d+>\s*(.*)', cleaned_line):
            item_name, rest_of_line = match.group(1), match.group(2)
            emoji = EMOJI_MAP.get(item_name, '▫️')
            item_line = f"{emoji} {rest_of_line}"
            
            ### <--- ИЗМЕНЕНИЕ: Добавляем теги <b>, если предмет нужно подсветить
            if item_name in highlight_items:
                item_line = f"<b>{item_line}</b>"

            if current_section == "seeds" and item_name in PVB_SEEDS:
                seeds_items.append((ITEM_ORDER.get(item_name, 99), item_line))
            elif current_section == "gear" and item_name in PVB_GEAR:
                gear_items.append((ITEM_ORDER.get(item_name, 99), item_line))
        else:
            formatted_lines.append(cleaned_line)

    seeds_items.sort(key=lambda x: x[0])
    gear_items.sort(key=lambda x: x[0])
    
    formatted_lines.append("<b>👾Seeds stock:\n</b>")
    formatted_lines.extend(item[1] for item in seeds_items)
    formatted_lines.append("<b>\n🪏Gear stock:\n</b>")
    formatted_lines.extend(item[1] for item in gear_items)
    return "\n".join(formatted_lines).strip()

# --- ФУНКЦИИ ГЕНЕРАЦИИ КЛАВИАТУР ---
def get_user_config(user_id: int) -> dict:
    user_configs = bot_data.setdefault("user_configs", {})
    return user_configs.setdefault(user_id, {"channels": [], "targets": {}})

def get_target_config(user_id: int, target_chat_id: int) -> dict:
    user_config = get_user_config(user_id)
    targets = user_config.setdefault("targets", {})
    return targets.setdefault(target_chat_id, {"is_active": False, "tracked_items": []})

def generate_autostock_main_markup(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    user_config = get_user_config(user_id)
    ls_target_config = get_target_config(user_id, user_id)
    ls_mark = "✅" if ls_target_config.get("is_active", False) else "❌"
    buttons.append([InlineKeyboardButton(text=f"{ls_mark} Личные сообщения", callback_data=f"as_sel:{user_id}:{user_id}")])
    for channel in user_config.get("channels", []):
        channel_target_config = get_target_config(user_id, channel['id'])
        channel_mark = "✅" if channel_target_config.get("is_active", False) else "❌"
        buttons.append([InlineKeyboardButton(text=f"{channel_mark} {html.escape(channel['name'])}", callback_data=f"as_sel:{channel['id']}:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def generate_autostock_target_menu_markup(user_id: int, target_chat_id: int) -> InlineKeyboardMarkup:
    target_config = get_target_config(user_id, target_chat_id)
    is_enabled = target_config.get("is_active", False)
    toggle_text = "❌ Отключить" if is_enabled else "✅ Включить"
    buttons = [
        [InlineKeyboardButton(text=toggle_text, callback_data=f"as_tog_t:{target_chat_id}:{user_id}")],
        [InlineKeyboardButton(text="⚙️ Настроить предметы", callback_data=f"as_items:{target_chat_id}:{user_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"as_main:{user_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def generate_autostock_items_markup(user_id: int, target_chat_id: int) -> InlineKeyboardMarkup:
    target_config = get_target_config(user_id, target_chat_id)
    tracked_items = target_config.get("tracked_items", [])
    buttons, row = [], []
    for idx, item in enumerate(ALL_ITEMS):
        mark = "✅" if item in tracked_items else "❌"
        display_name = item.replace('seed', '')
        row.append(InlineKeyboardButton(text=f"{mark} {display_name}", callback_data=f"as_ti:{idx}:{target_chat_id}:{user_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row: buttons.append(row)
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"as_sel:{target_chat_id}:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- ОБРАБОТЧИКИ КОМАНД ---
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "👋 <b>Привет! Я бот для мониторинга стока Plants vs Brainrots.</b>\n\n"
        "<b>Доступные команды:</b>\n"
        "  /stock - Показать актуальный сток.\n"
        "  /autostock - Настроить уведомления.\n"
        "  /add <code>@канал</code> - Добавить ваш канал/чат."
    )

### <--- ИЗМЕНЕНИЕ: Обработчик теперь реагирует на команду /stock И на текстовые сообщения
@dp.message(Command('stock'))
@dp.message(F.text.lower().in_([
    'что в стоке', 'что в стоке?', 'что сейчас в стоке?', 'сток',
    'какой сток', 'сток?', 'какой сток?'
]))
async def stock_handler(message: types.Message):
    msg = await message.reply("🔄 Запрашиваю сток...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PVB_STOCK_API_URL, timeout=10) as response:
                response.raise_for_status()
                data = await response.json()
        if not data or not isinstance(data, list):
            return await msg.edit_text("❌ <b>Ошибка API:</b> Некорректный формат данных.")
        description = data[0].get("embeds", [{}])[0].get("description", "Описание не найдено.")
        # Вызываем форматирование без подсветки, так как это просто запрос стока
        await msg.edit_text(f"📦 <b>Актуальный сток PvB:</b>\n\n{format_pvb_description(description)}\n\n<b>Заходи в оффициальный канал бота чтобы не пропускать хорошие стоки:</b>\n@PvBautostock")
    except Exception as e:
        logging.error(f"Error in stock_handler: {e}", exc_info=True)
        await msg.edit_text("❌ <b>Произошла ошибка</b> при получении стока.")

@dp.message(Command('autostock'))
async def autostock_handler(message: types.Message):
    await message.answer(
        "🔔 <b>Настройка уведомлений</b>\n\nВыберите канал или ЛС для настройки.",
        reply_markup=generate_autostock_main_markup(message.from_user.id)
    )

@dp.message(Command('add'))
async def add_channel_handler(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    if not command.args:
        return await message.answer("❌ <b>Ошибка:</b> Укажите юзернейм канала/чата.\n<i>Пример: /add @mychannel</i>")

    chat_username = command.args
    msg = await message.answer(f"🔄 Проверяю права для <code>{html.escape(chat_username)}</code>...")
    try:
        target_chat = await bot.get_chat(chat_username)
        bot_member = await bot.get_chat_member(chat_id=target_chat.id, user_id=bot.id)
        if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
            return await msg.edit_text(f"❌ <b>Ошибка:</b> Я не администратор в «{html.escape(target_chat.title)}».")
        user_member = await bot.get_chat_member(chat_id=target_chat.id, user_id=user_id)
        if user_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            return await msg.edit_text(f"❌ <b>Ошибка:</b> Вы не администратор в «{html.escape(target_chat.title)}».")
        
        async with bot_data_lock:
            user_config = get_user_config(user_id)
            if any(c['id'] == target_chat.id for c in user_config["channels"]):
                return await msg.edit_text("✅ Этот чат уже добавлен.")
            user_config["channels"].append({"id": target_chat.id, "name": target_chat.title})
        save_data()
        await msg.edit_text(f"✅ <b>Успешно!</b> Чат «{html.escape(target_chat.title)}» добавлен.")
    except TelegramBadRequest:
        await msg.edit_text("❌ <b>Ошибка:</b> Чат не найден или я не добавлен в него.")
    except Exception as e:
        logging.error(f"Error in add_channel_handler: {e}", exc_info=True)
        await msg.edit_text("❌ Произошла непредвиденная ошибка.")

# --- ФОНОВАЯ ЗАДАЧА ---
async def autostock_monitor():
    global last_pvb_stock_data
    while True:
        try:
            now = datetime.now()
            sleep_duration = (5 - (now.minute % 5)) * 60 - now.second + 3
            logging.info(f"Next check in {sleep_duration:.0f}s.")
            await asyncio.sleep(sleep_duration)

            logging.info("Waiting 20s for API update...")
            await asyncio.sleep(20)

            logging.info("Starting autostock check...")
            async with aiohttp.ClientSession() as s:
                async with s.get(PVB_STOCK_API_URL, timeout=10) as r:
                    if r.status != 200: continue
                    data = await r.json()
            if not isinstance(data, list) or not data: continue

            latest_message = data[0]
            current_id = latest_message.get("id")

            if current_id != last_pvb_stock_data["id"]:
                logging.info(f"New PvB stock detected! ID: {current_id}.")
                last_pvb_stock_data = {"id": current_id, "notified_users_targets": []}

            description = latest_message.get("embeds", [{}])[0].get("description", "")
            current_stock_items = set(re.findall(r'<:(\w+):\d+>', description))
            if not current_stock_items: continue

            async with bot_data_lock:
                configs_copy = deepcopy(bot_data.get("user_configs", {}))

            for user_id, config in configs_copy.items():
                for target_chat_id, target_config in config.get("targets", {}).items():
                    if not target_config.get("is_active", False): continue
                    if (user_id, target_chat_id) in last_pvb_stock_data["notified_users_targets"]: continue

                    matches = set(target_config.get("tracked_items", [])).intersection(current_stock_items)
                    if matches:
                        logging.info(f"Matches for user {user_id} in target {target_chat_id}: {matches}")
                        ### <--- ИЗМЕНЕНИЕ: Передаем найденные совпадения `matches` в функцию для подсветки
                        formatted_text = format_pvb_description(description, highlight_items=matches)
                        try:
                            await bot.send_message(
                                chat_id=target_chat_id, 
                                text=f"🔔 <b>Auto Stock:</b>\nПоявились нужные предметы!\n\n{formatted_text}"
                            )
                            last_pvb_stock_data["notified_users_targets"].append((user_id, target_chat_id))
                            await asyncio.sleep(0.1)
                        except Exception as e:
                            logging.error(f"Failed to send notification to {target_chat_id}: {e}")
        except Exception as e:
            logging.error(f"Critical error in autostock_monitor: {e}", exc_info=True)
            await asyncio.sleep(60)

# --- СИСТЕМА АНТИ-ФЛУДА ---
async def _execute_markup_update(user_id: int, chat_id: int, message_id: int, target_chat_id: int):
    await asyncio.sleep(1.2)
    key = (chat_id, message_id)
    try:
        logging.info(f"Executing delayed update for msg {message_id}")
        markup = generate_autostock_items_markup(user_id, target_chat_id)
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except TelegramRetryAfter as e:
        logging.warning(f"Flood error, retrying after {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        await _execute_markup_update(user_id, chat_id, message_id, target_chat_id)
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logging.error(f"Failed to edit msg {message_id}: {e}")
    finally:
        PENDING_EDITS.pop(key, None)

async def schedule_markup_update(call: types.CallbackQuery, target_chat_id: int):
    key = (call.message.chat.id, call.message.message_id)
    if key in PENDING_EDITS:
        PENDING_EDITS[key].cancel()
    PENDING_EDITS[key] = asyncio.create_task(
        _execute_markup_update(call.from_user.id, call.message.chat.id, call.message.message_id, target_chat_id)
    )

# --- ОБРАБОТЧИК CALLBACK ---
@dp.callback_query()
async def callback_query_handler(call: types.CallbackQuery):
    user_id = call.from_user.id
    try:
        parts = call.data.split(":")
        action = parts[0]
        owner_id = int(parts[-1])
        if user_id != owner_id:
            return await call.answer("❌ Это кнопки не для вас!", show_alert=True)
        
        if action == "as_sel":
            target_chat_id = int(parts[1])
            user_config = get_user_config(user_id)
            target_chat_name = "Личные сообщения"
            if target_chat_id != user_id:
                target_chat_name = next((c['name'] for c in user_config.get("channels", []) if c['id'] == target_chat_id), "Неизвестный чат")
            await call.message.edit_text(
                f"🔧 <b>Управление для:</b>\n<i>{html.escape(target_chat_name)}</i>", 
                reply_markup=generate_autostock_target_menu_markup(user_id, target_chat_id)
            )
        
        elif action == "as_tog_t":
            target_chat_id = int(parts[1])
            async with bot_data_lock:
                target_config = get_target_config(user_id, target_chat_id)
                target_config["is_active"] = not target_config.get("is_active", False)
                is_active_now = target_config["is_active"]
            save_data()
            await call.answer(f"Уведомления {'включены' if is_active_now else 'отключены'}.")
            await call.message.edit_reply_markup(reply_markup=generate_autostock_target_menu_markup(user_id, target_chat_id))
        
        elif action == "as_items":
            target_chat_id = int(parts[1])
            await call.message.edit_text(
                "📝 <b>Настройка предметов для отслеживания</b>\n\nВыберите нужные вам предметы.",
                reply_markup=generate_autostock_items_markup(user_id, target_chat_id)
            )

        elif action == "as_ti":
            item_index, target_chat_id = int(parts[1]), int(parts[2])
            item_name = ALL_ITEMS[item_index]
            async with bot_data_lock:
                target_config = get_target_config(user_id, target_chat_id)
                tracked_items = target_config.get("tracked_items", [])
                if item_name in tracked_items:
                    tracked_items.remove(item_name)
                    await call.answer(f"{item_name.replace('seed','')} отключен.")
                else:
                    tracked_items.append(item_name)
                    await call.answer(f"{item_name.replace('seed','')} включен.")
                target_config["tracked_items"] = tracked_items
            save_data()
            await schedule_markup_update(call, target_chat_id)
        
        elif action == "as_main":
            await call.message.edit_text(
                "🔔 <b>Настройка уведомлений</b>\n\nВыберите канал или ЛС для настройки.",
                reply_markup=generate_autostock_main_markup(user_id)
            )
        
    except Exception as e:
        logging.error(f"Error in callback handler: {e}", exc_info=True)
        await call.answer("Что-то пошло не так...", show_alert=True)

# --- ТОЧКА ВХОДА ---
async def main():
    load_data()
    autostock_task = asyncio.create_task(autostock_monitor())
    logging.info("Starting bot polling...")
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        logging.info("Shutting down...")
        await save_data_async()
        autostock_task.cancel()
        try:
            await autostock_task
        except asyncio.CancelledError:
            logging.info("Autostock monitor task cancelled.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
    except Exception as e:
        logging.critical(f"Bot crashed: {e}", exc_info=True)