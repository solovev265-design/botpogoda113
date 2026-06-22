import os
import json
import logging
from datetime import time, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ─── Токен берётся из переменной окружения ───
BOT_TOKEN = os.environ.get("BOT_TOKEN")

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

# ─── Коды погоды ───

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
    80: ("🌦", "местами дождь"),
    81: ("🌧", "ливень"),
    82: ("🌧", "сильный ливень"),
    85: ("🌨", "небольшой снегопад"),
    86: ("❄️", "сильный снегопад"),
    95: ("⛈", "гроза"),
    96: ("⛈", "гроза с градом"),
    99: ("⛈", "гроза с сильным градом"),
}


def decode_weather(code: int) -> tuple:
    return WEATHER_CODES.get(code, ("❓", "неизвестно"))


# ─── Сводка за интервал ───

def summarize_interval(
    hours: list,
    hourly_codes: list,
    hourly_temps: list,
    hourly_precip: list,
    hourly_wind: list,
) -> str:
    codes   = [hourly_codes[h]  for h in hours if h < len(hourly_codes)]
    temps   = [hourly_temps[h]  for h in hours if h < len(hourly_temps)]
    precips = [hourly_precip[h] for h in hours if h < len(hourly_precip)]
    winds   = [hourly_wind[h]   for h in hours if h < len(hourly_wind)]

    if not codes:
        return "нет данных"

    t_min = round(min(temps))
    t_max = round(max(temps))
    temp_str = f"{t_min}...{t_max}°C" if t_min != t_max else f"{t_min}°C"

    wind_max   = round(max(winds), 1)
    precip_sum = round(sum(precips), 1)

    worst_code = max(codes)
    emoji, description = decode_weather(worst_code)

    line = f"{emoji} {description}, {temp_str}, ветер до {wind_max} м/с"
    if precip_sum > 0:
        line += f", осадки {precip_sum} мм"

    return line


# ─── Получение прогноза ───

async def get_forecast_text(is_evening: bool = False) -> str:
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
    codes  = hourly["weather_code"]
    temps  = hourly["temperature_2m"]
    precip = hourly["precipitation"]
    wind   = hourly["wind_speed_10m"]

    D0 = 0   # день 0: часы 0-23
    D1 = 24  # день 1: часы 24-47

    now = datetime.now(IZHEVSK_TZ)
    today_str     = now.strftime("%d.%m.%Y")
    tomorrow_str  = (now + timedelta(days=1)).strftime("%d.%m.%Y")

    night_hours = list(range(D0 + 20, D0 + 24)) + list(range(D1 + 0, D1 + 8))

    if not is_evening:
        # ── Утро: прогноз на сегодня ──
        b_08_12 = summarize_interval(list(range(D0 + 8,  D0 + 12)), codes, temps, precip, wind)
        b_12_17 = summarize_interval(list(range(D0 + 12, D0 + 17)), codes, temps, precip, wind)
        b_17_20 = summarize_interval(list(range(D0 + 17, D0 + 20)), codes, temps, precip, wind)
        b_20_08 = summarize_interval(night_hours,                    codes, temps, precip, wind)

        return (
            f"🌅 Прогноз погоды — Ижевск\n"
            f"📅 {today_str}\n\n"
            f"🕗 08:00 – 12:00\n{b_08_12}\n\n"
            f"🕛 12:00 – 17:00\n{b_12_17}\n\n"
            f"🕔 17:00 – 20:00\n{b_17_20}\n\n"
            f"🌙 20:00 – 08:00\n{b_20_08}"
        )
    else:
        # ── Вечер: ночь + завтра ──
        b_20_08 = summarize_interval(night_hours,                    codes, temps, precip, wind)
        b_08_12 = summarize_interval(list(range(D1 + 8,  D1 + 12)), codes, temps, precip, wind)
        b_12_17 = summarize_interval(list(range(D1 + 12, D1 + 17)), codes, temps, precip, wind)
        b_17_20 = summarize_interval(list(range(D1 + 17, D1 + 20)), codes, temps, precip, wind)

        return (
            f"🌇 Вечерний прогноз — Ижевск\n"
            f"📅 {today_str}\n\n"
            f"🌙 Сегодня ночью (20:00 – 08:00)\n{b_20_08}\n\n"
            f"── Завтра, {tomorrow_str} ──\n\n"
            f"🕗 08:00 – 12:00\n{b_08_12}\n\n"
            f"🕛 12:00 – 17:00\n{b_12_17}\n\n"
            f"🕔 17:00 – 20:00\n{b_17_20}"
        )


# ─── Рассылка ───

async def send_to_all(context: ContextTypes.DEFAULT_TYPE, text: str):
    bad = []
    for chat_id in list(subscribers):
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logging.warning(f"Не удалось отправить в chat_id={chat_id}: {e}")
            bad.append(chat_id)
    for cid in bad:
        subscribers.discard(cid)
    if bad:
        save_subscribers(subscribers)


async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Запуск утренней рассылки...")
    if not subscribers:
        logging.info("Нет подписчиков.")
        return
    text = await get_forecast_text(is_evening=False)
    await send_to_all(context, text)


async def evening_job(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Запуск вечерней рассылки...")
    if not subscribers:
        logging.info("Нет подписчиков.")
        return
    text = await get_forecast_text(is_evening=True)
    await send_to_all(context, text)


# ─── Команды ───

async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    chat_type = update.effective_chat.type

    subscribers.add(chat_id)
    save_subscribers(subscribers)

    if chat_type in ["group", "supergroup"]:
        await update.message.reply_text(
            "✅ Группа подписана на рассылку погоды по Ижевску!\n\n"
            "📬 Прогноз приходит:\n"
            "  • 08:00 — прогноз на день\n"
            "  • 19:00 — прогноз на ночь и завтра\n\n"
            "/weather — получить прогноз прямо сейчас\n"
            "/unsubscribe — отписаться"
        )
    else:
        await update.message.reply_text(
            "✅ Вы подписались на рассылку погоды по Ижевску!\n\n"
            "📬 Прогноз приходит:\n"
            "  • 08:00 — прогноз на день\n"
            "  • 19:00 — прогноз на ночь и завтра\n\n"
            "/weather — получить прогноз прямо сейчас\n"
            "/unsubscribe — отписаться"
        )


async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers.discard(chat_id)
    save_subscribers(subscribers)
    await update.message.reply_text("❌ Вы отписались от рассылки погоды.")


async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(IZHEVSK_TZ)
    is_evening = now.hour >= 17
    text = await get_forecast_text(is_evening=is_evening)
    await update.message.reply_text(text)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = len(subscribers)
    await update.message.reply_text(f"📊 Активных подписчиков: {count}")


# ─── Запуск ───

def main():
    if not BOT_TOKEN:
        raise ValueError("Переменная окружения BOT_TOKEN не задана!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       subscribe_cmd))
    app.add_handler(CommandHandler("subscribe",   subscribe_cmd))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_cmd))
    app.add_handler(CommandHandler("stop",        unsubscribe_cmd))
    app.add_handler(CommandHandler("weather",     weather_cmd))
    app.add_handler(CommandHandler("status",      status_cmd))

    # Утро — 08:00 по Ижевску
    app.job_queue.run_daily(
        morning_job,
        time=time(hour=8, minute=0, tzinfo=IZHEVSK_TZ),
        name="morning_forecast"
    )

    # Вечер — 19:00 по Ижевску
    app.job_queue.run_daily(
        evening_job,
        time=time(hour=19, minute=0, tzinfo=IZHEVSK_TZ),
        name="evening_forecast"
    )

    logging.info("Бот успешно запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
