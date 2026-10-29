
import os
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Thread

import discord
import borsapy as bp
from flask import Flask
from discord.ext import tasks

# ==================== AYARLAR ====================
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
MESSAGE_ID_ENV = os.getenv("MESSAGE_ID", "").strip()

TZ = ZoneInfo("Europe/Istanbul")
REFRESH_SECONDS = 60
REQUEST_TIMEOUT = 12

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("canlidoviz")

# ==================== VARLIKLAR ====================
ASSET_MAP = {
    "💵 USD/TRY": ("fx", "USD"),
    "💶 EUR/TRY": ("fx", "EUR"),
    "💷 GBP/TRY": ("fx", "GBP"),
    "💴 JPY/TRY": ("fx", "JPY"),
    "🇨🇭 CHF/TRY": ("fx", "CHF"),
    "🇨🇦 CAD/TRY": ("fx", "CAD"),
    "🇦🇺 AUD/TRY": ("fx", "AUD"),
    "🪙 Gram Altın": ("fx", "gram-altin"),
    "🪙 ONS Altın": ("fx", "ons-altin"),
    "📈 BIST 100": ("index", "XU100"),
    "✈️ THYAO": ("stock", "THYAO"),
    "🏦 GARAN": ("stock", "GARAN"),
    "₿ BTC/USDT": ("crypto", "BTCUSDT"),
    "Ξ ETH/USDT": ("crypto", "ETHUSDT"),
}

# Son başarılı fiyatları bellekte tut.
LAST_PRICES = {}

# ==================== FLASK / RENDER ====================
app = Flask(__name__)


@app.route("/")
def home():
    return "Market bot is running.", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def run_flask():
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        use_reloader=False,
    )


# ==================== FİYAT YARDIMCILARI ====================
def to_number(value):
    if value is None:
        return None

    try:
        number = float(value)
        if number > 0 and number != float("inf"):
            return number
    except (TypeError, ValueError, OverflowError):
        pass

    return None


def format_price(value, asset_type):
    decimals = 4 if asset_type == "fx" else 2
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def get_single_price(asset_type, symbol):
    """borsapy üzerinden tek varlığın son fiyatını alır."""
    try:
        if asset_type == "fx":
            data = bp.FX(symbol).current
        elif asset_type == "index":
            data = bp.Index(symbol).info
        elif asset_type == "stock":
            data = bp.Ticker(symbol).info
        elif asset_type == "crypto":
            data = bp.Crypto(symbol).current
        else:
            return None

        if isinstance(data, dict):
            for key in ("last", "price", "last_price", "close"):
                number = to_number(data.get(key))
                if number is not None:
                    return format_price(number, asset_type)

        # Bazı kütüphane yanıtları dict benzeri olabilir.
        for key in ("last", "price", "last_price", "close"):
            try:
                number = to_number(getattr(data, key))
                if number is not None:
                    return format_price(number, asset_type)
            except (AttributeError, TypeError):
                continue

        log.warning("%s için fiyat alanı bulunamadı.", symbol)
        return None

    except Exception as exc:
        log.warning("%s fiyatı alınamadı: %s", symbol, exc)
        return None


async def fetch_one(asset_type, symbol):
    """Yavaş bir sorgu diğer fiyatların yenilenmesini engellemez."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(get_single_price, asset_type, symbol),
            timeout=REQUEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning(
            "%s sorgusu %s saniyede zaman aşımına uğradı.",
            symbol,
            REQUEST_TIMEOUT,
        )
        return None
    except Exception:
        log.exception("%s sorgusu başarısız.", symbol)
        return None


async def fetch_all():
    names = list(ASSET_MAP.keys())
    assets = list(ASSET_MAP.values())

    results = await asyncio.gather(
        *(
            fetch_one(asset_type, symbol)
            for asset_type, symbol in assets
        ),
        return_exceptions=True,
    )

    prices = {}

    for name, result in zip(names, results):
        if isinstance(result, str):
            prices[name] = result
            LAST_PRICES[name] = result
        else:
            # Son başarılı fiyat varsa geçici hata sırasında koru.
            prices[name] = LAST_PRICES.get(name)

    return prices


# ==================== TABLO ====================
def build_embed(prices):
    now = datetime.now(TZ)

    embed = discord.Embed(
        title="💹 Canlı Piyasa Fiyatları",
        description=(
            "Varlıkların son fiyatları\n"
            "Otomatik güncelleme: **60 saniye**"
        ),
        color=discord.Color.blue(),
        timestamp=now,
    )

    for name in ASSET_MAP:
        price = prices.get(name)

        if price is None:
            value = "`Veri alınamadı`"
        else:
            value = f"**{price}**"

        embed.add_field(
            name=name,
            value=value,
            inline=True,
        )

    embed.set_footer(
        text=f"Son güncelleme: {now:%d.%m.%Y %H:%M:%S} • Europe/Istanbul"
    )

    return embed


# ==================== GÜNCELLE BUTONU ====================
class RefreshView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Güncelle",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="refresh_canlidoviz_btn_v4",
    )
    async def refresh_btn(self, interaction, button):
        await interaction.response.defer()

        try:
            async with self.bot.refresh_lock:
                prices = await fetch_all()
                await interaction.message.edit(
                    embed=build_embed(prices),
                    view=self,
                )
        except Exception:
            log.exception("Butonla yenileme başarısız.")


# ==================== DISCORD BOT ====================
class PriceBot(discord.Client):
    def __init__(self):
        super().__init__(
            intents=discord.Intents.default()
        )
        self.message_id = self.load_message_id()
        self.refresh_lock = asyncio.Lock()
        self.ready_once = False

    @staticmethod
    def load_message_id():
        if MESSAGE_ID_ENV.isdigit():
            return int(MESSAGE_ID_ENV)

        # Render'da dosya sistemi sıfırlanabilir.
        try:
            with open("message_id.txt", "r", encoding="utf-8") as file:
                value = file.read().strip()
                if value.isdigit():
                    return int(value)
        except (OSError, ValueError):
            pass

        return None

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def auto_refresh(self):
        if not self.message_id or CHANNEL_ID == 0:
            return

        async with self.refresh_lock:
            try:
                channel = self.get_channel(CHANNEL_ID)
                if channel is None:
                    channel = await self.fetch_channel(CHANNEL_ID)

                message = await channel.fetch_message(self.message_id)
                prices = await fetch_all()

                await message.edit(
                    embed=build_embed(prices),
                    view=RefreshView(self),
                )

                log.info("Mevcut tablo güncellendi.")

            except discord.NotFound:
                log.error("Tablo mesajı bulunamadı. MESSAGE_ID kontrol et.")
            except discord.Forbidden:
                log.exception("Mesaj düzenleme izni eksik.")
            except Exception:
                log.exception("Otomatik güncelleme başarısız.")

    @auto_refresh.before_loop
    async def before_auto_refresh(self):
        await self.wait_until_ready()

    async def on_ready(self):
        log.info("Bot bağlandı: %s", self.user)

        if self.ready_once:
            return

        self.ready_once = True
        self.add_view(RefreshView(self))
        await self.ensure_message()

        if not self.auto_refresh.is_running():
            self.auto_refresh.start()

    async def ensure_message(self):
        if CHANNEL_ID == 0:
            log.error("CHANNEL_ID ayarlanmamış.")
            return

        channel = self.get_channel(CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.fetch_channel(CHANNEL_ID)
            except Exception:
                log.exception("Kanal bulunamadı.")
                return

        if self.message_id:
            try:
                message = await channel.fetch_message(self.message_id)
                prices = await fetch_all()
                await message.edit(
                    embed=build_embed(prices),
                    view=RefreshView(self),
                )
                log.info("Var olan tablo düzenlendi.")
                return
            except discord.NotFound:
                log.error(
                    "Kayıtlı mesaj silinmiş. Yeni mesaj oluşturulmadı. "
                    "MESSAGE_ID değerini kontrol et."
                )
                return
            except Exception:
                log.exception("Mevcut tablo düzenlenemedi.")
                return

        # İlk kurulumda bir kez mesaj oluştur.
        try:
            prices = await fetch_all()
            message = await channel.send(
                embed=build_embed(prices),
                view=RefreshView(self),
            )
            self.message_id = message.id

            try:
                with open("message_id.txt", "w", encoding="utf-8") as file:
                    file.write(str(message.id))
            except OSError:
                log.warning("Mesaj ID dosyası kaydedilemedi.")

            log.info("İlk tablo oluşturuldu. MESSAGE_ID=%s", message.id)

        except Exception:
            log.exception("İlk tablo gönderilemedi.")


# ==================== BAŞLAT ====================
def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN eksik.")
    if CHANNEL_ID == 0:
        raise RuntimeError("CHANNEL_ID eksik.")

    Thread(target=run_flask, daemon=True).start()
    PriceBot().run(TOKEN)


if __name__ == "__main__":
    main()
