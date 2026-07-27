# cogs/tickets.py
import os
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select, Modal, TextInput
from utils.storage import (
    get_tickets_data,
    save_tickets_data,
    is_user_blacklisted,
    add_to_blacklist,
    remove_from_blacklist,
    create_html_transcript
)

BLACKLIST_ROLE_ID = 1530330613029015704

# --- MODAL FOR TICKET DETAILS ---
class CarryQuestionsModal(Modal, title="Ticket Details"):
    roblox_user = TextInput(label="Roblox Username", placeholder="e.g. Builderman", required=True)
    details = TextInput(label="Details / Notes", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, category_name: str, prefix: str):
        super().__init__()
        self.category_name = category_name
        self.prefix = prefix

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user, guild = interaction.user, interaction.guild

        data = get_tickets_data()
        data["ticket_counter"] = data.get("ticket_counter", 0) + 1
        save_tickets_data(data)

        channel_name = f"{self.prefix}-{data['ticket_counter']:04d}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        
        embed = discord.Embed(
            title=f"🎫 {self.category_name} Ticket",
            description=f"Welcome {user.mention}! Staff will be with you shortly.\n\n**Roblox User:** {self.roblox_user.value}\n**Notes:** {self.details.value or 'None'}",
            color=discord.Color.green()
        )
        await ticket_channel.send(content=f"{user.mention}", embed=embed, view=TicketControlView())
        await interaction.followup.send(f"✅ Ticket created in {ticket_channel.mention}", ephemeral=True)


# --- TICKET CONTROL BUTTONS ---
class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⏳ Generating transcript and closing channel...")
        await create_html_transcript(interaction.channel)
        await interaction.channel.delete()


# --- TICKET SELECTION DROPDOWN ---
class TicketDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="General Support", description="Get general help", emoji="❓", value="general:ticket"),
            discord.SelectOption(label="Report User", description="Report a rule breaker", emoji="🚫", value="report:report"),
        ]
        super().__init__(placeholder="Select a ticket category...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if is_user_blacklisted(interaction.user.id):
            return await interaction.response.send_message("❌ You are blacklisted from creating tickets.", ephemeral=True)

        category_name, prefix = self.values[0].split(":")
        await interaction.response.send_modal(CarryQuestionsModal(category_name.capitalize(), prefix))


class TicketDropdownView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())


# --- TICKET COG & COMMANDS ---
class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup_tickets", description="Send the ticket creation panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_tickets(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Select an option from the menu below to open a ticket.",
            color=discord.Color.blue()
        )
        await interaction.channel.send(embed=embed, view=TicketDropdownView())
        await interaction.response.send_message("✅ Panel sent!", ephemeral=True)

    @app_commands.command(name="blacklist", description="Blacklist a user from creating tickets")
    @app_commands.describe(user="The member to blacklist")
    @app_commands.checks.has_permissions(administrator=True)
    async def blacklist(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        add_to_blacklist(user.id)
        role = interaction.guild.get_role(BLACKLIST_ROLE_ID)
        
        if role:
            try:
                await user.add_roles(role)
            except discord.Forbidden:
                pass

        await interaction.followup.send(f"🚫 {user.mention} has been added to the blacklist.", ephemeral=True)

    @app_commands.command(name="unblacklist", description="Remove a user from the ticket blacklist")
    @app_commands.describe(user="The member to unblacklist")
    @app_commands.checks.has_permissions(administrator=True)
    async def unblacklist(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        remove_from_blacklist(user.id)
        role = interaction.guild.get_role(BLACKLIST_ROLE_ID)

        if role and role in user.roles:
            try:
                await user.remove_roles(role)
            except discord.Forbidden:
                pass

        await interaction.followup.send(f"✅ {user.mention} has been removed from the blacklist.", ephemeral=True)

    @app_commands.command(name="move_ticket", description="Move the current ticket channel to another category")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def move_ticket(self, interaction: discord.Interaction):
        categories = interaction.guild.categories

        if not categories:
            return await interaction.response.send_message("❌ No categories found in this server.", ephemeral=True)

        options = [
            discord.SelectOption(label=cat.name, value=str(cat.id), emoji="📁")
            for cat in categories[:25]
        ]

        select = Select(placeholder="Choose a category to move this ticket to...", options=options)

        async def select_callback(select_interaction: discord.Interaction):
            category_id = int(select.values[0])
            target_category = select_interaction.guild.get_channel(category_id)
            
            await select_interaction.channel.edit(category=target_category)
            await select_interaction.response.send_message(f"✅ Moved ticket to **{target_category.name}**!")

        select.callback = select_callback
        view = View()
        view.add_item(select)

        await interaction.response.send_message("Select the target category:", view=view, ephemeral=True)

    @app_commands.command(name="rename_ticket", description="Rename the current ticket channel")
    @app_commands.describe(new_name="The new name for this ticket channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def rename_ticket(self, interaction: discord.Interaction, new_name: str):
        old_name = interaction.channel.name
        
        clean_name = new_name.lower().replace(" ", "-")
        await interaction.channel.edit(name=clean_name)
        
        await interaction.response.send_message(
            f"✅ Renamed channel from **#{old_name}** to **#{clean_name}**!", 
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
