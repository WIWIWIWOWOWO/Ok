import os
import asyncio
import random
import time
from keep_alive import keep_alive
keep_alive()
import discord
from discord.ext import commands
from discord import app_commands

# Enable message content intent
intents = discord.Intents.default()
intents.message_content = True

# Create the bot with a prefix for commands
bot = commands.Bot(command_prefix='!', intents=intents)


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

        # Use unique channel name with timestamp to avoid duplicates
        channel_name = f"{user.name}-{user.discriminator}-private-{int(time.time())}"

        # Create a new private channel
        channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            reason="User pressed the button to create a private channel"
        )

        # Create a webhook in the new channel
        webhook = await channel.create_webhook(name=f"{user.name}-webhook")

        # Try DMing the user the webhook URL
        try:
            await user.send(f"✅ Your private channel has been created: {channel.mention}\nWebhook URL: {webhook.url}")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"{user.mention} I couldn't DM you the webhook URL. Here's the channel instead: {channel.mention}",
                ephemeral=True
            )

        # Acknowledge the button click in Discord
        await interaction.response.send_message(
            f"✅ Created your private channel: {channel.mention}",
            ephemeral=True
        )


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

        giveaway_message = await interaction.channel.send(
            f"🎉 **GIVEAWAY STARTED!** 🎉\n\n"
            f"**Prize:** {giveaway_name}\n"
            f"**Winners:** {winners_count}\n"
            f"**Duration:** {duration_minutes} minutes\n\n"
            f"React with 🎉 to enter!"
        )
        await giveaway_message.add_reaction("🎉")

        # Start the giveaway task in background
        self.bot.loop.create_task(
            run_giveaway(
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


async def run_giveaway(channel, message, emoji, duration_minutes, winners_count, prize):
    print(f"[DEBUG] Giveaway started for prize: '{prize}', duration: {duration_minutes} minutes, winners: {winners_count}")
    await asyncio.sleep(duration_minutes * 60)
    print("[DEBUG] Giveaway duration ended, fetching message reactions...")

    message = await channel.fetch_message(message.id)

    for reaction in message.reactions:
        if str(reaction.emoji) == emoji:
            users = await reaction.users().flatten()
            users = [user for user in users if not user.bot]
            print(f"[DEBUG] Number of participants (excluding bots): {len(users)}")
            break
    else:
        users = []
        print("[DEBUG] No reactions found with the giveaway emoji")

    if len(users) < winners_count:
        await channel.send(f"❌ Not enough participants for the giveaway **{prize}**!")
        print("[DEBUG] Giveaway ended: Not enough participants")
        return

    winners = random.sample(users, winners_count)
    winners_mentions = ", ".join(winner.mention for winner in winners)
    print(f"[DEBUG] Winners selected: {winners_mentions}")

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

    @discord.ui.button(label="Setup Giveaway", style=discord.ButtonStyle.green)
    async def setup_giveaway_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ You are not allowed to use this button.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(GiveawaySetupModal(self.bot, self.author_id))


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    """Command for admins to post the ticket button message."""
    view = TicketButtonView(bot)
    await ctx.send("Click the button below to create your private channel:", view=view)


@bot.tree.command(name="giveaway", description="Start an interactive giveaway setup")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway(interaction: discord.Interaction):
    view = GiveawaySetupView(bot, interaction.user.id)
    await interaction.response.send_message(
        f"{interaction.user.mention}, click below to set up your giveaway!",
        view=view,
        ephemeral=True
    )

@bot.command()
async def test_giveaway(ctx):
    """Test if the giveaway message is sent correctly."""
    class DummyUser:
        def __init__(self, name, id):
            self.name = name
            self.id = id
            self.mention = f"<@{id}>"
            self.bot = False

    dummy_users = [
        DummyUser("User1", 123),
        DummyUser("User2", 456)
    ]
    winners_count = 1
    prize = "Test Prize"

    winners = random.sample(dummy_users, winners_count)
    winners_mentions = ", ".join(winner.mention for winner in winners)

    await ctx.send(
        f"🎉 **GIVEAWAY ENDED!** 🎉\n\n"
        f"**Prize:** {prize}\n"
        f"**Winners:** {winners_mentions}\n"
        f"Congratulations! 🎊"
    )

@bot.command()
async def hello(ctx):
    await ctx.send(f'Hello, {ctx.author.name}!')


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}!')
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Error syncing commands: {e}')
    bot.add_view(TicketButtonView(bot))


# Run the bot with your token
TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
