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
# MODAL: CARRY REQUEST INPUT
# ---------------------------------------------------------------------------
class CarryRequestModal(discord.ui.Modal, title="Request Carry"):
    game_mode = discord.ui.TextInput(
        label="What game mode?",
        placeholder="e.g. Normal / Hardcore / Insane",
        required=True,
        max_length=50
    )
    
    additional_info = discord.ui.TextInput(
        label="Additional Information",
        style=discord.TextStyle.paragraph,
        placeholder="Provide extra details (e.g. carry type, level, username)...",
        required=False,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if is_blacklisted(interaction.user.id):
            return await interaction.followup.send("❌ You are blacklisted from opening carry tickets.", ephemeral=True)

        count = increment_ticket_counter()
        channel_name = f"carry-{count:04d}"

        # Ensure category exists
        category = discord.utils.get(interaction.guild.categories, name="CARRY TICKETS")
        if not category:
            category = await interaction.guild.create_category("CARRY TICKETS")

        # Channel permissions
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

        # Ticket Content Embed
        ticket_embed = discord.Embed(
            title=f"🎫 Carry Ticket #{count:04d}",
            description=f"Welcome {interaction.user.mention}! Our carry team will be with you shortly.",
            color=discord.Color.blurple()
        )
        ticket_embed.add_field(name="🎮 Game Mode", value=self.game_mode.value, inline=True)
        ticket_embed.add_field(
            name="📝 Additional Info", 
            value=self.additional_info.value if self.additional_info.value.strip() else "None provided.", 
            inline=False
        )

        await channel.send(
            content=interaction.user.mention, 
            embed=ticket_embed, 
            view=TicketControlView()
        )

        await interaction.followup.send(f"✅ Ticket created! Check out {channel.mention}", ephemeral=True)

        # Log Ticket Creation
        log_chan = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_chan:
            log_embed = discord.Embed(title="🎫 New Carry Ticket Opened", color=discord.Color.green())
            log_embed.add_field(name="Ticket Number", value=f"#{count:04d}", inline=True)
            log_embed.add_field(name="User", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            log_embed.add_field(name="Game Mode", value=self.game_mode.value, inline=False)
            log_embed.add_field(
                name="Additional Info", 
                value=self.additional_info.value if self.additional_info.value.strip() else "None", 
                inline=False
            )
            await log_chan.send(embed=log_embed)


# ---------------------------------------------------------------------------
# MODAL: ADD USER TO TICKET
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

            # Log User Addition
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
# TICKET ACTION CONTROLS INSIDE TICKET CHANNELS
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

        # Export HTML direct to disk for Dashboard
        transcript = await chat_exporter.export(interaction.channel)
        if transcript:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(transcript)

        view_url = f"{DASHBOARD_URL}/transcripts/{filename}"
        
        embed = discord.Embed(
            title="🌐 Transcript Saved to Website",
            description=f"The transcript for `{interaction.channel.name}` is now saved to the web dashboard.\n\n🔗 [Click Here to View Transcript]({view_url})",
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
# MAIN CARRY PANEL VIEW (BUTTON ON PUBLIC CHANNEL)
# ---------------------------------------------------------------------------
class CarryPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request Carry", style=discord.ButtonStyle.primary, custom_id="request_carry_btn", emoji="🛒")
    async def request_carry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CarryRequestModal())


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
            title="Request Carry",
            description="Click below to request a carry ticket!",
            color=discord.Color.blue()
        )
        await interaction.channel.send(embed=embed, view=CarryPanelView())
        await interaction.response.send_message("✅ Carry Request panel deployed!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
