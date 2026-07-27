import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import chat_exporter
from utils.storage import increment_ticket_counter, is_blacklisted, TRANSCRIPTS_DIR

LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
DASHBOARD_URL = os.getenv("OAUTH2_REDIRECT_URI", "https://ticket-bot-f184.onrender.com").replace("/callback", "")

# ---------------------------------------------------------------------------
# MODALS
# ---------------------------------------------------------------------------
class AddUserModal(discord.ui.Modal, title="Add User to Ticket"):
    user_id_input = discord.ui.TextInput(
        label="User ID",
        placeholder="Enter Discord User ID to add...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_id = int(self.user_id_input.value.strip())
            member = interaction.guild.get_member(target_id) or await interaction.guild.fetch_member(target_id)
            
            await interaction.channel.set_permissions(member, read_messages=True, send_messages=True)
            await interaction.response.send_message(f"✅ Added {member.mention} to this ticket.", ephemeral=True)

            # Log to Log Channel
            log_chan = interaction.guild.get_channel(LOG_CHANNEL_ID)
            if log_chan:
                embed = discord.Embed(title="👤 User Added to Ticket", color=discord.Color.blue())
                embed.add_field(name="Ticket", value=interaction.channel.mention, inline=True)
                embed.add_field(name="Added By", value=interaction.user.mention, inline=True)
                embed.add_field(name="User Added", value=member.mention, inline=True)
                await log_chan.send(embed=embed)
        except Exception:
            await interaction.response.send_message("❌ Invalid User ID or member not found.", ephemeral=True)


# ---------------------------------------------------------------------------
# TICKET CONTROL BUTTONS INSIDE TICKET CHANNELS
# ---------------------------------------------------------------------------
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket_close", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        confirm_view = discord.ui.View()
        
        async def confirm_callback(inter: discord.Interaction):
            await inter.response.send_message("🔒 Closing ticket in 5 seconds...")
            await asyncio.sleep(5)
            await inter.channel.delete()

        confirm_btn = discord.ui.Button(label="Confirm Close", style=discord.ButtonStyle.danger)
        confirm_btn.callback = confirm_callback
        confirm_view.add_item(confirm_btn)

        await interaction.response.send_message("Are you sure you want to close this ticket?", view=confirm_view, ephemeral=True)

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.secondary, custom_id="ticket_add_user", emoji="➕")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddUserModal())

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim", emoji="🙋‍♂️")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        
        embed = discord.Embed(
            description=f"🙋‍♂️ Ticket claimed by {interaction.user.mention}.",
            color=discord.Color.green()
        )
        await interaction.channel.send(embed=embed)

        # Log Claim
        log_chan = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_chan:
            log_embed = discord.Embed(title="📌 Ticket Claimed", color=discord.Color.green())
            log_embed.add_field(name="Ticket", value=interaction.channel.mention, inline=True)
            log_embed.add_field(name="Claimed By", value=interaction.user.mention, inline=True)
            await log_chan.send(embed=log_embed)

    @discord.ui.button(label="Website Transcript", style=discord.ButtonStyle.primary, custom_id="ticket_transcript", emoji="📜")
    async def export_transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        filename = f"{interaction.channel.name}.html"
        filepath = os.path.join(TRANSCRIPTS_DIR, filename)

        # Export HTML direct to disk for Flask Dashboard
        transcript = await chat_exporter.export(interaction.channel)
        if transcript:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(transcript)

        view_url = f"{DASHBOARD_URL}/transcripts/{filename}"
        
        embed = discord.Embed(
            title="🌐 Transcript Saved to Website",
            description=f"The transcript for `{interaction.channel.name}` is now accessible on the web dashboard.\n\n🔗 [Click Here to View Transcript]({view_url})",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        # Log Transcript URL
        log_chan = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_chan:
            log_embed = discord.Embed(title="📜 Transcript Generated", color=discord.Color.blue())
            log_embed.add_field(name="Ticket", value=interaction.channel.name, inline=True)
            log_embed.add_field(name="Generated By", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Web Link", value=f"[View Transcript]({view_url})", inline=False)
            await log_chan.send(embed=log_embed)

    @discord.ui.button(label="Force Close", style=discord.ButtonStyle.danger, custom_id="ticket_force_close", emoji="⛔")
    async def force_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⛔ Force closing and deleting channel...")
        await asyncio.sleep(2)
        await interaction.channel.delete()


# ---------------------------------------------------------------------------
# CARRY PANEL DROPDOWN & VIEW
# ---------------------------------------------------------------------------
class CarrySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Normal Carry", value="normal", description="Request a Normal Mode Carry", emoji="🟢"),
            discord.SelectOption(label="Hardcore Carry", value="hardcore", description="Request a Hardcore Mode Carry", emoji="🔴"),
            discord.SelectOption(label="Insane Carry", value="insane", description="Request an Insane Mode Carry", emoji="🟣")
        ]
        super().__init__(placeholder="🛒 Request Carry", min_values=1, max_values=1, options=options, custom_id="carry_select")

    async def callback(self, interaction: discord.Interaction):
        # Acknowledge the interaction immediately to prevent timeouts
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

        # Response via followup after deferring
        await interaction.followup.send(f"✅ Created your ticket: {channel.mention}", ephemeral=True)

        # Log Ticket Creation
        log_chan = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_chan:
            log_embed = discord.Embed(title="🎫 Carry Ticket Opened", color=discord.Color.green())
            log_embed.add_field(name="User", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Type", value=carry_type.capitalize(), inline=True)
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            await log_chan.send(embed=log_embed)


class CarryPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CarrySelect())


# ---------------------------------------------------------------------------
# COG COMMAND SETUP
# ---------------------------------------------------------------------------
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(CarryPanelView())
        self.bot.add_view(TicketControlView())

    @app_commands.command(name="setup_carry", description="Deploy the Carry Request Panel")
    @app_commands.default_permissions(administrator=True)
    async def setup_carry(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Carry Request System",
            description="Select the carry service you require from the dropdown menu below to open a ticket.",
            color=discord.Color.blurple()
        )
        await interaction.channel.send(embed=embed, view=CarryPanelView())
        await interaction.response.send_message("✅ Carry Panel deployed!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
