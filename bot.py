import os
import asyncio
import random
import time
import logging
import json
from keep_alive import keep_alive

import discord
from discord.ext import commands
from discord import app_commands, HTTPException, InteractionResponded

# Set up logging
logging.basicConfig(level=logging.DEBUG)  # Change to INFO in production

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

# Keep your bot alive (for hosting services like Replit)
keep_alive()

# Enable intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Needed to fetch members

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


# ---------- Giveaway Modal & View ----------

class GiveawaySetupModal(discord.ui.Modal, title="Setup Giveaway"):
    def __init__(self, bot, author_id):
        super().__init__()
        self.bot = bot
        self.author_id = author_id

        self.add_item(discord.ui.TextInput(
            label="Giveaway Name",
            placeholder="Example: Nitro Giveaway",
            max_length=100
        ))
        self.add_item(discord.ui.TextInput(
            label="Number of Winners",
            placeholder="1",
            max_length=2
        ))
        self.add_item(discord.ui.TextInput(
            label="Duration (minutes)",
            placeholder="e.g., 10",
            max_length=4
        ))

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You are not allowed to submit this modal.", ephemeral=True)
            return

        giveaway_name = self.children[0].value
        winners_count = self.children[1].value
        duration_minutes = self.children[2].value

        try:
            winners_count = int(winners_count)
            duration_minutes = int(duration_minutes)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid number for winners or duration. Please use integers.",
                ephemeral=True
            )
            return

        try:
            giveaway_message = await interaction.channel.send(
                f"🎉 **GIVEAWAY STARTED!** 🎉\n\n"
                f"**Prize:** {giveaway_name}\n"
                f"**Winners:** {winners_count}\n"
                f"**Duration:** {duration_minutes} minutes\n\n"
                f"React with 🎉 to enter!"
            )
            await giveaway_message.add_reaction("🎉")
        except HTTPException as e:
            if e.status == 429:
                logging.warning(f"Rate limited when starting giveaway: {e}")
                await interaction.response.send_message(
                    "❌ I am being rate limited by Discord. Please try again shortly.",
                    ephemeral=True
                )
                return
            logging.error(f"Failed to send giveaway message or add reaction: {e}")
            await interaction.response.send_message(
                "❌ Failed to start giveaway due to Discord API error.",
                ephemeral=True
            )
            return

        self.bot.loop.create_task(
            safe_run_giveaway(
                self.bot,
                interaction.channel,
                giveaway_message,
                "🎉",
                duration_minutes,
                winners_count,
                giveaway_name
            )
        )

        await interaction.response.send_message(
            f"✅ Giveaway started in {interaction.channel.mention}!",
            ephemeral=True
        )


async def safe_run_giveaway(bot, channel, message, emoji, duration_minutes, winners_count, prize):
    try:
        await run_giveaway(channel, message, emoji, duration_minutes, winners_count, prize)
    except Exception as e:
        logging.error(f"Exception in giveaway task: {e}")
        try:
            await channel.send(f"⚠️ Giveaway **{prize}** encountered an error and was stopped.")
        except Exception:
            pass


async def run_giveaway(channel, message, emoji, duration_minutes, winners_count, prize):
    await asyncio.sleep(duration_minutes * 60)

    try:
        message = await channel.fetch_message(message.id)
    except discord.NotFound:
        logging.warning("Giveaway message was deleted before ending giveaway.")
        return
    except HTTPException as e:
        if e.status == 429:
            logging.warning(f"Rate limited when fetching giveaway message: {e}")
            await asyncio.sleep(5)
            try:
                message = await channel.fetch_message(message.id)
            except Exception as e:
                logging.error(f"Failed retrying to fetch message: {e}")
                return
        else:
            logging.error(f"Failed to fetch giveaway message: {e}")
            return

    users = []
    for reaction in message.reactions:
        if str(reaction.emoji) == emoji:
            try:
                users = [user async for user in reaction.users() if not user.bot]
            except HTTPException as e:
                logging.error(f"Failed to fetch reaction users: {e}")
                users = []
            break

    if len(users) < winners_count:
        await channel.send(f"❌ Not enough participants for the giveaway **{prize}**!")
        return

    winners = random.sample(users, winners_count)
    winners_mentions = ", ".join(winner.mention for winner in winners)

    await channel.send(
        f"🎉 **GIVEAWAY ENDED!** 🎉\n\n"
        f"**Prize:** {prize}\n"
        f"**Winners:** {winners_mentions}\n"
        f"Congratulations! 🎊"
    )


class GiveawaySetupView(discord.ui.View):
    def __init__(self, bot, author_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.author_id = author_id

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Setup Giveaway", style=discord.ButtonStyle.green)
    async def setup_giveaway_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ You are not allowed to use this button.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(GiveawaySetupModal(self.bot, self.author_id))


# ---------- Slash command for giveaway ----------

@bot.tree.command(name="giveaway", description="Start an interactive giveaway setup")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway(interaction: discord.Interaction):
    try:
        view = GiveawaySetupView(bot, interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            f"{interaction.user.mention}, click below to set up your giveaway!",
            view=view,
            ephemeral=True
        )
    except HTTPException as e:
        if e.status == 429:
            logging.warning(f"Rate-limited on /giveaway command: {e}")
            try:
                await interaction.response.send_message(
                    "❌ I got rate-limited by Discord! Please try again shortly.",
                    ephemeral=True
                )
            except InteractionResponded:
                await interaction.followup.send(
                    "❌ I got rate-limited by Discord! Please try again shortly.",
                    ephemeral=True
                )
        else:
            logging.error(f"HTTPException in /giveaway command: {e}")
            raise


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
        await dm.send("Please reply with the username#discriminator of the person you want to vouch for.")
        
        def check(m):
            return m.author == ctx.author and isinstance(m.channel, discord.DMChannel)
        
        msg = await bot.wait_for('message', check=check, timeout=120)
        target_name = msg.content.strip()

        # Ensure we are in a guild context
        if not ctx.guild:
            await dm.send("❌ This command can only be used in a server.")
            return

        if "#" not in target_name:
            await dm.send("❌ Please provide the username in the format username#1234.")
            return

        name, discriminator = target_name.split("#", 1)

        # Search member in guild
        target_member = discord.utils.get(ctx.guild.members, name=name, discriminator=discriminator)
        
        if not target_member:
            await dm.send("❌ Could not find that user in this server. Please make sure you typed it correctly.")
            return
        
        if target_member.id == ctx.author.id:
            await dm.send("❌ You cannot vouch yourself.")
            return

        if add_vouch(target_member.id, ctx.author.id):
            await dm.send(f"✅ You have successfully vouched for {target_member.display_name}.")
            await ctx.channel.send(f"📢 {ctx.author.mention} has vouched for {target_member.mention}!")
        else:
            await dm.send("❌ You have already vouched for this user.")
    except asyncio.TimeoutError:
        await ctx.author.send("⌛ Vouch timed out. Please try again.")
    except discord.Forbidden:
        await ctx.send(f"{ctx.author.mention}, I couldn't DM you. Please enable your DMs and try again.")


# ---------- Run Bot ----------

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    logging.error("No bot token found in environment variable DISCORD_BOT_TOKEN!")
else:
    bot.run(TOKEN)
bot.run(os.environ["TOKEN"])
