import logging

logging.basicConfig(level=logging.INFO)

# ... keep rest of your code unchanged ...

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
            # Only the user who opened the modal can submit
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

        # Schedule the giveaway runner task safely
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
        # Try to notify channel about the failure (best effort)
        try:
            await channel.send(f"⚠️ Giveaway **{prize}** encountered an error and was stopped.")
        except:
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
        super().__init__(timeout=300)  # 5-minute timeout
        self.bot = bot
        self.author_id = author_id

    async def on_timeout(self):
        # Disable all buttons when view times out to avoid stale interactions
        for child in self.children:
            child.disabled = True
        # We do not have an interaction here to update the message but this is best effort
        # If you want, you can store the original message and edit it here

    @discord.ui.button(label="Setup Giveaway", style=discord.ButtonStyle.green)
    async def setup_giveaway_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ You are not allowed to use this button.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(GiveawaySetupModal(self.bot, self.author_id))


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
