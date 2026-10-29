import os
import discord
import requests
import asyncio
from flask import Flask
from threading import Thread

# Render'ın web servisi için mini sunucu (Port 10000)
app = Flask('')

@app.route('/')
def home():
    return "Bot aktif ve calisiyor!"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Güvenli ortam değişkenleri
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
client = discord.Client(intents=intents)

def get_crypto_and_exchange_rates():
    try:
        usd_res = requests.get("https://api.exchangerate-api.com/v4/latest/USD").json()
        eur_res = requests.get("https://api.exchangerate-api.com/v4/latest/EUR").json()
        gbp_res = requests.get("https://api.exchangerate-api.com/v4/latest/GBP").json()
        
        usd_try = usd_res["rates"].get("TRY", 0)
        eur_try = eur_res["rates"].get("TRY", 0)
        gbp_try = gbp_res["rates"].get("TRY", 0)
        
        btc_res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd").json()
        btc_usd = btc_res["bitcoin"]["usd"]
        btc_try = btc_usd * usd_try
        
        return {
            "USD": f"{usd_try:,.2f} TL",
            "EUR": f"{eur_try:,.2f} TL",
            "GBP": f"{gbp_try:,.2f} TL",
            "BTC": f"{btc_try:,.2f} TL"
        }
    except Exception as e:
        print("Veri çekilemedi:", e)
        return None

@client.event
async def on_ready():
    print(f"{client.user} olarak giris yapildi!")
    channel = client.get_channel(CHANNEL_ID)
    
    if not channel:
        print(f"Kanal bulunamadi! Kanal ID'sini kontrol et: {CHANNEL_ID}")
        return

    while True:
        rates = get_crypto_and_exchange_rates()
        if rates:
            try:
                async for message in channel.history(limit=5):
                    if message.author == client.user:
                        await message.delete()
            except Exception as e:
                print("Eski mesajlar silinirken hata:", e)

            embed = discord.Embed(title="📊 Canlı Piyasa Verileri", color=0x00ff00)
            embed.add_field(name="🇺🇸 Dolar (USD)", value=rates["USD"], inline=False)
            embed.add_field(name="🇪🇺 Euro (EUR)", value=rates["EUR"], inline=False)
            embed.add_field(name="🇬🇧 Sterlin (GBP)", value=rates["GBP"], inline=False)
            embed.add_field(name="₿ Bitcoin (BTC)", value=rates["BTC"], inline=False)
            await channel.send(embed=embed)
            print("Piyasa tablosu güncellendi!")
        
        await asyncio.sleep(900)

# Web sunucusunu başlat ve botu çalıştır
keep_alive()
client.run(TOKEN)
