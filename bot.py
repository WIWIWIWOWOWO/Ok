import os
import asyncio
import random
import time
import logging
import json
from keep_alive import keep_alive

import discord
from discord.ext import commands, tasks
from discord import app_commands, HTTPException

# Set up logging
logging.basicConfig(level=logging.DEBUG)  # Change to INFO in production

# Keep your bot alive (for hosting services like Replit)
keep_alive()

# Enable intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# Helper for Discord API calls with retry on rate limit
async def discord_api_call_with_retry(coro_func, *args, max_retries=5, **kwargs):
    retry_delay = 1
    for attempt in range(max_retries):
        try:
            return await coro_func(*args, **kwargs)
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = e.response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else retry_delay
                logging.warning(f"Rate limited, retrying after {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                retry_delay *= 2
            else:
                raise
    raise Exception("Max retries exceeded due to rate limits")


# ---------- Ticket Button ----------

class TicketButtonView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Create Private Channel", style=discord.ButtonStyle.green, custom_id="create_ticket")
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }

        channel_name = f"{user.name}-{user.discriminator}-private-{int(time.time())}"

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                reason="User pressed the button to create a private channel"
            )
        except HTTPException as e:
            if e.status == 429:
                retry_after = e.response.headers.get("Retry-After")
                logging.warning(f"Rate limited creating channel. Retry after {retry_after} seconds.")
                await interaction.response.send_message(
                    "⚠️ I'm being rate limited by Discord. Please try again in a few seconds.",
                    ephemeral=True
                )
                return
            else:
                logging.error(f"Error creating channel: {e}")
                await interaction.response.send_message(
                    "❌ Failed to create channel due to an error.",
                    ephemeral=True
                )
                return

        # Create webhook in the channel
        webhook = None
        try:
            webhook = await channel.create_webhook(name=f"{user.name}-webhook")
        except HTTPException as e:
            logging.error(f"Error creating webhook: {e}")

        # Try to DM the user the webhook URL
        try:
            if webhook:
                await user.send(
                    f"✅ Your private channel has been created: {channel.mention}\nWebhook URL: {webhook.url}"
                )
            else:
                await user.send(
                    f"✅ Your private channel has been created: {channel.mention}"
                )
        except discord.Forbidden:
            # Fallback if user blocks DMs
            if webhook:
                await interaction.response.send_message(
                    f"{user.mention} I couldn't DM you. Here's your private channel: {channel.mention}\nWebhook URL: {webhook.url}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"{user.mention} I couldn't DM you. Here's your private channel: {channel.mention}",
                    ephemeral=True
                )
            return

        await interaction.response.send_message(
            f"✅ Created your private channel: {channel.mention}",
            ephemeral=True
        )


# ---------- Giveaway System ----------

GIVEAWAY_FILE = "giveaways.json"
if not os.path.exists(GIVEAWAY_FILE):
    with open(GIVEAWAY_FILE, "w") as f:
        json.dump({}, f)

def load_giveaways():
    with open(GIVEAWAY_FILE) as f:
        return json.load(f)

def save_giveaways(data):
    with open(GIVEAWAY_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def end_giveaway(guild_id, giveaway_id, giveaway_data):
    channel = bot.get_channel(giveaway_data["channel_id"])
    if not channel:
        logging.warning("Channel not found for ending giveaway")
        return

    try:
        message = await channel.fetch_message(giveaway_data["message_id"])
    except discord.NotFound:
        await channel.send(f"⚠️ Giveaway for **{giveaway_data['prize']}** ended but original message is gone.")
        return

    users = list(set(giveaway_data.get("entries", [])))
    if len(users) < giveaway_data["winners_count"]:
        await channel.send(f"❌ Giveaway for **{giveaway_data['prize']}** ended. Not enough participants.")
    else:
        winners = random.sample(users, giveaway_data["winners_count"])
        mentions = " ".join(f"<@{uid}>" for uid in winners)
        await channel.send(
            f"🎉 **GIVEAWAY ENDED!** 🎉\n\nPrize: **{giveaway_data['prize']}**\nWinners: {mentions}\nCongratulations!"
        )

    giveaways = load_giveaways()
    giveaways[guild_id][giveaway_id]["ended"] = True
    save_giveaways(giveaways)

@tasks.loop(seconds=30)
async def giveaway_checker():
    giveaways = load_giveaways()
    changed = False
    now = time.time()
    for guild_id, guild_giveaways in giveaways.items():
        for giveaway_id, giveaway in guild_giveaways.items():
            if not giveaway.get("ended") and giveaway["ends_at"] <= now:
                await end_giveaway(guild_id, giveaway_id, giveaway)
                changed = True
    if changed:
        save_giveaways(giveaways)

# Start the giveaway checker task when bot is ready
@giveaway_checker.before_loop
async def before_giveaway_checker():
    await bot.wait_until_ready()

giveaway_checker.start()


@bot.tree.command(name="giveaway_start", description="Start a giveaway")
@app_commands.describe(
    prize="Prize for the giveaway",
    duration_minutes="Duration in minutes",
    winners_count="Number of winners"
)
@app_commands.checks.has_permissions(manage_messages=True)
async def giveaway_start(interaction: discord.Interaction, prize: str, duration_minutes: int, winners_count: int):
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel

    ends_at = time.time() + duration_minutes * 60

    giveaways = load_giveaways()
    guild_id = str(interaction.guild.id)
    if guild_id not in giveaways:
        giveaways[guild_id] = {}

    giveaway_id = str(int(time.time()))
    entry_message = await channel.send(
        f"🎉 **GIVEAWAY STARTED!** 🎉\n\n"
        f"**Prize:** {prize}\n"
        f"**Ends in:** {duration_minutes} minutes\n"
        f"**Number of winners:** {winners_count}\n\n"
        f"React with 🎉 to enter!"
    )
    await entry_message.add_reaction("🎉")

    giveaways[guild_id][giveaway_id] = {
        "channel_id": channel.id,
        "message_id": entry_message.id,
        "prize": prize,
        "ends_at": ends_at,
        "winners_count": winners_count,
        "entries": [],
        "ended": False
    }
    save_giveaways(giveaways)

    await interaction.followup.send(f"✅ Giveaway started in {channel.mention}!", ephemeral=True)

@bot.tree.command(name="giveaway_cancel", description="Cancel a giveaway by ID")
@app_commands.describe(giveaway_id="ID of the giveaway to cancel")
@app_commands.checks.has_permissions(manage_messages=True)
async def giveaway_cancel(interaction: discord.Interaction, giveaway_id: str):
    giveaways = load_giveaways()
    guild_id = str(interaction.guild.id)
    if guild_id not in giveaways or giveaway_id not in giveaways[guild_id]:
        await interaction.response.send_message("❌ Giveaway ID not found.", ephemeral=True)
        return

    giveaways[guild_id][giveaway_id]["ended"] = True
    save_giveaways(giveaways)
    await interaction.response.send_message("✅ Giveaway cancelled.", ephemeral=True)

@bot.tree.command(name="giveaway_reroll", description="Reroll winners for a giveaway by ID")
@app_commands.describe(giveaway_id="ID of the giveaway to reroll")
@app_commands.checks.has_permissions(manage_messages=True)
async def giveaway_reroll(interaction: discord.Interaction, giveaway_id: str):
    giveaways = load_giveaways()
    guild_id = str(interaction.guild.id)
    if guild_id not in giveaways or giveaway_id not in giveaways[guild_id]:
        await interaction.response.send_message("❌ Giveaway ID not found.", ephemeral=True)
        return

    giveaway = giveaways[guild_id][giveaway_id]
    if not giveaway.get("entries"):
        await interaction.response.send_message("❌ No participants to reroll.", ephemeral=True)
        return

    if giveaway["ended"]:
        users = list(set(giveaway["entries"]))
        if len(users) < giveaway["winners_count"]:
            await interaction.response.send_message("❌ Not enough participants to reroll.", ephemeral=True)
            return

        winners = random.sample(users, giveaway["winners_count"])
        mentions = " ".join(f"<@{uid}>" for uid in winners)
        channel = bot.get_channel(giveaway["channel_id"])
        await channel.send(
            f"🎉 **GIVEAWAY REROLLED!** 🎉\n\nPrize: **{giveaway['prize']}**\nWinners: {mentions}\nCongratulations!"
        )
        await interaction.response.send_message("✅ Rerolled winners!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Giveaway is still running. Wait until it ends to reroll.", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != "🎉":
        return

    giveaways = load_giveaways()
    guild_id = str(payload.guild_id)
    if guild_id not in giveaways:
        return
    for giveaway_id, giveaway in giveaways[guild_id].items():
        if giveaway.get("ended"):
            continue
        if payload.message_id == giveaway["message_id"]:
            if payload.user_id not in giveaway["entries"]:
                giveaway["entries"].append(payload.user_id)
                save_giveaways(giveaways)
            break


# ---------- Admin command to post ticket button ----------

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    """Command for admins to post the ticket button message."""
    view = TicketButtonView(bot)
    await ctx.send("Click the button below to create your private channel:", view=view)


# ---------- Simple hello command with cooldown ----------

@bot.command()
@commands.cooldown(rate=1, per=10, type=commands.BucketType.user)
async def hello(ctx):
    await ctx.send(f'Hello, {ctx.author.name}!')

@hello.error
async def hello_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Please wait {round(error.retry_after, 1)} seconds before using this command again.")


# ---------- Vouch System ----------

# Global in-memory vouch storage
vouch_counts = {}  # user_id: int
vouches_given = {}  # giver_id: set of vouched user_ids

def add_vouch(given_to_id: int, given_by_id: int) -> bool:
    """Return True if vouch added, False if giver already vouched this user."""
    if given_by_id not in vouches_given:
        vouches_given[given_by_id] = set()
    if given_to_id in vouches_given[given_by_id]:
        return False  # already vouched this user
    vouches_given[given_by_id].add(given_to_id)
    vouch_counts[given_to_id] = vouch_counts.get(given_to_id, 0) + 1
    return True

@bot.command(name="vouch")
async def vouch(ctx: commands.Context):
    """Initiate a DM to vouch someone."""
    try:
        dm = await ctx.author.create_dm()
        await dm.send(
            "Please reply with the username, mention, or user ID of the person you want to vouch for.\n"
            "Examples:\n"
            "- Username#1234\n"
            "- Username\n"
            "- @Mention\n"
            "- UserID"
        )
        
        def check(m):
            return m.author == ctx.author and isinstance(m.channel, discord.DMChannel)
        
        msg = await bot.wait_for('message', check=check, timeout=120)
        target_input = msg.content.strip()

        # Ensure we are in a guild context
        if not ctx.guild:
            await dm.send("❌ This command can only be used in a server.")
            return

        user = None

        # Try mention format <@123456789012345678> or <@!123456789012345678>
        import re
        mention_match = re.match(r"<@!?(\d+)>", target_input)
        if mention_match:
            user_id = int(mention_match.group(1))
            user = ctx.guild.get_member(user_id)
        else:
            # Try ID
            if target_input.isdigit():
                user = ctx.guild.get_member(int(target_input))
            # Try username#discriminator
            elif "#" in target_input:
                name, disc = target_input.split("#", 1)
                user = discord.utils.get(ctx.guild.members, name=name, discriminator=disc)
            else:
                # Try username only (first match)
                user = discord.utils.find(lambda m: m.name == target_input, ctx.guild.members)

        if not user:
            await dm.send("❌ Could not find that user in this server. Vouch cancelled.")
            return
        if user.id == ctx.author.id:
            await dm.send("❌ You cannot vouch for yourself.")
            return

        added = add_vouch(user.id, ctx.author.id)
        if added:
            await dm.send(f"✅ You have successfully vouched for {user.mention}!")
        else:
            await dm.send(f"⚠️ You have already vouched for {user.mention} before.")

    except asyncio.TimeoutError:
        await ctx.author.send("⌛ Vouch timed out. Please run the command again if you still want to vouch.")

@bot.command()
async def vouches(ctx, member: discord.Member = None):
    """Check how many vouches a user has."""
    target = member or ctx.author
    count = vouch_counts.get(target.id, 0)
    await ctx.send(f"{target.mention} has {count} vouches.")




# ---------- Event to show when the bot is ready ----------
@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logging.info(f"✅ Synced {len(synced)} slash commands.")
    except Exception as e:
        logging.error(f"❌ Failed to sync commands: {e}")
    logging.info("Bot is ready.")


# ---------- Actually run the bot ----------
if __name__ == "__main__":
    keep_alive()

    TOKEN = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("TOKEN")
    if not TOKEN:
        logging.error("❌ No bot token found in environment variables! Please set DISCORD_BOT_TOKEN or TOKEN.")
    else:
        try:
            bot.run(TOKEN)
        except Exception as e:
            logging.error(f"❌ Exception when running bot: {e}")
