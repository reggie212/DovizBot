import os
import discord
import requests
import asyncio
from datetime import datetime
from flask import Flask
from threading import Thread

# Render web servisi için 7/24 mini sunucu
app = Flask('')

@app.route('/')
def home():
    return "Bot otomatik piyasa takibinde ve 7/24 calisiyor!"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
client = discord.Client(intents=intents)

def get_fiat_rates():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        # En sağlam ve engelsiz döviz servisi
        res = requests.get("https://open.er-api.com/v6/latest/USD", headers=headers, timeout=5).json()
        rates = res["rates"]
        
        usd_try = rates.get("TRY", 0)
        eur_to_usd = rates.get("EUR", 1)
        gbp_to_usd = rates.get("GBP", 1)
        chf_to_usd = rates.get("CHF", 1) # İsviçre Frangı
        
        eur_try = usd_try / eur_to_usd
        gbp_try = usd_try / gbp_to_usd
        chf_try = usd_try / chf_to_usd
        
        return {
            "USD": f"{usd_try:,.2f} TL",
            "EUR": f"{eur_try:,.2f} TL",
            "GBP": f"{gbp_try:,.2f} TL",
            "CHF": f"{chf_try:,.2f} TL"
        }
    except Exception as e:
        print("[HATA] Döviz verileri çekilemedi:", e)
        return None

async def background_market_updater():
    await client.wait_until_ready()
    try:
        channel = await client.fetch_channel(CHANNEL_ID)
        print(f"[BAŞARILI] Hedef kanal bağlandı: {channel.name}")
    except Exception as e:
        print("[KRİTİK] Kanal bulunamadı:", e)
        return

    while not client.is_closed():
        rates = get_fiat_rates()
        if rates:
            try:
                # Eski bot mesajlarını temizle ki alt alta yığılmasın
                async for message in channel.history(limit=5):
                    if message.author == client.user:
                        await message.delete()
            except Exception:
                pass

            embed = discord.Embed(
                title="📊 Canlı Döviz ve Piyasa Monitörü", 
                description="Güncel döviz kurları (Her 3 dakikada bir **otomatik** güncellenir):",
                color=0x00ff88
            )
            embed.add_field(name="🇺🇸 Dolar (USD)", value=rates["USD"], inline=True)
            embed.add_field(name="🇪🇺 Euro (EUR)", value=rates["EUR"], inline=True)
            embed.add_field(name="🇬🇧 Sterlin (GBP)", value=rates["GBP"], inline=True)
            embed.add_field(name="🇨🇭 İsviçre Frangı (CHF)", value=rates["CHF"], inline=True)
            
            now = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
            embed.set_footer(text=f"Son Güncelleme: {now} | Otomatik Mod 🚀")
            
            await channel.send(embed=embed)
            print("[BİLGİ] Tablo otomatik olarak güncellendi ve gönderildi.")
        else:
            print("[UYARI] Veriler alınamadı, bir sonraki döngü bekleniyor...")
        
        # 3 dakikada bir otomatik yeniler
        await asyncio.sleep(180)

@client.event
async def on_ready():
    print(f"[BAĞLANDI] {client.user} aktif!")
    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Piyasalar Otomatik Takipte 📈"))
    client.loop.create_task(background_market_updater())

keep_alive()
client.run(TOKEN)
