
import os
import re
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from threading import Thread

import aiohttp
import discord
from bs4 import BeautifulSoup
from flask import Flask
from discord.ext import tasks

# =========================================================
# AYARLAR
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))

SOURCE_URL = "https://bigpara.hurriyet.com.tr/doviz/"
UPDATE_SECONDS = 120
BUTTON_COOLDOWN = 15
TZ = ZoneInfo("Europe/Istanbul")
TITLE = "💱 Canlı Döviz Kurları"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("currency-monitor")

if not TOKEN or CHANNEL_ID <= 0:
    raise RuntimeError(
        "DISCORD_TOKEN ve CHANNEL_ID ortam değişkenlerini ayarla."
    )

# Bigpara'da gösterilen yaygın dövizler
CURRENCIES = [
    ("USD", "🇺🇸", "Dolar", ("dolar",)),
    ("EUR", "🇪🇺", "Euro", ("euro",)),
    ("GBP", "🇬🇧", "İngiliz Sterlini", ("sterlin",)),
    ("CHF", "🇨🇭", "İsviçre Frangı", ("isviçre frangı",)),
    ("JPY", "🇯🇵", "Japon Yeni", ("japon yeni",)),
    ("SAR", "🇸🇦", "Suudi Arabistan Riyali", ("suudi arabistan riyali",)),
    ("NOK", "🇳🇴", "Norveç Kronu", ("norveç kronu",)),
    ("DKK", "🇩🇰", "Danimarka Kronu", ("danimarka kronu",)),
    ("AUD", "🇦🇺", "Avustralya Doları", ("avustralya doları",)),
    ("CAD", "🇨🇦", "Kanada Doları", ("kanada doları",)),
    ("SEK", "🇸🇪", "İsveç Kronu", ("isveç kronu",)),
    ("RUB", "🇷🇺", "Rus Rublesi", ("rus rublesi", "rublesi")),
    ("KWD", "🇰🇼", "Kuveyt Dinarı", ("kuveyt dinarı",)),
    ("CNY", "🇨🇳", "Çin Yuanı", ("çin yuanı", "yuanı")),
    ("AED", "🇦🇪", "BAE Dirhemi", ("bae dirhemi", "dirhemi")),
]

# =========================================================
# WEB SAĞLIK KONTROLÜ
# =========================================================

web = Flask(__name__)


@web.route("/")
def home():
    return {
        "service": "Currency Monitor",
        "status": "running",
        "source": SOURCE_URL,
        "interval_seconds": UPDATE_SECONDS
    }


@web.route("/health")
def health():
    return {"status": "ok"}, 200


def run_web():
    web.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


# =========================================================
# SAYISAL VERİ İŞLEME
# =========================================================

def parse_number(text):
    """Türkçe ve İngilizce ondalık gösterimlerini dönüştürür."""
    if not text:
        return None

    value = (
        str(text)
        .replace("\xa0", "")
        .replace("₺", "")
        .replace("TL", "")
        .replace("%", "")
        .replace(" ", "")
        .strip()
    )

    if not re.fullmatch(r"[+-]?\d[\d.,]*", value):
        return None

    # Türkçe gösterim: 49.789,20
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")

    elif "," in value:
        value = value.replace(",", ".")

    try:
        return float(value)
    except ValueError:
        return None


def fmt(value, digits=4):
    if value is None:
        return "—"

    return (
        f"{value:,.{digits}f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def fmt_percent(value):
    if value is None:
        return "—"

    sign = "+" if value > 0 else ""
    return f"{sign}{fmt(value, 2)}%"


def normalized(text):
    text = str(text).casefold()
    return (
        text.replace("ı", "i")
        .replace("İ", "i")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
    )


def identify_currency(text):
    text = normalized(text)

    # Önce uzun ve ayırt edici isimler
    for code, flag, name, aliases in CURRENCIES:
        if any(normalized(alias) in text for alias in aliases):
            return code

    return None


# =========================================================
# BIGPARA SAYFA OKUYUCU
# =========================================================

async def fetch_bigpara():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
        "Cache-Control": "no-cache",
    }

    timeout = aiohttp.ClientTimeout(total=25)

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout
    ) as session:
        async with session.get(SOURCE_URL) as response:
            response.raise_for_status()
            html = await response.text()

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    if len(page_text) < 500:
        raise ValueError("Bigpara sayfası eksik veya boş geldi.")

    # Sayfa son güncelleme zamanı
    source_update = None
    update_match = re.search(
        r"Son Güncelleme\s*"
        r"(\d{1,2}\s+\w+\s*,?\s*\d{1,2}:\d{2}(?::\d{2})?)",
        page_text,
        flags=re.IGNORECASE
    )

    if update_match:
        source_update = update_match.group(1)

    result = {}

    # Sayfa tablolarındaki satırları tara
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)

        if len(cells) < 2:
            continue

        row_text = " ".join(
            cell.get_text(" ", strip=True) for cell in cells
        )

        code = identify_currency(row_text)

        if not code:
            continue

        # Her hücreyi ayrı okumak sütun sırasını korur.
        # Fiyat, yüzde değişim, fark, alış, satış,
        # günlük yüksek, günlük düşük beklenir.
        numeric_cells = []

        for cell in cells:
            cell_text = cell.get_text(" ", strip=True)

            # Yüzde değerleri dahil, hücredeki ilk sayı
            match = re.search(
                r"(?<!\w)[+-]?\d[\d.,]*(?:\s*%)?",
                cell_text
            )

            if match:
                numeric_cells.append(
                    parse_number(match.group(0))
                )
            else:
                numeric_cells.append(None)

        # Para adı genellikle ilk sütundadır. Sayısal
        # sütunların başlangıcını tespit et.
        first_numeric = next(
            (
                i for i, cell in enumerate(cells)
                if parse_number(
                    re.sub(
                        r"%",
                        "",
                        cell.get_text(" ", strip=True)
                    )
                ) is not None
            ),
            None
        )

        if first_numeric is None:
            continue

        values = numeric_cells[first_numeric:]

        # Fiyat ve yüzde değişim en azından bulunmalı.
        # Eksik hücreler uydurulmaz.
        while values and values[-1] is None:
            values.pop()

        if not values or values[0] is None:
            continue

        price = values[0]

        # Sütunlar Bigpara tablosunun gerçek sırasına
        # göre yorumlanır. Alan yoksa None kalır.
        change = values[1] if len(values) > 1 else None
        difference = values[2] if len(values) > 2 else None
        buy = values[3] if len(values) > 3 else None
        sell = values[4] if len(values) > 4 else None
        high = values[5] if len(values) > 5 else None
        low = values[6] if len(values) > 6 else None

        # Bozuk satırı kabul etme.
        if price <= 0:
            continue

        # Aynı para birimi birden fazla tabloda bulunursa
        # ilk geçerli serbest piyasa satırını koru.
        if code not in result:
            result[code] = {
                "price": price,
                "change": change,
                "difference": difference,
                "buy": buy,
                "sell": sell,
                "high": high,
                "low": low,
                "row_text": row_text[:300],
            }

    if len(result) < 3:
        raise ValueError(
            "Bigpara tablosundan yeterli kur okunamadı. "
            "HTML yapısı değişmiş veya erişim engellenmiş olabilir."
        )

    return {
        "rates": result,
        "source_update": source_update,
        "fetched_at": datetime.now(TZ),
    }


# =========================================================
# DISCORD EMBED
# =========================================================

def make_embed(snapshot=None, error=None):
    now = datetime.now(TZ)

    if snapshot is None:
        embed = discord.Embed(
            title=TITLE,
            description=(
                "⚠️ **Bigpara verisi alınamadı.**\n\n"
                "Yeni fiyatlar doğrulanamadığı için eski veriler "
                "güncelmiş gibi gösterilmiyor.\n"
                "Güncelle butonunu daha sonra tekrar deneyebilirsin."
            ),
            color=0xE74C3C,
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(
            name="Son kontrol",
            value=now.strftime("%d.%m.%Y %H:%M:%S"),
            inline=False
        )

        embed.add_field(
            name="Hata",
            value=(error or "Veri alınamadı")[:900],
            inline=False
        )

        embed.add_field(
            name="Kaynak",
            value=f"[Bigpara Döviz Sayfası]({SOURCE_URL})",
            inline=False
        )

        return embed

    rates = snapshot["rates"]

    embed = discord.Embed(
        title=TITLE,
        url=SOURCE_URL,
        description=(
            "📈 **Serbest piyasa döviz tablosu**\n"
            "Kaynak: Bigpara / Hürriyet\n"
            "Fiyatlar kaynak sayfadan okunur; çapraz kur hesaplanmaz."
        ),
        color=0x20C997,
        timestamp=datetime.now(timezone.utc)
    )

    for code, flag, name, aliases in CURRENCIES:
        data = rates.get(code)

        if not data:
            continue

        change = data["change"]
        color_icon = (
            "🟢" if change is not None and change > 0
            else "🔴" if change is not None and change < 0
            else "⚪"
        )

        value = (
            f"**Fiyat:** {fmt(data['price'])} TL\n"
            f"**Değişim:** {color_icon} {fmt_percent(change)}\n"
            f"**Alış:** {fmt(data['buy'])} TL\n"
            f"**Satış:** {fmt(data['sell'])} TL\n"
            f"**Günlük yüksek:** {fmt(data['high'])} TL\n"
            f"**Günlük düşük:** {fmt(data['low'])} TL"
        )

        embed.add_field(
            name=f"{flag} {name} ({code})",
            value=value,
            inline=True
        )

    embed.add_field(
        name="🕒 Kaynak güncelleme zamanı",
        value=snapshot["source_update"] or (
            "Sayfada güncelleme zamanı bulunamadı"
        ),
        inline=False
    )

    embed.add_field(
        name="🔄 Botun kontrol zamanı",
        value=snapshot["fetched_at"].strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
        inline=True
    )

    embed.add_field(
        name="⏱️ Otomatik kontrol",
        value=f"{UPDATE_SECONDS} saniyede bir",
        inline=True
    )

    embed.add_field(
        name="⚠️ Bilgilendirme",
        value=(
            "Kontrol saati, fiyatların o anda değiştiği anlamına gelmez. "
            "Güncellik Bigpara'nın kendi veri akışına bağlıdır. "
            "Alış ve satış fiyatları kaynakta bulunmuyorsa — gösterilir."
        ),
        inline=False
    )

    embed.set_footer(
        text="Kaynak verisi • Otomatik kontrol • Manuel yenileme"
    )

    return embed


# =========================================================
# BOT VE MESAJ YÖNETİMİ
# =========================================================

intents = discord.Intents.default()
client = discord.Client(intents=intents)

market_message = None
update_lock = asyncio.Lock()
last_button_time = 0.0


async def get_channel():
    channel = client.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await client.fetch_channel(CHANNEL_ID)

    if not hasattr(channel, "send"):
        raise RuntimeError("Bu Discord kanalı mesaj kabul etmiyor.")

    return channel


async def find_market_message(channel):
    global market_message

    known_titles = {
        TITLE,
        "📊 Canlı Döviz ve Piyasa Monitörü",
        "📊 DEXAR | Döviz Piyasası",
        "💱 Döviz Piyasası",
    }

    try:
        async for message in channel.history(limit=100):
            if message.author != client.user or not message.embeds:
                continue

            if message.embeds[0].title not in known_titles:
                continue

            if market_message is None:
                market_message = message
            elif message.id != market_message.id:
                # Yalnızca eski bot mesajlarını temizle.
                try:
                    await message.delete()
                except discord.HTTPException:
                    log.warning("Eski mesaj silinemedi: %s", message.id)

    except discord.Forbidden:
        log.warning(
            "Read Message History izni yok; eski mesaj bulunamayabilir."
        )

    return market_message


async def publish(snapshot=None, error=None):
    global market_message

    channel = await get_channel()
    await find_market_message(channel)

    embed = make_embed(snapshot, error)
    view = MarketView()

    if market_message is None:
        market_message = await channel.send(embed=embed, view=view)
    else:
        try:
            await market_message.edit(embed=embed, view=view)
        except discord.NotFound:
            market_message = await channel.send(embed=embed, view=view)

    log.info("Mesaj güncellendi.")


# =========================================================
# GÜNCELLE BUTONU
# =========================================================

class MarketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Güncelle",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="currency_refresh_button"
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        global last_button_time

        loop = asyncio.get_running_loop()
        now = loop.time()

        if now - last_button_time < BUTTON_COOLDOWN:
            remaining = max(
                1, int(BUTTON_COOLDOWN - (now - last_button_time))
            )

            await interaction.response.send_message(
                f"Tekrar denemeden önce {remaining} saniye bekle.",
                ephemeral=True
            )
            return

        last_button_time = now

        await interaction.response.defer(thinking=True)

        try:
            async with update_lock:
                snapshot = await fetch_bigpara()
                await publish(snapshot)

            await interaction.followup.send(
                "Bigpara kontrolü tamamlandı. Mesajdaki kaynak zamanını kontrol et.",
                ephemeral=True
            )

        except Exception as exc:
            log.exception("Manuel yenileme başarısız.")

            try:
                await publish(error=str(exc))
            except Exception:
                log.exception("Hata mesajı güncellenemedi.")

            await interaction.followup.send(
                "Veri alınamadı. Bot loglarını kontrol et.",
                ephemeral=True
            )


# =========================================================
# OTOMATİK GÜNCELLEME
# =========================================================

@tasks.loop(seconds=UPDATE_SECONDS)
async def automatic_update():
    try:
        async with update_lock:
            snapshot = await fetch_bigpara()
            await publish(snapshot)

    except Exception as exc:
        log.exception("Otomatik güncelleme başarısız.")

        try:
            await publish(error=str(exc))
        except Exception:
            log.exception("Hata durumu Discord'a yazılamadı.")


@automatic_update.before_loop
async def before_automatic_update():
    await client.wait_until_ready()


@client.event
async def on_ready():
    log.info("Bot bağlandı: %s", client.user)

    client.add_view(MarketView())

    await client.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="Döviz kurları"
        )
    )

    if not automatic_update.is_running():
        automatic_update.start()


# =========================================================
# BAŞLAT
# =========================================================

async def main():
    Thread(target=run_web, daemon=True).start()

    async with client:
        await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
