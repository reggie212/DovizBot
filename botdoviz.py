
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

# =========================================================
# AYARLAR
# =========================================================
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
MESSAGE_ID_ENV = os.getenv("MESSAGE_ID", "").strip()

TZ = ZoneInfo("Europe/Istanbul")
REFRESH_SECONDS = 60
MESSAGE_ID_FILE = "message_id.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("canlidoviz")

# =========================================================
# VARLIKLAR
# Desteklenen semboller kullanılıyor.
# =========================================================
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

# =========================================================
# FLASK / RENDER
# =========================================================
app = Flask(__name__)


@app.route("/")
def home():
    return "Live market price bot is running.", 200


@app.route("/health")
def health():
    return {"status": "ok"}, 200


def run_flask():
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        use_reloader=False,
    )


# =========================================================
# FİYAT YARDIMCILARI
# =========================================================
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
    if asset_type == "fx":
        decimals = 4
    else:
        decimals = 2

    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def get_single_price(asset_type, symbol):
    """borsapy 0.11.x API'si ile tek fiyat alır."""

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
            log.warning("Bilinmeyen varlık tipi: %s", asset_type)
            return None

        # API yanıtındaki son fiyatı al.
        raw = None

        if isinstance(data, dict):
            for key in ("last", "price", "last_price", "close"):
                raw = to_number(data.get(key))
                if raw is not None:
                    break
        else:
            for key in ("last", "price", "last_price", "close"):
                try:
                    raw = to_number(getattr(data, key))
                except (AttributeError, TypeError):
                    raw = None

                if raw is not None:
                    break

        if raw is None:
            log.warning("%s için fiyat verisi bulunamadı.", symbol)
            return None

        return format_price(raw, asset_type)

    except Exception as exc:
        # Bir varlık hata verirse diğerleri yine alınır.
        log.warning("%s fiyatı alınamadı: %s", symbol, exc)
        return None


async def fetch_all():
    """Senkron borsapy çağrılarını event loop dışında yürütür."""

    names = list(ASSET_MAP.keys())
    assets = list(ASSET_MAP.values())

    results = await asyncio.gather(
        *(
            asyncio.to_thread(get_single_price, asset_type, symbol)
            for asset_type, symbol in assets
        ),
        return_exceptions=True,
    )

    prices = {}

    for name, result in zip(names, results):
        if isinstance(result, Exception):
            log.error("%s sorgusu başarısız: %s", name, result)
            prices[name] = None
        else:
            prices[name] = result

    return prices


# =========================================================
# DISCORD TABLOSU
# =========================================================
def build_embed(prices, updated_at=None):
    updated_at = updated_at or datetime.now(TZ)

    embed = discord.Embed(
        title="💹 Canlı Piyasa Fiyatları",
        description=(
            "Varlıkların son fiyatları\n"
            "Otomatik yenileme: **60 saniye**"
        ),
        color=discord.Color.blue(),
        timestamp=updated_at,
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
        text=(
            "Son güncelleme: "
            + updated_at.strftime("%d.%m.%Y %H:%M:%S")
            + " • Europe/Istanbul"
        )
    )

    return embed


# =========================================================
# GÜNCELLE BUTONU
# =========================================================
class RefreshView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Güncelle",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="refresh_canlidoviz_btn_v3",
    )
    async def refresh_btn(self, interaction, button):
        # Ephemeral yanıt veya kanala yeni mesaj gönderilmez.
        await interaction.response.defer()

        try:
            async with self.bot.refresh_lock:
                prices = await fetch_all()
                embed = build_embed(prices)

                # Sadece mevcut tablo düzenlenir.
                await interaction.message.edit(
                    embed=embed,
                    view=self,
                )

        except Exception:
            log.exception("Butonla yenileme başarısız oldu.")


# =========================================================
# DISCORD BOT
# =========================================================
class PriceBot(discord.Client):
    def __init__(self):
        super().__init__(
            intents=discord.Intents.default()
        )

        self.message_id = self._load_message_id()
        self.refresh_lock = asyncio.Lock()
        self.ready_once = False

    def _load_message_id(self):
        if MESSAGE_ID_ENV.isdigit():
            return int(MESSAGE_ID_ENV)

        try:
            with open(MESSAGE_ID_FILE, "r", encoding="utf-8") as file:
                value = file.read().strip()
                return int(value) if value.isdigit() else None
        except (OSError, ValueError):
            return None

    def _save_message_id(self):
        try:
            with open(MESSAGE_ID_FILE, "w", encoding="utf-8") as file:
                file.write(str(self.message_id or ""))
        except OSError:
            log.warning("Mesaj ID dosyası kaydedilemedi.")

    async def on_ready(self):
        log.info("Discord bağlantısı kuruldu: %s", self.user)

        if self.ready_once:
            return

        self.ready_once = True
        self.add_view(RefreshView(self))

        await self.ensure_message()

        if not self.auto_refresh.is_running():
            self.auto_refresh.start()

    async def ensure_message(self):
        if CHANNEL_ID == 0:
            log.error("CHANNEL_ID ortam değişkeni ayarlanmamış.")
            return

        channel = self.get_channel(CHANNEL_ID)

        if channel is None:
            try:
                channel = await self.fetch_channel(CHANNEL_ID)
            except Exception:
                log.exception("Kanal bulunamadı.")
                return

        # Önce kayıtlı mesajı bul; varsa yeni mesaj oluşturma.
        if self.message_id:
            try:
                message = await channel.fetch_message(self.message_id)

                async with self.refresh_lock:
                    prices = await fetch_all()
                    await message.edit(
                        embed=build_embed(prices),
                        view=RefreshView(self),
                    )

                log.info("Mevcut tablo düzenlendi: %s", self.message_id)
                return

            except discord.NotFound:
                log.error(
                    "Kayıtlı tablo bulunamadı. "
                    "Yeni mesaj oluşturmamak için işlem durduruldu. "
                    "MESSAGE_ID değerini kontrol et."
                )
                return

            except discord.Forbidden:
                log.exception("Mesajı düzenleme izni yok.")
                return

            except discord.HTTPException:
                log.exception("Mevcut mesaj düzenlenemedi.")
                return

        # İlk kurulumda MESSAGE_ID verilmediyse bir kez oluştur.
        try:
            prices = await fetch_all()
            message = await channel.send(
                embed=build_embed(prices),
                view=RefreshView(self),
            )

            self.message_id = message.id
            self._save_message_id()

            log.info("İlk tablo oluşturuldu: %s", message.id)
            log.info(
                "Render Environment Variables bölümüne "
                "MESSAGE_ID=%s ekle.", message.id
            )

        except Exception:
            log.exception("İlk tablo oluşturulamadı.")

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

                log.info("Mevcut tablo otomatik güncellendi.")

            except discord.NotFound:
                log.error(
                    "Tablo mesajı silinmiş veya MESSAGE_ID yanlış."
                )

            except discord.Forbidden:
                log.exception("Kanal/mesaj izinleri yetersiz.")

            except Exception:
                log.exception("Otomatik güncelleme başarısız.")

    @auto_refresh.before_loop
    async def before_auto_refresh(self):
        await self.wait_until_ready()


# =========================================================
# BAŞLAT
# =========================================================
def main():
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN Render Environment Variables bölümünde yok."
        )

    if CHANNEL_ID == 0:
        raise RuntimeError(
            "CHANNEL_ID Render Environment Variables bölümünde yok."
        )

    Thread(target=run_flask, daemon=True).start()
    PriceBot().run(TOKEN)


if __name__ == "__main__":
    main()
