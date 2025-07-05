import discord
from discord.ext import commands

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

        # Create a new private channel
        channel = await guild.create_text_channel(
            name=f"{user.name}-private",
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


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    """Command for admins to post the ticket button message."""
    view = TicketButtonView(bot)
    await ctx.send("Click the button below to create your private channel:", view=view)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}!')
    bot.add_view(TicketButtonView(bot))


# Command: !hello
@bot.command()
async def hello(ctx):
    await ctx.send(f'Hello, {ctx.author.name}!')


# Run the bot with your token
bot.run('MTM5MDk4MDg0OTYzMzA3MTE3NQ.GtRNEf.QOnLOEkyk2E05lgLvIiAlfENn0rTvmbQ6fZ0aI')