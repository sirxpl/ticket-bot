import discord
from discord.ext import commands
from utils.storage import get_settings


class TicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)  # Persistent view across restarts
        self.bot = bot

    @discord.ui.button(
        label="Create Ticket", 
        style=discord.ButtonStyle.primary, 
        custom_id="create_ticket_btn",
        emoji="🎫"
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. Check global toggle setting from dashboard
        settings = get_settings()
        if not settings.get("tickets_enabled", True):
            await interaction.response.send_message(
                "🚫 Ticket creation is currently disabled by administrators.", 
                ephemeral=True
            )
            return

        guild = interaction.guild
        user = interaction.user

        # Defer interaction response while creating channel
        await interaction.response.defer(ephemeral=True)

        # 2. Setup Private Permissions
        # @everyone cannot see channel; User and Bot can see/send messages
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        # Grant view access to Support Role silently (without pinging them)
        support_role_id = settings.get("support_role_id")
        if support_role_id:
            support_role = guild.get_role(int(support_role_id))
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # Retrieve selected category if configured
        category_id = settings.get("default_category_id")
        category = guild.get_channel(int(category_id)) if category_id else None

        # 3. Create Channel Name (e.g. ticket-username)
        channel_name = f"ticket-{user.name.lower().replace(' ', '-')}"

        try:
            # Create actual channel on Discord server
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Ticket created by {user.name}"
            )

            # Send welcome message inside the new ticket channel (PING ONLY THE USER)
            embed = discord.Embed(
                title=f"🎫 Ticket Opened: {user.name}",
                description="Thank you for contacting support! Please describe your request below.",
                color=discord.Color.blue()
            )
            
            # Mention ONLY the user who opened the ticket
            await ticket_channel.send(content=f"{user.mention}", embed=embed)

            # Notify user where their ticket channel is
            await interaction.followup.send(
                f"✅ Ticket created! Please head over to {ticket_channel.mention}.", 
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I lack permissions to create channels or set permissions in this server.", 
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to create ticket channel: {str(e)}", 
                ephemeral=True
            )


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_ticket_view(self):
        return TicketView(self.bot)

    async def deploy_panel_from_dashboard(
        self, 
        channel_id: int, 
        title: str, 
        description: str, 
        category_id: int = None, 
        support_role_id: int = None,
        color: str = "#58b9ff",
        image_url: str = None,
        thumbnail_url: str = None,
        footer_text: str = None,
        fields: list = None
    ):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return False, f"Channel ID {channel_id} could not be found."

        # Save active category & support role settings globally
        settings = get_settings()
        if category_id:
            settings["default_category_id"] = category_id
        if support_role_id:
            settings["support_role_id"] = support_role_id
        
        # Save back to settings file
        from utils.storage import SETTINGS_FILE
        import json
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=4)

        try:
            hex_val = color.lstrip("#")
            embed_color = discord.Color(int(hex_val, 16))
        except Exception:
            embed_color = discord.Color.blue()

        embed = discord.Embed(
            title=title,
            description=description,
            color=embed_color
        )

        if image_url:
            embed.set_image(url=image_url)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        if footer_text:
            embed.set_footer(text=footer_text)

        if fields and isinstance(fields, list):
            for f in fields:
                field_name = f.get("name", "").strip()
                field_value = f.get("value", "").strip()
                is_inline = bool(f.get("inline", False))

                if field_name and field_value:
                    embed.add_field(
                        name=field_name,
                        value=field_value,
                        inline=is_inline
                    )

        try:
            await channel.send(embed=embed, view=self.get_ticket_view())
            return True, "Panel deployed successfully!"
        except discord.Forbidden:
            return False, "Bot lacks permission to send messages in that channel."
        except Exception as e:
            return False, f"Failed to send embed: {str(e)}"


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
