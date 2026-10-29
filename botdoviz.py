
import os
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Thread

import discord
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
# BORSA.PY
# =========================================================
try:
    from borsapy import doviz, altin, borsa, kripto
    BORSAPY_AVAILABLE = True
except ImportError:
    doviz = altin = borsa = kripto = None
    BORSAPY_AVAILABLE = False
    log.exception("borsapy import edilemedi. requirements.txt dosyasını kontrol et.")


# =========================================================
# VARLIK LİSTESİ
# =========================================================
ASSET_MAP = {
    "💵 USD/TRY": ("doviz", "USD"),
    "💶 EUR/TRY": ("doviz", "EUR"),
    "💷 GBP/TRY": ("doviz", "GBP"),
    "💴 JPY/TRY": ("doviz", "JPY"),
    "🇨🇭 CHF/TRY": ("doviz", "CHF"),
    "🇨🇦 CAD/TRY": ("doviz", "CAD"),
    "🇦🇺 AUD/TRY": ("doviz", "AUD"),
    "🪙 Gram Altın": ("altin", "gram-altin"),
    "🪙 Çeyrek Altın": ("altin", "ceyrek-altin"),
    "🪙 Yarım Altın": ("altin", "yarim-altin"),
    "🪙 Tam Altın": ("altin", "tam-altin"),
    "🪙 ONS Altın": ("altin", "ons-altin"),
    "📈 BIST 100": ("borsa", "XU100"),
    "✈️ THYAO": ("borsa", "THYAO"),
    "🏦 GARAN": ("borsa", "GARAN"),
    "₿ BTC/USDT": ("kripto", "BTCUSDT"),
    "Ξ ETH/USDT": ("kripto", "ETHUSDT"),
}

# =========================================================
# FLASK / RENDER
# =========================================================
app = Flask(__name__)


@app.route("/")
def home():
    return "Doviz bot is running."


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
def get_field(data, *keys):
    """Sözlük veya pandas benzeri sonuçtan alan okumayı dener."""
    for key in keys:
        try:
            value = data[key]
            if value is not None:
                return value
        except (KeyError, TypeError, IndexError, AttributeError):
            pass

        try:
            value = getattr(data, key)
            if value is not None:
                return value
        except (AttributeError, TypeError):
            pass

    return None


def to_number(value):
    """Fiyat değerini güvenli biçimde float'a çevirir."""
    if value is None:
        return None

    try:
        # Pandas/NumPy skalerleri için de çalışır.
        number = float(value)
        if number > 0 and number != float("inf"):
            return number
    except (TypeError, ValueError, OverflowError):
        pass

    return None


def format_price(value, asset_type):
    """Fiyatı Türkçe sayı biçiminde gösterir."""
    if asset_type == "doviz":
        decimals = 4
    elif asset_type == "borsa":
        decimals = 2
    else:
        decimals = 2

    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def get_single_price(asset_type, symbol):
    """borsapy üzerinden tek varlığın son/satış fiyatını alır."""
    if not BORSAPY_AVAILABLE:
        return None

    try:
        if asset_type == "doviz":
            data = doviz.get_current(symbol)
            raw = get_field(data, "Satış", "Satis", "satış", "sell", "last")

        elif asset_type == "altin":
            data = altin.get_current(symbol)
            raw = get_field(data, "Satış", "Satis", "satış", "sell", "last")

        elif asset_type == "borsa":
            data = borsa.get_current(symbol)
            raw = get_field(data, "Son", "son", "Close", "close", "last")

        elif asset_type == "kripto":
            data = kripto.get_current(symbol)
            raw = get_field(data, "Satış", "Satis", "satış", "sell", "last")

        else:
            return None

        number = to_number(raw)

        if number is None:
            log.warning("%s için geçerli fiyat alanı bulunamadı.", symbol)
            return None

        return format_price(number, asset_type)

    except Exception:
        log.exception("%s fiyatı alınamadı.", symbol)
        return None


async def fetch_all():
    """Senkron fiyat sorgularını event loop dışında çalıştırır."""
    results = await asyncio.gather(
        *[
            asyncio.to_thread(get_single_price, asset_type, symbol)
            for asset_type, symbol in ASSET_MAP.values()
        ],
        return_exceptions=True,
    )

    prices = {}

    for name, result in zip(ASSET_MAP.keys(), results):
        if isinstance(result, Exception):
            log.error("%s fiyat sorgusu başarısız: %s", name, result)
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
            "Anlık fiyat tablosu\n"
            "Otomatik güncelleme: **60 saniye**"
        ),
        color=discord.Color.blue(),
        timestamp=updated_at,
    )

    for name in ASSET_MAP:
        price = prices.get(name)
        value = f"**{price}**" if price else "`Veri alınamadı`"

        embed.add_field(
            name=name,
            value=value,
            inline=True,
        )

    embed.set_footer(
        text="Son güncelleme: "
        + updated_at.strftime("%d.%m.%Y %H:%M:%S")
        + " • Europe/Istanbul"
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
        custom_id="refresh_canlidoviz_btn_v2",
    )
    async def refresh_btn(self, interaction, button):
        # Kullanıcıya yeni mesaj göndermez.
        await interaction.response.defer()

        try:
            async with self.bot.refresh_lock:
                prices = await fetch_all()
                embed = build_embed(prices)

                await interaction.message.edit(
                    embed=embed,
                    view=self,
                )

        except Exception:
            log.exception("Butonla güncelleme başarısız oldu.")
            # Hata durumunda da kanala mesaj atılmaz.


# =========================================================
# BOT
# =========================================================
class PriceBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
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
                log.exception("Discord kanalı bulunamadı.")
                return

        try:
            prices = await fetch_all()
            embed = build_embed(prices)
        except Exception:
            log.exception("İlk fiyat verileri alınamadı.")
            embed = build_embed({name: None for name in ASSET_MAP})

        view = RefreshView(self)

        if self.message_id:
            try:
                message = await channel.fetch_message(self.message_id)
                await message.edit(embed=embed, view=view)
                log.info("Mevcut tablo düzenlendi: %s", self.message_id)
                return
            except discord.NotFound:
                log.warning("Kayıtlı mesaj silinmiş; yeni mesaj oluşturulacak.")
            except discord.Forbidden:
                log.exception("Mesajı düzenleme izni yok.")
                return
            except discord.HTTPException:
                log.exception("Kayıtlı mesaj düzenlenemedi.")
                return

        try:
            message = await channel.send(embed=embed, view=view)
            self.message_id = message.id
            self._save_message_id()
            log.info("İlk tablo mesajı oluşturuldu: %s", message.id)
        except Exception:
            log.exception("İlk tablo mesajı gönderilemedi.")

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
                embed = build_embed(prices)

                await message.edit(
                    embed=embed,
                    view=RefreshView(self),
                )

                log.info("Mevcut tablo otomatik güncellendi.")

            except discord.NotFound:
                log.error(
                    "Tablo mesajı bulunamadı. MESSAGE_ID değerini kontrol et."
                )
            except discord.Forbidden:
                log.exception("Kanal veya mesaj için yetki eksik.")
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

    if not BORSAPY_AVAILABLE:
        raise RuntimeError(
            "borsapy yüklenemedi. requirements.txt dosyasına borsapy ekle."
        )

    Thread(target=run_flask, daemon=True).start()
    PriceBot().run(TOKEN)


if __name__ == "__main__":
    main()
