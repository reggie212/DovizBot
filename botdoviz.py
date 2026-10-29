import os
import re
import json
import asyncio
import logging
from datetime import datetime
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
TZ = ZoneInfo("Europe/Istanbul")
REFRESH_SECONDS = 60
MESSAGE_ID_FILE = "message_id.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bigpara")

# =========================================================
# FLASK (keep-alive)
# =========================================================
app = Flask(__name__)

@app.route("/")
def home():
    return "OK"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

# =========================================================
# VERİ KAYNAKLARI
# =========================================================

ASSETS = {
    "💵 USD/TRY":  "https://bigpara.hurriyet.com.tr/doviz/amerikan-dolari/",
    "💶 EUR/TRY":  "https://bigpara.hurriyet.com.tr/doviz/euro/",
    "💷 GBP/TRY":  "https://bigpara.hurriyet.com.tr/doviz/ingiliz-sterlini/",
    "🪙 Gram Altın": "https://bigpara.hurriyet.com.tr/altin/gram-altin/",
    "🪙 Çeyrek Altın": "https://bigpara.hurriyet.com.tr/altin/ceyrek-altin/",
    "🪙 Yarım Altın":  "https://bigpara.hurriyet.com.tr/altin/yarim-altin/",
    "🪙 Tam Altın":    "https://bigpara.hurriyet.com.tr/altin/tam-altin/",
    "📈 BIST 100":  "https://bigpara.hurriyet.com.tr/borsa/endeks/bist-100/",
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Accept-Language": "tr-TR,tr;q=0.9",
}

def _extract_price(html: str):
    """Bigpara HTML yapısına özel, çok daha isabetli fiyat çekici."""
    soup = BeautifulSoup(html, "html.parser")

    # 1. Meta Etiketleri (En güvenilir yöntem)
    # Bigpara genelde <meta itemprop="price" content="34.50"> kullanır
    meta_price = soup.find("meta", itemprop="price")
    if meta_price and meta_price.get("content"):
        val = meta_price["content"]
        # Eğer nokta varsa Türkçe formata çevir (34.50 -> 34,50)
        return val.replace(".", ",")

    # 2. JSON-LD Script (Bazı sayfalarda fiyat JSON içinde gelir)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict):
                if "offers" in data and "price" in data["offers"]:
                    return str(data["offers"]["price"]).replace(".", ",")
                if "price" in data:
                    return str(data["price"]).replace(".", ",")
        except Exception:
            pass

    # 3. CSS Seçicileri (Bigpara'nın kullandığı özel sınıflar)
    # w-doviz, w-altin, value, last-price gibi sınıflar
    selectors = [
        ".value", ".price", ".last-price", ".last-value",
        ".w-doviz", ".w-altin", ".bigpara-value", 
        "span[data-value]", ".market-value", ".table-value"
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            # Sadece sayıları, noktayı ve virgülü al
            num = re.search(r"[\d\.,]+", text)
            if num:
                return num.group(0)

    # 4. Fallback: Sayfadaki Türkçe formatlı en belirgin sayıyı bul
    matches = re.findall(r"\b\d{1,3}(?:\.\d{3})*,\d+\b", html)
    if matches:
        return matches[0]

    return None

async def fetch_price(session, name, url):
    try:
        async with session.get(url, headers=HEADERS, timeout=15) as r:
            if r.status != 200:
                return name, None
            html = await r.text()
        return name, _extract_price(html)
    except Exception as e:
        log.warning(f"{name} alınamadı: {e}")
        return name, None

async def fetch_all():
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(fetch_price(session, n, u) for n, u in ASSETS.items())
        )
    return dict(results)

# =========================================================
# EMBED
# =========================================================

def build_embed(prices: dict, updated_at: datetime):
    embed = discord.Embed(
        title="💹 Anlık Piyasa Fiyatları",
        description="Aşağıdaki tablo **60 saniyede bir** otomatik güncellenir.",
        color=discord.Color.blurple(),
    )
    for name, price in prices.items():
        # SADECE anlık fiyat (artış/düşüş veya alış/satış YOK)
        value = f"**{price}**" if price else "`Veri Bekleniyor...`"
        embed.add_field(name=name, value=value, inline=True)

    embed.set_footer(
        text=f"Son güncelleme: {updated_at.strftime('%d.%m.%Y %H:%M:%S')} • {TZ.key}"
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
        custom_id="refresh_prices_btn",
    )
    async def refresh_btn(self, interaction: discord.Interaction, _button: discord.ui.Button):
        # Kullanıcıya hiçbir mesaj (ephemeral veya normal) atılmaz.
        await interaction.response.defer()
        
        prices = await fetch_all()
        embed = build_embed(prices, datetime.now(TZ))
        
        try:
            # Sadece embed'i düzenle
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
        """Her N saniyede bir tabloyu yeniler — EK MESAJ ATMAZ."""
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
