import os
import discord
from discord import app_commands
from discord.ext import tasks, commands
import aiohttp
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# 2. --- CONFIGURATION ---
TARGET_GAME_ID = 4238077359                     # Your hardcoded Roblox Game/Place ID
REQUIRED_ROLE_ID = 1517189563473203453          # Your hardcoded Discord Staff Role ID
SAVE_FILE = "bounties.txt"                      # File name where targets are permanently stored

# Global state tracking
KOS_LIST = {}
alert_channel = None        # Channel where @everyone alerts go
radar_message = None        # Stores the live auto-updating message object
universe_id = None          # Resolved automatically by the bot

# --- HELPER FUNCTIONS FOR STORAGE ---
def load_bounties():
    """Loads targets from the text file into memory on startup."""
    global KOS_LIST
    if os.path.exists(SAVE_FILE):
        try:
            with open(SAVE_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and ":" in line:
                        rid_str, name = line.split(":", 1)
                        KOS_LIST[int(rid_str)] = name
            print(f"📁 Loaded {len(KOS_LIST)} targets from persistent storage.")
        except Exception as e:
            print(f"Error loading save file: {e}")

def save_bounties():
    """Writes the current target list into the text file."""
    try:
        with open(SAVE_FILE, "w") as f:
            for rid, name in KOS_LIST.items():
                f.write(f"{rid}:{name}\n")
    except Exception as e:
        print(f"Error saving data to file: {e}")

# 3. --- BOT SETUP ---
class TrackerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = TrackerBot()

@bot.event
async def on_ready():
    global universe_id
    print(f"🤖 Bot is online as {bot.user.name}")
    
    # Load saved targets right when the bot turns on
    load_bounties()
    
    # Pre-resolve Game ID to ensure background lookups work flawlessly
    async with aiohttp.ClientSession() as session:
        try:
            url = f"https://apis.roblox.com/universes/v1/places/{TARGET_GAME_ID}/universe"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    universe_id = data.get("universeId")
                    print(f"✅ Game ID Verified. Universe ID: {universe_id}")
                else:
                    print(f"⚠️ Warning: Could not resolve Universe ID for Place {TARGET_GAME_ID}")
        except Exception as e:
            print(f"Error initializing game validation: {e}")

    # Start the automated worker cycles
    background_tracking_matrix.start()

def has_bounty_role(interaction: discord.Interaction) -> bool:
    role = interaction.guild.get_role(REQUIRED_ROLE_ID)
    return role in interaction.user.roles

# 4. --- SLASH COMMANDS ---

@bot.tree.command(name="bounty-add", description="Log a new target bounty ticket.")
@app_commands.describe(roblox_id="The numeric Roblox User ID", player_name="The target's Roblox Username")
async def bounty_add(interaction: discord.Interaction, roblox_id: int, player_name: str):
    global alert_channel, radar_message
    if not has_bounty_role(interaction):
        await interaction.response.send_message("❌ You do not have permission to manage bounty tickets.", ephemeral=True)
        return

    alert_channel = interaction.channel
    KOS_LIST[roblox_id] = player_name
    
    # Save target immediately to disk
    save_bounties()
    
    radar_message = None

    embed = discord.Embed(
        title="📝 NEW BOUNTY TICKET LOGGED",
        description=f"Added **{player_name}** to the matrix. Progress permanently saved.",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="bounty-remove", description="Close an active bounty ticket.")
@app_commands.describe(roblox_id="The numeric Roblox User ID to remove")
async def bounty_remove(interaction: discord.Interaction, roblox_id: int):
    global radar_message
    if not has_bounty_role(interaction):
        await interaction.response.send_message("❌ You do not have permission to manage bounty tickets.", ephemeral=True)
        return

    if roblox_id in KOS_LIST:
        removed_name = KOS_LIST.pop(roblox_id)
        
        # Update the permanent storage file
        save_bounties()
        
        radar_message = None  
        await interaction.response.send_message(f"✅ **Ticket Closed:** Removed `{removed_name}` ({roblox_id}) from database.")
    else:
        await interaction.response.send_message(f"❌ No active ticket found for Roblox ID: `{roblox_id}`", ephemeral=True)

# 5. --- AUTOMATED TRACKING & AUTO-REFRESH RADAR LOOP ---
@tasks.loop(seconds=30)
async def background_tracking_matrix():
    global alert_channel, radar_message
    
    # Loop functions seamlessly on startup if targets exist, using default channel if command wasn't re-run
    if not KOS_LIST:
        return  

    # Try to find a channel context if it wasn't dynamically set yet
    if not alert_channel:
        for guild in bot.guilds:
            # Tries to find any text channel to update status on until a command binds it
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    alert_channel = channel
                    break
            if alert_channel:
                break
                
    if not alert_channel:
        return

    status_map = {0: "Offline 🔴", 1: "On Website 🌐", 2: "In Game 🟢", 3: "In Studio 🛠️"}
    
    async with aiohttp.ClientSession() as session:
        try:
            presence_url = "https://presence.roblox.com/v1/presence/users"
            async with session.post(presence_url, json={"userIds": list(KOS_LIST.keys())}) as resp:
                presences = {}
                if resp.status == 200:
                    p_data = await resp.json()
                    for u in p_data.get('userPresences', []):
                        presences[u.get('userId')] = u

            thumb_url = f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={','.join(map(str, KOS_LIST.keys()))}&size=150x150&format=Png&isCircular=false"
            async with session.get(thumb_url) as resp:
                thumbs = {}
                if resp.status == 200:
                    t_data = await resp.json()
                    for t in t_data.get('data', []):
                        thumbs[t.get('targetId')] = t.get('imageUrl')

            embed = discord.Embed(
                title="📡 LIVE AUTOMATED BOUNTY RADAR", 
                description="This board automatically updates itself every 30 seconds.",
                color=discord.Color.purple()
            )

            for rid, name in KOS_LIST.items():
                p_info = presences.get(rid, {})
                status_code = p_info.get('userPresenceType', 0)
                current_status = status_map.get(status_code, "Unknown")
                
                location_info = ""
                if status_code == 2:
                    current_place = p_info.get('placeId')
                    current_universe = p_info.get('universeId')
                    game_id = p_info.get('gameId')

                    if current_place == TARGET_GAME_ID or (universe_id and current_universe == universe_id):
                        location_info = "\n🚨 **⚠️ TARGET MATCHED IN GAME!**"
                        
                        alert_embed = discord.Embed(
                            title="🚨 TARGET LOCATED IN-GAME 🚨",
                            description=f"**{name}** is actively in your tracked server instance!",
                            color=discord.Color.red()
                        )
                        alert_embed.add_field(name="Join Link", value=f"[Click to Launch Roblox](roblox://placeId={TARGET_GAME_ID}&gameInstanceId={game_id})", inline=False)
                        await alert_channel.send(content="@everyone 🎯 Active Target Spotted!", embed=alert_embed)
                    else:
                        location_info = f"\n🎮 Playing a different game (ID: {current_place})"

                value_text = (
                    f"**ID:** `{rid}`\n"
                    f"**Status:** {current_status}{location_info}\n"
                    f"**Profile:** [Roblox Link](https://www.roblox.com/users/{rid}/profile)"
                )
                embed.add_field(name=f"👤 {name}", value=value_text, inline=False)
                
                if rid in thumbs:
                    embed.set_thumbnail(url=thumbs[rid])

            if radar_message is None:
                radar_message = await alert_channel.send(embed=embed)
            else:
                try:
                    await radar_message.edit(embed=embed)
                except discord.NotFound:
                    radar_message = await alert_channel.send(embed=embed)

        except Exception as e:
            print(f"Automated background processing error: {e}")

# 6. --- RUN BOT ---
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ Setup incomplete: No DISCORD_TOKEN found inside your .env file.")
