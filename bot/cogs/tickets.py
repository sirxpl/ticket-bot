class CarrySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Normal Carry", value="normal", description="Request a Normal Mode Carry", emoji="🟢"),
            discord.SelectOption(label="Hardcore Carry", value="hardcore", description="Request a Hardcore Mode Carry", emoji="🔴"),
            discord.SelectOption(label="Insane Carry", value="insane", description="Request an Insane Mode Carry", emoji="🟣")
        ]
        super().__init__(placeholder="🛒 Request Carry", min_values=1, max_values=1, options=options, custom_id="carry_select")

    async def callback(self, interaction: discord.Interaction):
        # Acknowledge the interaction immediately so Discord doesn't time out
        await interaction.response.defer(ephemeral=True)

        if is_blacklisted(interaction.user.id):
            return await interaction.followup.send("❌ You are blacklisted from opening carry tickets.", ephemeral=True)

        carry_type = self.values[0]
        count = increment_ticket_counter()
        channel_name = f"{carry_type}-carry-{count:04d}"

        # Category Setup
        category = discord.utils.get(interaction.guild.categories, name="CARRY TICKETS")
        if not category:
            category = await interaction.guild.create_category("CARRY TICKETS")

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites
        )

        ticket_embed = discord.Embed(
            title=f"🎫 {carry_type.capitalize()} Carry Request #{count:04d}",
            description=f"Welcome {interaction.user.mention}!\n\nOur carry team will be with you shortly. Please state details or wait for a booster to claim.",
            color=discord.Color.brand_green()
        )
        await channel.send(content=interaction.user.mention, embed=ticket_embed, view=TicketControlView())

        # Send confirmation using followup (since we deferred earlier)
        await interaction.followup.send(f"✅ Created your ticket: {channel.mention}", ephemeral=True)

        # Log Ticket Creation
        log_chan = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_chan:
            log_embed = discord.Embed(title="🎫 Carry Ticket Opened", color=discord.Color.green())
            log_embed.add_field(name="User", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Type", value=carry_type.capitalize(), inline=True)
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            await log_chan.send(embed=log_embed)
