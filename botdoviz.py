
import os
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord
from flask import Flask
from threading import Thread

# ==================================================
# DEXAR MARKET MONITOR
# ==================================================

BOT_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", "10000"))

API_URL = "https://open.er-api.com/v6/latest/USD"
UPDATE_INTERVAL = 900  # 15 dakikada bir kontrol
MESSAGE_TITLE = "📊 DEXAR | Döviz Piyasası"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("dexar-market")

if not BOT_TOKEN:
    raise RuntimeError("DISCORD_TOKEN ortam değişkeni eksik!")

if not CHANNEL_ID_RAW:
    raise RuntimeError("CHANNEL_ID ortam değişkeni eksik!")

try:
    CHANNEL_ID = int(CHANNEL_ID_RAW)
except ValueError:
    raise RuntimeError("CHANNEL_ID sayısal bir Discord kanal ID'si olmalı!")

# ==================================================
# RENDER HEALTH CHECK
# ==================================================

app = Flask(__name__)


@app.route("/")
def home():
    return {
        "bot": "DEXAR Market Monitor",
        "status": "running",
        "data_type": "Daily reference exchange rates",
        "check_interval_seconds": UPDATE_INTERVAL
    }


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def run_web():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


def keep_alive():
    Thread(target=run_web, daemon=True).start()


# ==================================================
# DISCORD
# ==================================================

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True

client = discord.Client(intents=intents)

http_session = None
updater_task = None
status_message = None


# ==================================================
# FORMAT HELPERS
# ==================================================

def format_tl(value):
    """Türkçe sayı görünümü: 42.123,45 TL"""
    formatted = f"{value:,.2f}"
    formatted = formatted.replace(",", "_")
    formatted = formatted.replace(".", ",")
    formatted = formatted.replace("_", ".")
    return f"{formatted} TL"


def turkey_time():
    return datetime.now(
        ZoneInfo("Europe/Istanbul")
    ).strftime("%d.%m.%Y %H:%M:%S")


# ==================================================
# RATE PROVIDER
# ==================================================

async def get_fiat_rates():
    global http_session

    try:
        timeout = aiohttp.ClientTimeout(total=15)

        async with http_session.get(
            API_URL,
            timeout=timeout,
            headers={"Accept": "application/json"}
        ) as response:

            response.raise_for_status()
            data = await response.json(content_type=None)

        if data.get("result") != "success":
            raise ValueError(
                f"API başarısız yanıt verdi: {data.get('result')}"
            )

        rates = data.get("rates")

        if not isinstance(rates, dict):
            raise ValueError("API kur tablosu geçersiz.")

        usd_try = rates.get("TRY")
        eur_usd = rates.get("EUR")
        gbp_usd = rates.get("GBP")
        chf_usd = rates.get("CHF")

        values = [usd_try, eur_usd, gbp_usd, chf_usd]

        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value <= 0
            for value in values
        ):
            raise ValueError("Eksik veya geçersiz döviz verisi.")

        # API: 1 USD kaç birim para ediyor?
        # Çapraz kur: TRY / ilgili para biriminin USD karşılığı
        eur_try = usd_try / eur_usd
        gbp_try = usd_try / gbp_usd
        chf_try = usd_try / chf_usd

        result = {
            "USD": usd_try,
            "EUR": eur_try,
            "GBP": gbp_try,
            "CHF": chf_try,
            "source_date": data.get("time_last_update_utc", "Bilinmiyor"),
            "next_update": data.get("time_next_update_utc", "Bilinmiyor"),
        }

        log.info(
            "Kur verisi alındı. Kaynak güncellemesi: %s",
            result["source_date"]
        )

        return result

    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
        KeyError,
        TypeError
    ) as error:
        log.exception("Kur verisi alınamadı: %s", error)
        return None

    except Exception:
        log.exception("Beklenmeyen API hatası.")
        return None


# ==================================================
# EMBED
# ==================================================

def create_market_embed(rates, error=None):
    now = turkey_time()

    if rates is None:
        embed = discord.Embed(
            title=MESSAGE_TITLE,
            description=(
                "⚠️ **Veri sağlayıcısına ulaşılamadı.**\n\n"
                "Yeni kur verisi doğrulanamadı. "
                "Eski fiyatlar güncelmiş gibi gösterilmiyor.\n"
                "Bot otomatik olarak tekrar deneyecek."
            ),
            color=0xFF4545,
            timestamp=datetime.now(ZoneInfo("UTC"))
        )

        embed.add_field(
            name="Son kontrol",
            value=now,
            inline=False
        )

        if error:
            embed.add_field(
                name="Durum",
                value="Bağlantı veya veri doğrulama hatası",
                inline=False
            )

    else:
        embed = discord.Embed(
            title=MESSAGE_TITLE,
            description=(
                "💱 **Döviz kurları / TL**\n"
                "Kaynak: ExchangeRate-API\n"
                "[Kaynağı aç](https://www.exchangerate-api.com/)"
            ),
            color=0x00D68F,
            timestamp=datetime.now(ZoneInfo("UTC"))
        )

        currencies = [
            ("🇺🇸 Dolar (USD/TRY)", "USD"),
            ("🇪🇺 Euro (EUR/TRY)", "EUR"),
            ("🇬🇧 Sterlin (GBP/TRY)", "GBP"),
            ("🇨🇭 İsviçre Frangı (CHF/TRY)", "CHF"),
        ]

        for label, key in currencies:
            embed.add_field(
                name=label,
                value=f"**{format_tl(rates[key])}**",
                inline=True
            )

        embed.add_field(
            name="📅 Kaynağın son veri güncellemesi",
            value=str(rates["source_date"]),
            inline=False
        )

        embed.add_field(
            name="🔄 Kaynağın sonraki güncelleme zamanı",
            value=str(rates["next_update"]),
            inline=False
        )

        embed.add_field(
            name="🕒 Botun son kontrolü (Türkiye)",
            value=now,
            inline=False
        )

        embed.add_field(
            name="📡 Veri tipi",
            value="Günlük referans kuru — anlık alış/satış fiyatı değildir.",
            inline=False
        )

    embed.set_footer(
        text="DEXAR MARKET • Otomatik takip sistemi"
    )

    return embed


# ==================================================
# SINGLE MESSAGE MANAGEMENT
# ==================================================

async def find_existing_message(channel):
    """Botun eski piyasa mesajını bulur; kanal geçmişini silmez."""
    try:
        async for message in channel.history(limit=100):
            if message.author != client.user:
                continue

            if not message.embeds:
                continue

            if message.embeds[0].title == MESSAGE_TITLE:
                log.info("Önceki piyasa mesajı bulundu: %s", message.id)
                return message

    except discord.Forbidden:
        log.error(
            "Mesaj geçmişi okunamıyor. Read Message History izni gerekli."
        )
    except discord.HTTPException:
        log.exception("Eski mesaj aranırken Discord hatası.")

    return None


async def publish_or_edit(channel, embed):
    global status_message

    try:
        if status_message is not None:
            await status_message.edit(embed=embed)
        else:
            status_message = await find_existing_message(channel)

            if status_message is not None:
                await status_message.edit(embed=embed)
            else:
                status_message = await channel.send(embed=embed)

        log.info("Piyasa mesajı başarıyla güncellendi.")

    except discord.NotFound:
        status_message = await channel.send(embed=embed)
        log.info("Silinmiş mesajın yerine yeni mesaj oluşturuldu.")

    except discord.Forbidden:
        log.error(
            "Botun mesaj gönderme veya düzenleme izni yok."
        )
        raise

    except discord.HTTPException:
        log.exception("Discord mesaj işlemi başarısız.")
        raise


# ==================================================
# BACKGROUND UPDATER
# ==================================================

async def background_market_updater():
    global http_session

    await client.wait_until_ready()

    channel = client.get_channel(CHANNEL_ID)

    if channel is None:
        try:
            channel = await client.fetch_channel(CHANNEL_ID)
        except discord.HTTPException:
            log.exception("Kanal bulunamadı veya erişilemiyor.")
            return

    if not hasattr(channel, "send"):
        log.error("Verilen kanal mesaj gönderilebilir bir kanal değil.")
        return

    log.info("Piyasa takip sistemi başladı.")

    async with aiohttp.ClientSession() as session:
        http_session = session

        while not client.is_closed():
            try:
                rates = await get_fiat_rates()

                embed = create_market_embed(
                    rates,
                    error=None if rates else "Veri alınamadı"
                )

                await publish_or_edit(channel, embed)

            except asyncio.CancelledError:
                raise

            except Exception:
                log.exception(
                    "Güncelleme döngüsünde hata; yeniden denenecek."
                )

            await asyncio.sleep(UPDATE_INTERVAL)


# ==================================================
# STARTUP
# ==================================================

@client.event
async def on_ready():
    global updater_task

    log.info("Discord bağlantısı kuruldu: %s", client.user)

    await client.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Döviz piyasası 📈"
        )
    )

    # Yeniden bağlantıda ikinci bir döngü başlatma.
    if updater_task is None or updater_task.done():
        updater_task = asyncio.create_task(
            background_market_updater()
        )


@client.event
async def on_disconnect():
    log.warning("Discord bağlantısı kesildi; yeniden bağlanma bekleniyor.")


async def main():
    keep_alive()

    async with client:
        await client.start(BOT_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot kapatıldı.")
