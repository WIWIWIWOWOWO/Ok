import os
import asyncio
import time
import random
import logging
from keep_alive import keep_alive

import discord
from discord.ext import commands
from discord import app_commands, HTTPException, InteractionResponded

# ---------- Logging Setup ----------
logging.basicConfig(level=logging.INFO)  # Change to DEBUG for more logs

# ---------- Keep Alive ----------
keep_alive()

# ---------- Intents ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# ---------- Retry Helper (optional) ----------
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

        await interaction.response.send_message(
            f"✅ Created your private channel: {channel.mention}",
            ephemeral=True
        )


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    """Admin command to post the ticket button."""
    view = TicketButtonView(bot)
    await ctx.send("Click the button below to create your private channel:", view=view)


# ---------- Hello Command ----------
@bot.command()
@commands.cooldown(rate=1, per=10, type=commands.BucketType.user)
async def hello(ctx):
    await ctx.send(f'Hello, {ctx.author.name}!')

@hello.error
async def hello_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Please wait {round(error.retry_after, 1)} seconds before using this command again.")


# ---------- Giveaway Slash Command ----------
class Giveaway:
    def __init__(self, bot, channel, message, prize, winners_count):
        self.bot = bot
        self.channel = channel
        self.message = message
        self.prize = prize
        self.winners_count = winners_count

    async def run(self, duration_minutes):
        await asyncio.sleep(duration_minutes * 60)
        try:
            message = await self.channel.fetch_message(self.message.id)
        except discord.NotFound:
            logging.warning("Giveaway message deleted, cancelling giveaway.")
            return
        except HTTPException as e:
            logging.error(f"Error fetching giveaway message: {e}")
            return

        reaction = None
        for react in message.reactions:
            if str(react.emoji) == "🎉":
                reaction = react
                break

        if reaction is None:
            await self.channel.send(f"❌ No 🎉 reaction found. Giveaway **{self.prize}** canceled.")
            return

        try:
            users = [user async for user in reaction.users() if not user.bot]
        except Exception as e:
            logging.error(f"Error fetching users from reaction: {e}")
            users = []

        if len(users) == 0:
            await self.channel.send(f"❌ No valid participants for giveaway **{self.prize}**!")
            return

        winners = random.sample(users, min(self.winners_count, len(users)))
        winners_mentions = ", ".join(winner.mention for winner in winners)

        await self.channel.send(
            f"🎉 **GIVEAWAY ENDED!** 🎉\n\n"
            f"**Prize:** {self.prize}\n"
            f"**Winners:** {winners_mentions}\n"
            f"Congratulations! 🎊"
        )


@bot.tree.command(name="giveaway", description="Start a giveaway (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(prize="Giveaway prize name", duration="Duration in minutes", winners="Number of winners")
async def giveaway(interaction: discord.Interaction, prize: str, duration: int, winners: int):
    await interaction.response.defer(ephemeral=True)
    if duration <= 0 or winners <= 0:
        await interaction.followup.send("❌ Duration and winners must be positive integers.", ephemeral=True)
        return

    try:
        giveaway_msg = await interaction.channel.send(
            f"🎉 **GIVEAWAY STARTED!** 🎉\n\n"
            f"**Prize:** {prize}\n"
            f"**Duration:** {duration} minutes\n"
            f"**Number of winners:** {winners}\n\n"
            f"React with 🎉 to enter!"
        )
        await giveaway_msg.add_reaction("🎉")
    except HTTPException as e:
        logging.error(f"Failed to send giveaway message: {e}")
        await interaction.followup.send("❌ Failed to start giveaway due to an error.", ephemeral=True)
        return

    giveaway_instance = Giveaway(bot, interaction.channel, giveaway_msg, prize, winners)
    bot.loop.create_task(giveaway_instance.run(duration))

    await interaction.followup.send(f"✅ Giveaway started for **{prize}**!", ephemeral=True)


# ---------- Vouch System ----------
vouch_counts = {}       # user_id -> int
vouches_given = {}      # giver_id -> set of user_ids they've vouched

def add_vouch(given_to_id: int, given_by_id: int) -> bool:
    if given_by_id not in vouches_given:
        vouches_given[given_by_id] = set()
    if given_to_id in vouches_given[given_by_id]:
        return False
    vouches_given[given_by_id].add(given_to_id)
    vouch_counts[given_to_id] = vouch_counts.get(given_to_id, 0) + 1
    return True

@bot.command(name="vouch")
async def vouch(ctx: commands.Context):
    """Start a DM to vouch someone."""
    try:
        dm = await ctx.author.create_dm()
        await dm.send("Please reply with the username#discriminator or global name of the person you want to vouch for.")

        def check(m):
            return m.author == ctx.author and isinstance(m.channel, discord.DMChannel)

        msg = await bot.wait_for('message', check=check, timeout=120)
        target_name = msg.content.strip()

        if not ctx.guild:
            await dm.send("❌ This command can only be used in a server.")
            return

        target_member = None
        if "#" in target_name:
            # Old style with discriminator
            name, discriminator = target_name.split("#", 1)
            for member in ctx.guild.members:
                if (member.name == name and member.discriminator == discriminator):
                    target_member = member
                    break
        else:
            # New style
            target_member = discord.utils.find(
                lambda m: (m.global_name and m.global_name.lower() == target_name.lower()) or
                          m.name.lower() == target_name.lower(),
                ctx.guild.members
            )

        if not target_member:
            await dm.send("❌ Could not find that user in this server. Please make sure you typed it correctly.")
            return

        if target_member.id == ctx.author.id:
            await dm.send("❌ You cannot vouch for yourself.")
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


# ---------- Events ----------
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}!')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Error syncing commands: {e}')
    bot.add_view(TicketButtonView(bot))


@bot.tree.error
async def on_app_command_error(interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
    elif isinstance(error, app_commands.errors.CommandInvokeError):
        original = error.original
        if isinstance(original, HTTPException) and original.status == 429:
            try:
                await interaction.response.send_message("❌ Rate limited by Discord, try again later.", ephemeral=True)
            except InteractionResponded:
                await interaction.followup.send("❌ Rate limited by Discord, try again later.", ephemeral=True)
        else:
            logging.error(f"Unhandled error: {original}")
    else:
        logging.error(f"App command error: {error}")


# ---------- Run ----------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    logging.error("DISCORD_TOKEN environment variable is not set!")
else:
    bot.run(TOKEN)
