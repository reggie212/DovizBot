import os
import discord
import requests
import asyncio

# Token ve Kanal ID'yi artık gizli ayarlardan çekeceğiz:
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
client = discord.Client(intents=intents)

def get_crypto_and_exchange_rates():
    try:
        # Dolar, Euro ve Sterlin kurları (TRY bazlı)
        usd_res = requests.get("https://api.exchangerate-api.com/v4/latest/USD").json()
        eur_res = requests.get("https://api.exchangerate-api.com/v4/latest/EUR").json()
        gbp_res = requests.get("https://api.exchangerate-api.com/v4/latest/GBP").json()
        
        usd_try = usd_res["rates"].get("TRY", 0)
        eur_try = eur_res["rates"].get("TRY", 0)
        gbp_try = gbp_res["rates"].get("TRY", 0)
        
        # Bitcoin fiyatı (CoinGecko API - güncel ve stabil)
        btc_res = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd").json()
        btc_usd = btc_res["bitcoin"]["usd"]
        btc_try = btc_usd * usd_try # Dolar kuru ile TL'ye çeviriyoruz
        
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
    print(f"{client.user} olarak giriş yapıldı!")
    channel = client.get_channel(CHANNEL_ID)
    
    if not channel:
        print(f"Kanal bulunamadı! Kanal ID'sini kontrol et: {CHANNEL_ID}")
        return

    while True:
        rates = get_crypto_and_exchange_rates()
        if rates:
            try:
                # Kanaldaki eski bot mesajlarını temizle
                async for message in channel.history(limit=5):
                    if message.author == client.user:
                        await message.delete()
            except Exception as e:
                print("Eski mesajlar silinirken hata:", e)

            # Şık bir Discord Embed tablosu oluştur
            embed = discord.Embed(title="📊 Canlı Piyasa Verileri", color=0x00ff00)
            embed.add_field(name="🇺🇸 Dolar (USD)", value=rates["USD"], inline=False)
            embed.add_field(name="🇪🇺 Euro (EUR)", value=rates["EUR"], inline=False)
            embed.add_field(name="🇬🇧 Sterlin (GBP)", value=rates["GBP"], inline=False)
            embed.add_field(name="₿ Bitcoin (BTC)", value=rates["BTC"], inline=False)
            embed.set_footer(text="Her 15 dakikada bir otomatik güncellenir.")

            await channel.send(embed=embed)
            print("Piyasa tablosu güncellendi!")
        
        # 15 dakika bekle (15 * 60 saniye = 900 saniye)
        await asyncio.sleep(250)

client.run(TOKEN)
