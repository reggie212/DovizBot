import os
import discord
import requests
import asyncio
import traceback
from datetime import datetime
from flask import Flask
from threading import Thread

# --- 1. RENDER 7/24 UPTIME SUNUCUSU ---
app = Flask('')

@app.route('/')
def home():
    return "Bot aktif, oto-koruma devrede ve 7/24 calisiyor!"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 2. AYARLAR VE İNTENTLER ---
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
client = discord.Client(intents=intents)

# --- 3. HATA KORUMALI VERİ ÇEKME MOTORU ---
def get_market_rates():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        # Döviz Kurları (ExchangeRate-API)
        usd_res = requests.get("https://api.exchangerate-api.com/v4/latest/USD", headers=headers, timeout=5).json()
        eur_res = requests.get("https://api.exchangerate-api.com/v4/latest/EUR", headers=headers, timeout=5).json()
        gbp_res = requests.get("https://api.exchangerate-api.com/v4/latest/GBP", headers=headers, timeout=5).json()
        
        usd_try = usd_res["rates"].get("TRY", 0)
        eur_try = eur_res["rates"].get("TRY", 0)
        gbp_try = gbp_res["rates"].get("TRY", 0)
        
        # Kripto Paralar (Binance API - Anlık)
        btc_res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", headers=headers, timeout=5).json()
        eth_res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT", headers=headers, timeout=5).json()
        
        btc_usd = float(btc_res["price"])
        eth_usd = float(eth_res["price"])
        
        btc_try = btc_usd * usd_try
        eth_try = eth_usd * usd_try
        
        return {
            "USD": f"{usd_try:,.2f} TL",
            "EUR": f"{eur_try:,.2f} TL",
            "GBP": f"{gbp_try:,.2f} TL",
            "BTC": f"{btc_try:,.2f} TL",
            "ETH": f"{eth_try:,.2f} TL"
        }
    except Exception as e:
        print("[HATA] Veri çekilirken sorun yaşandı:", e)
        return None

# --- 4. OTO-DEVAM EDEN ARKA PLAN GÖREVİ (SELF-HEALING LOOP) ---
async def background_market_updater():
    await client.wait_until_ready()
    try:
        channel = await client.fetch_channel(CHANNEL_ID)
        print(f"[BAŞARILI] Hedef kanal bağlandı: {channel.name}")
    except Exception as e:
        print(f"[KRİTİK HATA] Kanal bulunamadı veya yetki yok: {e}")
        return

    while not client.is_closed():
        try:
            rates = get_market_rates()
            if rates:
                # Eski mesajları temizle
                try:
                    async for message in channel.history(limit=5):
                        if message.author == client.user:
                            await message.delete()
                except Exception as ex:
                    print("[BİLGİ] Eski mesajlar silinirken küçük hata:", ex)

                # Yeni Embed Tablosu
                embed = discord.Embed(
                    title="📊 Canlı Piyasa ve Kripto Monitörü", 
                    description="Güncel döviz kurları ve kripto varlık değerleri (Otomatik Güncellenir):",
                    color=0x00ff88
                )
                embed.add_field(name="🇺🇸 Dolar (USD)", value=rates["USD"], inline=True)
                embed.add_field(name="🇪🇺 Euro (EUR)", value=rates["EUR"], inline=True)
                embed.add_field(name="🇬🇧 Sterlin (GBP)", value=rates["GBP"], inline=True)
                embed.add_field(name="₿ Bitcoin (BTC)", value=rates["BTC"], inline=False)
                embed.add_field(name="Ξ Ethereum (ETH)", value=rates["ETH"], inline=False)
                
                now = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
                embed.set_footer(text=f"Son Güncelleme: {now} | Oto-Koruma Aktif 🚀")
                
                await channel.send(embed=embed)
                print("[BİLGİ] Piyasa tablosu otomatik güncellendi.")
            else:
                print("[UYARI] Veriler boş döndü, bir sonraki döngü bekleniyor...")

        except Exception as loop_error:
            print("[OTO-KURTARMA] Döngüde hata yakalandı, bot çalışmaya devam ediyor:", loop_error)
        
        # 3 dakika (180 saniye) bekle
        await asyncio.sleep(180)

# --- 5. EVENTLER VE KOMUTLAR ---
@client.event
async def on_ready():
    print(f"[BAĞLANDI] {client.user} sistemde aktif!")
    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="!fiyat yazarak öğren 📈"))
    # Arka plan görevini güvenli bir şekilde başlatıyoruz
    client.loop.create_task(background_market_updater())

@client.event
async def on_message(message):
    if message.author == client.user:
        return
    
    if message.content.lower() == "!fiyat":
        try:
            rates = get_market_rates()
            if rates:
                embed = discord.Embed(
                    title="📊 Anlık İstenen Piyasa Verileri", 
                    description="Komut ile anlık sorgulanan kurlar:",
                    color=0x00aaee
                )
                embed.add_field(name="🇺🇸 Dolar (USD)", value=rates["USD"], inline=True)
                embed.add_field(name="🇪🇺 Euro (EUR)", value=rates["EUR"], inline=True)
                embed.add_field(name="🇬🇧 Sterlin (GBP)", value=rates["GBP"], inline=True)
                embed.add_field(name="₿ Bitcoin (BTC)", value=rates["BTC"], inline=False)
                embed.add_field(name="Ξ Ethereum (ETH)", value=rates["ETH"], inline=False)
                
                now = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
                embed.set_footer(text=f"Sorgulanan Zaman: {now} | Komut İsteği 🎯")
                
                await message.channel.send(embed=embed)
            else:
                await message.channel.send("⚠️ Şu an anlık veriler çekilemedi kanka, ağ bağlantısı bekleniyor.")
        except Exception as cmd_err:
            print("[KOMUT HATASI]:", cmd_err)
            await message.channel.send("⚠️ Komut işlenirken beklenmeyen bir hata oluştu.")

# --- 6. BAŞLATICI ---
keep_alive()
client.run(TOKEN)
