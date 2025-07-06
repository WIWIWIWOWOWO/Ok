import os
import asyncio
import time
import logging
from keep_alive import keep_alive

import discord
from discord.ext import commands
from discord import app_commands, HTTPException, InteractionResponded

# Retry helper for API calls on rate limits
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

# Setup logging
logging.basicConfig(level=logging.INFO)  # Change to DEBUG for verbose logs

# Keep bot alive (for Replit or similar)
keep_alive()

# Enable intents (need message_content for commands)
intents = discord.Intents.default()
intents.message_content = True

# Create bot instance
bot = commands.Bot(command_prefix='!', intents=intents)

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

        await interaction.response.send_message(
            f"✅ Created your private channel: {channel.mention}",
            ephemeral=True
        )

# ---------- Admin command to post ticket button ----------

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    """Admin command to post the ticket button."""
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

# ---------- Bot events ----------

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}!')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Error syncing commands: {e}')
    bot.add_view(TicketButtonView(bot))

# ---------- Run bot ----------

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    logging.error("DISCORD_TOKEN environment variable is not set!")
else:
    bot.run(TOKEN)
