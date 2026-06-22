import os
import json
import logging
from datetime import time, datetime
from zoneinfo import ZoneInfo

import aiohttp
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUBSCRIBERS_FILE = "subscribers.json"

LATITUDE = 56.8526
LONGITUDE = 53.2045
IZHEVSK_TZ = ZoneInfo("Europe/Samara")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)


# ─── Подписчики ───

def load_subscribers() -> set:
    if not os.path.exists(SUBSCRIBERS_FILE):
        return set()
    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_subscribers(subs: set):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(subs), f, ensure_ascii=False, indent=2)


subscribers = load_subscribers()


# ─── Расшифровка кодов погоды ───

WEATHER_CODES = {
    0:  ("☀️", "ясно"),
    1:  ("🌤", "в основном ясно"),
    2:  ("⛅", "переменная облачность"),
    3:  ("☁️", "пасмурно"),
    45: ("🌫", "туман"),
    48: ("🌫", "изморозь"),
    51: ("🌦", "слабая морось"),
    53: ("🌦", "морось"),
    55: ("🌧", "сильная морось"),
    56: ("🌧", "ледяная морось"),
    57: ("🌧", "сильная ледяная морось"),
    61: ("🌦", "небольшой дождь"),
    63: ("🌧", "дождь"),
    65: ("🌧", "сильный дождь"),
    66: ("🌧", "ледяной дождь"),
    67: ("🌧", "сильный ледяной дождь"),
    71: ("🌨", "небольшой снег"),
    73: ("🌨", "снег"),
    75: ("❄️", "сильный снег"),
    77: ("🌨", "снежные зёрна"),
    80: ("🌦", "небольшой ливень"),
    81: ("🌧", "ливень"),
    82: ("🌧", "сильный ливень"),
    85: ("🌨", "небольшой снегопад"),
    86: ("❄️", "сильный снегопад"),
    95: ("⛈", "гроза"),
    96: ("⛈", "гроза с градом"),
    99: ("⛈", "гроза с сильным градом"),
}


def decode_weather(code: int) -> tuple[str, str]:
    return WEATHER_CODES.get(code, ("❓", "неизвестно"))


# ─── Определяем «главную» погоду за интервал часов ───

def summarize_interval(
    hours: list[int],
    hourly_codes: list[int],
    hourly_temps: list[float],
    hourly_precip: list[float],
    hourly_wind: list[float],
) -> str:
    """
    Для заданного списка часов (индексов 0-23 или 0-47)
    формирует строку-сводку.
    """
    codes = [hourly_codes[h] for h in hours if h < len(hourly_codes)]
    temps = [hourly_temps[h] for h in hours if h < len(hourly_temps)]
    precips = [hourly_precip[h] for h in hours if h < len(hourly_precip)]
    winds = [hourly_wind[h] for h in hours if h < len(hourly_wind)]

    if not codes:
        return "нет данных"

    # Температура: мин и макс
    t_min = round(min(temps))
    t_max = round(max(temps))
    temp_str = f"{t_min}…{t_max}°C" if t_min != t_max else f"{t_min}°C"

    # Ветер: максимальный
    wind_max = round(max(winds), 1)

    # Суммарные осадки за интервал
    precip_sum = round(sum(precips), 1)

    # Определяем преобладающий weather_code (самый «тяжёлый»)
    worst_code = max(codes)
    emoji, description = decode_weather(worst_code)

    # Собираем строку
    line = f"{emoji} {description}, {temp_str}, ветер до {wind_max} м/с"
    if precip_sum > 0:
        line += f", осадки {precip_sum} мм"

    return line


# ─── Получаем данные и формируем сообщение ───

async def get_forecast_text(is_evening: bool = False) -> str:
    """
    is_evening=False  → утренняя рассылка (08:00), прогноз на сегодня:
        08–12, 12–17, 17–20, 20–08 (ночь → берём часы 20-23 сегодня + 0-7 завтра)

    is_evening=True   → вечерняя рассылка (19:00), прогноз:
        20–08 (ночь → 20-23 сегодня + 0-7 завтра)
        + завтра: 08–12, 12–17, 17–20
    """

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}"
        f"&longitude={LONGITUDE}"
        "&hourly=temperature_2m,weather_code,precipitation,wind_speed_10m"
        "&timezone=Europe%2FSamara"
        "&forecast_days=2"
        "&wind_speed_unit=ms"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                resp.raise_for_status()
                data = await resp.json()
    except Exception as e:
        logging.error(f"Ошибка получения погоды: {e}")
        return "⚠️ Не удалось получить данные о погоде. Попробуйте позже."

    hourly = data["hourly"]
    codes  = hourly["weather_code"]        # 48 значений (2 дня × 24)
    temps  = hourly["temperature_2m"]
    precip = hourly["precipitation"]
    wind   = hourly["wind_speed_10m"]

    # Индексы: день 0 = часы 0..23, день 1 = часы 24..47
    D0 = 0   # смещение дня 0
    D1 = 24  # смещение дня 1

    now = datetime.now(IZHEVSK_TZ)
    today_str = now.strftime("%d.%m.%Y")
    tomorrow = now.date().isoformat()  # для заголовка

    # Готовим дату завтра для заголовка
    from datetime import timedelta
    tomorrow_date = (now + timedelta(days=1)).strftime("%d.%m.%Y")

    if not is_evening:
        # ── Утренняя рассылка ──
        title = f"🌅 Прогноз погоды — Ижевск\n📅 {today_str}\n"

        block_08_12 = summarize_interval(
            list(range(D0 + 8, D0 + 12)), codes, temps, precip, wind
        )
        block_12_17 = summarize_interval(
            list(range(D0 + 12, D0 + 17)), codes, temps, precip, wind
        )
        block_17_20 = summarize_interval(
            list(range(D0 + 17, D0 + 20)), codes, temps, precip, wind
        )
        # Ночь: 20-23 сегодня + 0-7 завтра
        night_hours = list(range(D0 + 20, D0 + 24)) + list(range(D1 + 0, D1 + 8))
        block_20_08 = summarize_interval(
            night_hours, codes, temps, precip, wind
        )

        text = (
            f"{title}\n"
            f"🕗 08:00 – 12:00\n{block_08_12}\n\n"
            f"🕛 12:00 – 17:00\n{block_12_17}\n\n"
            f"🕔 17:00 – 20:00\n{block_17_20}\n\n"
            f"🌙 20:00 – 08:00\n{block_20_08}"
        )

    else:
        # ── Вечерняя рассылка ──
        title = f"🌇 Вечерний прогноз — Ижевск\n📅 {today_str}\n"

        # Ночь: 20-23 сегодня + 0-7 завтра
        night_hours = list(range(D0 + 20, D0 + 24)) + list(range(D1 + 0, D1 + 8))
        block_20_08 = summarize_interval(
            night_hours, codes, temps, precip, wind
        )

        # Завтра
        block_08_12 = summarize_interval(
            list(range(D1 + 8, D1 + 12)), codes, temps, precip, wind
        )
        block_12_17 = summarize_interval(
            list(range(D1 + 12, D1 + 17)), codes, temps, precip, wind
        )
        block_17_20 = summarize_interval(
            list(range(D1 + 17, D1 + 20)), codes, temps, precip, wind
        )

        text = (
            f"{title}\n"
            f"🌙 Сегодня ночью (20:00 – 08:00)\n{block_20_08}\n\n"
            f"── Завтра, {tomorrow_date} ──\n\n"
            f"🕗 08:00 – 12:00\n{block_08_12}\n\n"
            f"🕛 12:00 – 17:00\n{block_12_17}\n\n"
            f"🕔 17:00 – 20:00\n{block_17_20}"
        )

    return text


# ─── Рассылка ───

async def send_to_subscribers(context: ContextTypes.DEFAULT_TYPE, text: str):
    bad = []
    for chat_id in subscribers:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logging.warning(f"chat_id={chat_id}: {e}")
            bad.append(chat_id)
    for cid in bad:
        subscribers.discard(cid)
    if bad:
        save_subscribers(subscribers)


async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    if not subscribers:
        return
    text = await get_forecast_text(is_evening=False)
    await send_to_subscribers(context, text)


async def evening_job(context: ContextTypes.DEFAULT_TYPE):
    if not subscribers:
        return
    text = await get_forecast_text(is_evening=True)
    await send_to_subscribers(context, text)


# ─── Команды ───

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers.add(chat_id)
    save_subscribers(subscribers)
    await update.message.reply_text(
        "✅ Вы подписались на прогноз погоды по Ижевску!\n\n"
        "📬 Рассылка приходит:\n"
        "  • 08:00 — прогноз на день\n"
        "  • 19:00 — прогноз на ночь и завтра\n\n"
        "Команды:\n"
        "/weather — прогноз прямо сейчас\n"
        "/stop — отписаться"
    )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers.discard(chat_id)
    save_subscribers(subscribers)
    await update.message.reply_text("❌ Вы отписались от рассылки.")


async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(IZHEVSK_TZ)
    is_evening = now.hour >= 17
    text = await get_forecast_text(is_evening=is_evening)
    await update.message.reply_text(text)


# ─── Запуск ───

def main():
    if not BOT_TOKEN:
        raise ValueError("Не найден BOT_TOKEN в .env")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("weather", weather_cmd))

    # Утренняя рассылка — 08:00 по Ижевску
    app.job_queue.run_daily(
        morning_job,
        time=time(hour=8, minute=0, tzinfo=IZHEVSK_TZ),
        name="morning_forecast"
    )

    # Вечерняя рассылка — 19:00 по Ижевску
    app.job_queue.run_daily(
        evening_job,
        time=time(hour=19, minute=0, tzinfo=IZHEVSK_TZ),
        name="evening_forecast"
    )

    logging.info("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
