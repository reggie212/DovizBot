import os
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Thread

import discord
from flask import Flask
from discord.ext import tasks

# Canlı veri için borsapy kütüphanesi
try:
    from borsapy import doviz, altin, borsa, kripto
except ImportError:
    raise SystemExit("Lütfen önce 'pip install borsapy' komutunu çalıştırın.")

# =========================================================
# AYARLAR
# =========================================================
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
TZ = ZoneInfo("Europe/Istanbul")
REFRESH_SECONDS = 60
MESSAGE_ID_FILE = "message_id.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("canlidoviz")

# =========================================================
# FLASK (Render / UptimeRobot için keep-alive)
# =========================================================
app = Flask(__name__)

@app.route("/")
def home():
    return "OK"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

# =========================================================
# VERİ ÇEKME FONKSİYONLARI (Kapsamlı)
# =========================================================
def get_single_price(asset_type, symbol):
    """borsapy kütüphanesini kullanarak anlık fiyatı çeker."""
    try:
        if asset_type == "doviz":
            # Döviz kurları (USD, EUR, GBP vb.)
            data = doviz.get_current(symbol)
            return f"{data['Satış']:.4f}".replace(".", ",")
        elif asset_type == "altin":
            # Altın fiyatları (gram-altin, ceyrek-altin vb.)
            data = altin.get_current(symbol)
            return f"{data['Satış']:,.2f}".replace(".", ",").replace(",", ".")
        elif asset_type == "borsa":
            # Borsa İstanbul (XU100, THYAO vb.)
            data = borsa.get_current(symbol)
            return f"{data['Son']:,.2f}".replace(".", ",").replace(",", ".")
        elif asset_type == "kripto":
            # Kripto Paralar (BTC, ETH vb.)
            data = kripto.get_current(symbol)
            return f"{data['Satış']:,.2f}".replace(".", ",").replace(",", ".")
    except Exception as e:
        log.warning(f"{symbol} verisi çekilemedi: {e}")
        return None
    return None

async def fetch_all():
    """Tüm varlıkları asenkron olarak toplar (borsapy senkron olduğu için to_thread kullanılır)."""
    
    # Kapsamlı Varlık Listesi
    ASSET_MAP = {
        "💵 USD/TRY": ("doviz", "USD"),
        "💶 EUR/TRY": ("doviz", "EUR"),
        "💷 GBP/TRY": ("doviz", "GBP"),
        "💴 JPY/TRY": ("doviz", "JPY"),
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

    async def task_wrapper(name, atype, sym):
        price = await asyncio.to_thread(get_single_price, atype, sym)
        return name, price

    results = await asyncio.gather(
        *(task_wrapper(name, atype, sym) for name, (atype, sym) in ASSET_MAP.items())
    )
    return dict(results)

# =========================================================
# EMBED (TABLO) OLUŞTURMA
# =========================================================
def build_embed(prices: dict, updated_at: datetime):
    embed = discord.Embed(
        title="💹 Canlı Piyasa Fiyatları",
        description="Aşağıdaki tablo **60 saniyede bir** otomatik güncellenir.",
        color=discord.Color.green(),
    )
    
    for name, price in prices.items():
        # Sadece anlık fiyat gösterilir, artış/düşüş YOK.
        value = f"**{price}**" if price else "`Veri Bekleniyor...`"
        embed.add_field(name=name, value=value, inline=True)

    embed.set_footer(
        text=f"Son güncelleme: {updated_at.strftime('%d.%m.%Y %H:%M:%S')} • {TZ.key} | Kaynak: canlidoviz.com"
    )
    return embed

# =========================================================
# GÜNCELLE BUTONU (Mesaj Atmaz, Sadece Tabloyu Günceller)
# =========================================================
class RefreshView(discord.ui.View):
    def __init__(self, bot: "PriceBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="🔄 Güncelle",
        style=discord.ButtonStyle.primary,
        custom_id="refresh_canlidoviz_btn",
    )
    async def refresh_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        # Discord'a "tıklandı" bilgisi gider ama kullanıcıya hiçbir görünür mesaj (ephemeral vb.) atılmaz.
        await interaction.response.defer()
        
        prices = await fetch_all()
        embed = build_embed(prices, datetime.now(TZ))
        
        try:
            # Sadece ana mesajın embed'ini düzenle
            await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            log.warning(f"Embed düzenlenemedi: {e}")

# =========================================================
# BOT
# =========================================================
class PriceBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.message_id = None
        self._load_message_id()

    def _load_message_id(self):
        try:
            if os.path.exists(MESSAGE_ID_FILE):
                with open(MESSAGE_ID_FILE) as f:
                    v = f.read().strip()
                    self.message_id = int(v) if v else None
        except Exception:
            self.message_id = None

    def _save_message_id(self):
        try:
            with open(MESSAGE_ID_FILE, "w") as f:
                f.write(str(self.message_id or ""))
        except Exception:
            pass

    async def on_ready(self):
        log.info(f"Giriş yapıldı: {self.user}")
        self.add_view(RefreshView(self))  # Butonun bot yeniden başlasa bile çalışmasını sağlar
        await self.ensure_message()
        self.auto_refresh.start()

    async def ensure_message(self):
        channel = self.get_channel(CHANNEL_ID)
        if not channel:
            log.error("CHANNEL_ID geçersiz / kanal bulunamadı!")
            return

        prices = await fetch_all()
        embed = build_embed(prices, datetime.now(TZ))
        view = RefreshView(self)

        if self.message_id:
            try:
                msg = await channel.fetch_message(self.message_id)
                await msg.edit(embed=embed, view=view)
                log.info(f"Mevcut mesaj düzenlendi (id={self.message_id})")
                return
            except Exception:
                log.info("Eski mesaj bulunamadı, yenisi oluşturuluyor…")

        # Eğer kayıtlı mesaj yoksa ilk defa gönderir
        msg = await channel.send(embed=embed, view=view)
        self.message_id = msg.id
        self._save_message_id()
        log.info(f"Yeni mesaj gönderildi (id={self.message_id})")

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def auto_refresh(self):
        """Her 60 saniyede bir tabloyu yeniler — EK MESAJ ATMAZ."""
        try:
            channel = self.get_channel(CHANNEL_ID)
            if not channel or not self.message_id:
                return
            msg = await channel.fetch_message(self.message_id)
            prices = await fetch_all()
            embed = build_embed(prices, datetime.now(TZ))
            await msg.edit(embed=embed, view=RefreshView(self))
            log.info("Otomatik güncelleme yapıldı.")
        except Exception as e:
            log.warning(f"Otomatik güncelleme hatası: {e}")

    @auto_refresh.before_loop
    async def _wait(self):
        await self.wait_until_ready()

# =========================================================
# ÇALIŞTIR
# =========================================================
def main():
    if not TOKEN:
        log.error("DISCORD_TOKEN ortam değişkeni yok!")
        return
    Thread(target=run_flask, daemon=True).start()
    PriceBot().run(TOKEN)

if __name__ == "__main__":
    main()
