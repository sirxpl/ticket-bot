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

        # Category Setup
        category = None
        if self.category_id:
            category = interaction.guild.get_channel(self.category_id)
        if not category:
            category = discord.utils.get(interaction.guild.categories, name="CARRY TICKETS")
            if not category:
                category = await interaction.guild.create_category("CARRY TICKETS")

        # Overwrites Setup
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

        # Log Ticket Creation
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

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.secondary, custom_id="ticket_add_user", emoji="➕")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddUserModal())

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket_claim", emoji="🙋‍♂️")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        
        embed = discord.Embed(description=f"🙋‍♂️ Ticket claimed by {interaction.user.mention}.", color=discord.Color.green())
        await interaction.channel.send(embed=embed)

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
# PUBLIC CARRY PANEL VIEW
# ---------------------------------------------------------------------------
class CarryPanelView(discord.ui.View):
    def __init__(self, category_id: int = None, support_role_id: int = None, log_channel_id: int = None, title: str = None, description: str = None):
        super().__init__(timeout=None)
        self.category_id = category_id
        self.support_role_id = support_role_id
        self.log_channel_id = log_channel_id
        self.title = title
        self.description = description

    @discord.ui.button(label="Request Carry", style=discord.ButtonStyle.primary, custom_id="request_carry_btn", emoji="🛒")
    async def request_carry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            CarryRequestModal(self.category_id, self.support_role_id, self.log_channel_id)
        )


# ---------------------------------------------------------------------------
# MODAL: SET CUSTOM PANEL TITLE & DESCRIPTION
# ---------------------------------------------------------------------------
class EditPanelDetailsModal(discord.ui.Modal, title="Edit Panel Text"):
    panel_title = discord.ui.TextInput(
        label="Panel Title",
        placeholder="e.g. Request Carry",
        default="Request Carry",
        required=True,
        max_length=100
    )
    panel_description = discord.ui.TextInput(
        label="Panel Description",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Click below to request a carry ticket!",
        default="Click below to request a carry ticket!",
        required=True,
        max_length=500
    )

    def __init__(self, wizard_view):
        super().__init__()
        self.wizard_view = wizard_view

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard_view.custom_title = self.panel_title.value.strip()
        self.wizard_view.custom_description = self.panel_description.value.strip()
        await interaction.response.edit_message(embed=self.wizard_view.build_embed(), view=self.wizard_view)


# ---------------------------------------------------------------------------
# INTERACTIVE SETUP WIZARD (EXACT MATCH FOR IMAGE_AFB4F7.PNG)
# ---------------------------------------------------------------------------
class ConfigWizardView(discord.ui.View):
    def __init__(self, target_channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.target_channel = target_channel
        self.category_id = None
        self.support_role_id = None
        self.log_channel_id = None
        self.custom_title = "Request Carry"
        self.custom_description = "Click below to request a carry ticket!"

    def build_embed(self):
        embed = discord.Embed(
            title="⚙️ Ticket Setup Panel",
            description="Use the configuration options below to customize and send the ticket panel.",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="📢 Panel Channel",
            value=self.target_channel.mention,
            inline=True
        )
        embed.add_field(
            name="📂 Category",
            value=f"<#{self.category_id}>" if self.category_id else "`Default (CARRY TICKETS)`",
            inline=True
        )
        embed.add_field(
            name="👥 Support Role",
            value=f"<@&{self.support_role_id}>" if self.support_role_id else "`None`",
            inline=True
        )
        embed.add_field(
            name="📜 Log Channel",
            value=f"<#{self.log_channel_id}>" if self.log_channel_id else "`Default Logs`",
            inline=True
        )
        embed.add_field(
            name="✏️ Panel Title",
            value=f"`{self.custom_title}`",
            inline=True
        )
        embed.add_field(
            name="📝 Panel Description",
            value=f"`{self.custom_description}`",
            inline=False
        )
        return embed

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.category],
        placeholder="📂 Select Ticket Category...",
        row=0
    )
    async def set_category(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.category_id = select.values[0].id
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="👥 Select Support / Booster Role...",
        row=1
    )
    async def set_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        self.support_role_id = select.values[0].id
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📜 Select Log Channel...",
        row=2
    )
    async def set_log_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.log_channel_id = select.values[0].id
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="✏️ Edit Title & Description", style=discord.ButtonStyle.secondary, row=3)
    async def edit_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditPanelDetailsModal(self))

    @discord.ui.button(label="✅ Submit & Send Panel", style=discord.ButtonStyle.success, row=3)
    async def deploy_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel_embed = discord.Embed(
            title=self.custom_title,
            description=self.custom_description,
            color=discord.Color.blue()
        )
        view = CarryPanelView(
            category_id=self.category_id,
            support_role_id=self.support_role_id,
            log_channel_id=self.log_channel_id,
            title=self.custom_title,
            description=self.custom_description
        )
        await self.target_channel.send(embed=panel_embed, view=view)
        await interaction.response.send_message(f"🎉 Ticket Panel successfully deployed to {self.target_channel.mention}!", ephemeral=True)


class ChannelSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="📢 Select the channel where the panel should be sent..."
    )
    async def select_panel_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        target_channel = select.values[0]
        wizard = ConfigWizardView(target_channel)
        await interaction.response.send_message(embed=wizard.build_embed(), view=wizard, ephemeral=True)


# ---------------------------------------------------------------------------
# COG COMMAND SETUP
# ---------------------------------------------------------------------------
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(CarryPanelView())
        self.bot.add_view(TicketControlView())

    @app_commands.command(name="setup_carry", description="Start the interactive ticket setup wizard")
    @app_commands.default_permissions(administrator=True)
    async def setup_carry(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⚙️ Ticket Setup Wizard",
            description="Select the text channel where you want to post the **Carry Ticket Request Panel**.",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=ChannelSelectView(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
