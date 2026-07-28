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
# CARRY REQUEST MODAL
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

    def __init__(self, category_id: int = None, support_role_id: int = None, log_channel_id: int = None):
        super().__init__()
        self.category_id = category_id
        self.support_role_id = support_role_id
        self.log_channel_id = log_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if is_blacklisted(interaction.user.id):
            return await interaction.followup.send("❌ You are blacklisted from opening carry tickets.", ephemeral=True)

        count = increment_ticket_counter()
        channel_name = f"carry-{count:04d}"

        category = None
        if self.category_id:
            category = interaction.guild.get_channel(self.category_id)
        if not category:
            category = discord.utils.get(interaction.guild.categories, name="CARRY TICKETS")
            if not category:
                category = await interaction.guild.create_category("CARRY TICKETS")

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        if self.support_role_id:
            role = interaction.guild.get_role(self.support_role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites
        )

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

        ping_content = interaction.user.mention
        if self.support_role_id:
            role = interaction.guild.get_role(self.support_role_id)
            if role:
                ping_content += f" {role.mention}"

        await channel.send(content=ping_content, embed=ticket_embed, view=TicketControlView())
        await interaction.followup.send(f"✅ Ticket created! Check out {channel.mention}", ephemeral=True)

        target_log_id = self.log_channel_id or LOG_CHANNEL_ID
        log_chan = interaction.guild.get_channel(target_log_id)
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
# TICKET CHANNEL CONTROLS
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

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim", emoji="🙋‍♂️")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        
        embed = discord.Embed(description=f"🙋‍♂️ Ticket claimed by {interaction.user.mention}.", color=discord.Color.green())
        await interaction.channel.send(embed=embed)

    @discord.ui.button(label="Website Transcript", style=discord.ButtonStyle.primary, custom_id="ticket_transcript", emoji="📜")
    async def export_transcript(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        filename = f"{interaction.channel.name}.html"
        filepath = os.path.join(TRANSCRIPTS_DIR, filename)

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


# ---------------------------------------------------------------------------
# PUBLIC CARRY PANEL VIEW (Uses custom_id encoding)
# ---------------------------------------------------------------------------
class CarryPanelView(discord.ui.View):
    def __init__(self, category_id: int = 0, support_role_id: int = 0, log_channel_id: int = 0):
        super().__init__(timeout=None)
        cat = category_id or 0
        sup = support_role_id or 0
        log = log_channel_id or 0

        btn = discord.ui.Button(
            label="Request Carry",
            style=discord.ButtonStyle.primary,
            custom_id=f"req_carry:{cat}:{sup}:{log}",
            emoji="🛒"
        )
        btn.callback = self.request_carry_callback
        self.add_item(btn)

    async def request_carry_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")
        parts = custom_id.split(":")
        
        category_id = int(parts[1]) if len(parts) > 1 and parts[1] != "0" else None
        support_role_id = int(parts[2]) if len(parts) > 2 and parts[2] != "0" else None
        log_channel_id = int(parts[3]) if len(parts) > 3 and parts[3] != "0" else None

        await interaction.response.send_modal(
            CarryRequestModal(category_id, support_role_id, log_channel_id)
        )


# ---------------------------------------------------------------------------
# COG SETUP
# ---------------------------------------------------------------------------
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(TicketControlView())

    async def deploy_panel_from_dashboard(self, channel_id: int, title: str, description: str, category_id: int = None, support_role_id: int = None, log_channel_id: int = None):
        """Helper method called by your web dashboard route"""
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if not channel:
            return False, "Channel not found"

        embed = discord.Embed(
            title=title or "Request Carry",
            description=description or "Click below to request a carry ticket!",
            color=discord.Color.blue()
        )
        view = CarryPanelView(category_id, support_role_id, log_channel_id)
        await channel.send(embed=embed, view=view)
        return True, "Panel deployed successfully!"


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
