import os
import discord
import requests
import asyncio
from datetime import datetime
from flask import Flask
from threading import Thread

# Render web servisi için mini sunucu (7/24 aktif tutmak için)
app = Flask('')

@app.route('/')
def home():
    return "Bot aktif ve 7/24 calisiyor!"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

# Tam yetkili intentler
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
client = discord.Client(intents=intents)

def get_market_rates():
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        
        # Döviz Kurları (Frankfurter API - Kesintisiz ve güncel)
        usd_res = requests.get("https://api.frankfurter.app/latest?from=USD&to=TRY", headers=headers, timeout=10).json()
        eur_res = requests.get("https://api.frankfurter.app/latest?from=EUR&to=TRY", headers=headers, timeout=10).json()
        gbp_res = requests.get("https://api.frankfurter.app/latest?from=GBP&to=TRY", headers=headers, timeout=10).json()
        
        usd_try = usd_res["rates"]["TRY"]
        eur_try = eur_res["rates"]["TRY"]
        gbp_try = gbp_res["rates"]["TRY"]
        
        # Kripto Paralar (Binance API - Saniyelik anlık veriler)
        btc_res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", headers=headers, timeout=10).json()
        eth_res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT", headers=headers, timeout=10).json()
        
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
        print("Veri çekilirken hata oluştu:", e)
        return None

@client.event
async def on_ready():
    print(f"{client.user} olarak giriş yapıldı ve bot aktif!")
    
    # Botun Discord durum mesajı
    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="!fiyat yazarak öğren 📈"))
    
    try:
        channel = await client.fetch_channel(CHANNEL_ID)
        print(f"Hedef kanal bulundu: {channel.name}")
    except Exception as e:
        print(f"Kanal bulunamadı! ID'yi kontrol et: {e}")
        return

    while True:
        rates = get_market_rates()
        if rates:
            try:
                # Önceki bot mesajlarını temizle ki kanal kirlenmesin
                async for message in channel.history(limit=5):
                    if message.author == client.user:
                        await message.delete()
            except Exception as e:
                print("Eski mesajlar temizlenirken hata:", e)

            # Şık Embed tasarımı
            embed = discord.Embed(
                title="📊 Canlı Piyasa ve Kripto Monitörü", 
                description="Güncel döviz kurları ve kripto varlık değerleri (Her 3 dakikada bir güncellenir):",
                color=0x00ff88
            )
            embed.add_field(name="🇺🇸 Dolar (USD)", value=rates["USD"], inline=True)
            embed.add_field(name="🇪🇺 Euro (EUR)", value=rates["EUR"], inline=True)
            embed.add_field(name="🇬🇧 Sterlin (GBP)", value=rates["GBP"], inline=True)
            embed.add_field(name="₿ Bitcoin (BTC)", value=rates["BTC"], inline=False)
            embed.add_field(name="Ξ Ethereum (ETH)", value=rates["ETH"], inline=False)
            
            now = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
            embed.set_footer(text=f"Son Güncelleme: {now} | 7/24 Aktif 🚀")
            
            await channel.send(embed=embed)
            print("Piyasa tablosu başarıyla güncellendi ve gönderildi!")
        
        # 180 saniye (3 dakika) bekle
        await asyncio.sleep(180)

# Chat'e !fiyat yazıldığında anında cevap verme komutu
@client.event
async def on_message(message):
    if message.author == client.user:
        return
    
    if message.content.lower() == "!fiyat":
        rates = get_market_rates()
        if rates:
            embed = discord.Embed(
                title="📊 Anlık İstenen Piyasa Verileri", 
                description="Anlık sorgulanan güncel kurlar:",
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
            await message.channel.send("Şu an anlık veriler çekilemedi kanka, birazdan tekrar dene.")

keep_alive()
client.run(TOKEN)
