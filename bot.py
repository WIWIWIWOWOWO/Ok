import os
import asyncio
import random
import time
import logging
from keep_alive import keep_alive

import discord
from discord.ext import commands
from discord import app_commands

# Set up logging
logging.basicConfig(level=logging.INFO)

# Keep your bot alive (for hosting services like Replit)
keep_alive()

# Enable intents
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

        channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            reason="User pressed the button to create a private channel"
        )

        webhook = await channel.create_webhook(name=f"{user.name}-webhook")

        try:
            await user.send(f"✅ Your private channel has been created: {channel.mention}\nWebhook URL: {webhook.url}")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"{user.mention} I couldn't DM you the webhook URL. Here's the channel instead: {channel.mention}",
                ephemeral=True
            )

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
        except discord.HTTPException as e:
            logging.error(f"Failed to send giveaway message or add reaction: {e}")
            await interaction.response.send_message(
                "❌ Failed to start giveaway due to Discord API error.",
                ephemeral=True
            )
            return

        # Schedule giveaway task safely
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
    except discord.HTTPException as e:
        logging.error(f"Failed to fetch giveaway message: {e}")
        return

    users = []
    for reaction in message.reactions:
        if str(reaction.emoji) == emoji:
            try:
                users = [user async for user in reaction.users() if not user.bot]
            except discord.HTTPException as e:
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
        # Optional: store message to edit it on timeout (not implemented here)

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
    view = GiveawaySetupView(bot, interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(
        f"{interaction.user.mention}, click below to set up your giveaway!",
        view=view,
        ephemeral=True
    )

# ---------- Admin command to post ticket button ----------

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    """Command for admins to post the ticket button message."""
    view = TicketButtonView(bot)
    await ctx.send("Click the button below to create your private channel:", view=view)

# ---------- Simple hello command with cooldown ----------

@bot.command()
@commands.cooldown(rate=1, per=10, type=commands.BucketType.user)  # 1 use per 10 seconds per user
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
    # Add persistent views for buttons that never timeout
    bot.add_view(TicketButtonView(bot))


# ---------- Run bot ----------

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    logging.error("DISCORD_TOKEN environment variable is not set!")
else:
    bot.run(TOKEN)
