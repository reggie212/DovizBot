import os
import discord
import requests
import asyncio
from datetime import datetime
from flask import Flask
from threading import Thread

# Render web servisi için mini sunucu
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
        # Döviz Kurları
        usd_res = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10).json()
        eur_res = requests.get("https://api.exchangerate-api.com/v4/latest/EUR", timeout=10).json()
        gbp_res = requests.get("https://api.exchangerate-api.com/v4/latest/GBP", timeout=10).json()
        
        usd_try = usd_res["rates"].get("TRY", 0)
        eur_try = eur_res["rates"].get("TRY", 0)
        gbp_try = gbp_res["rates"].get("TRY", 0)
        
        # Kripto Paralar (BTC ve ETH)
        crypto_res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd", timeout=10).json()
        btc_usd = crypto_res["bitcoin"]["usd"]
        ethereum_usd = crypto_res["ethereum"]["usd"]
        
        btc_try = btc_usd * usd_try
        eth_try = ethereum_usd * usd_try
        
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
    
    # Botun Discord durum mesajını ayarlayalım
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

            # Şık bir Embed tasarımı
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

# YENİ ÖZELLİK: Chat'e !fiyat yazıldığında anında cevap verme komutu
@client.event
async def on_message(message):
    # Botun kendi mesajlarına tepki vermesini engelle
    if message.author == client.user:
        return
    
    # Kullanıcı "!fiyat" yazdığında çalışır
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
