import discord
from discord.ext import commands
from utils.storage import get_settings


class TicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)  # Persistent view across restarts
        self.bot = bot

    @discord.ui.select(
        placeholder="Choose ticket type...",
        min_values=1,
        max_values=1,
        custom_id="create_ticket_select",
        options=[
            discord.SelectOption(label="General Support", description="General help or questions", emoji="❓"),
            discord.SelectOption(label="Report a User", description="Report another user", emoji="⚠️"),
            discord.SelectOption(label="Appeal / Ban Review", description="Appeal moderation action", emoji="📝"),
        ]
    )
    async def ticket_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        # 1. Check global toggle setting from dashboard
        settings = get_settings()
        if not settings.get("tickets_enabled", True):
            await interaction.response.send_message(
                "🚫 Ticket creation is currently disabled by administrators.",
                ephemeral=True
            )
            return

        selection = select.values[0] if select.values else "General Support"

        class TicketModal(discord.ui.Modal, title=f"Ticket - {selection}"):
            def __init__(self, author, selection):
                super().__init__()
                self.author = author
                self.selection = selection

                # Form fields requested by user (no can_join here; it'll be a select step after)
                self.timezone = discord.ui.TextInput(
                    label="Timezone(s)",
                    placeholder="e.g. UTC, PST, CET",
                    required=False,
                    style=discord.TextStyle.short
                )

                self.usernames = discord.ui.TextInput(
                    label="Username(s) (comma separated)",
                    placeholder="user1, user2",
                    required=False,
                    style=discord.TextStyle.short
                )

                self.details = discord.ui.TextInput(
                    label="Details",
                    style=discord.TextStyle.paragraph,
                    required=True,
                    placeholder="Describe your request or the issue in detail"
                )

                self.add_item(self.timezone)
                self.add_item(self.usernames)
                self.add_item(self.details)

            async def on_submit(self, modal_interaction: discord.Interaction):
                # After modal submit, ask a Yes/No select for `Can you join a private server?`
                author = self.author
                selection = self.selection
                modal_values = {
                    "timezone": self.timezone.value,
                    "usernames": self.usernames.value,
                    "details": self.details.value
                }

                class CanJoinView(discord.ui.View):
                    def __init__(self, author, modal_values, selection):
                        super().__init__(timeout=120)
                        self.author = author
                        self.modal_values = modal_values
                        self.selection = selection

                    @discord.ui.select(
                        placeholder="Can you join a private server?",
                        min_values=1,
                        max_values=1,
                        options=[
                            discord.SelectOption(label="Yes", value="Yes", description="I can join a private server", emoji="✅"),
                            discord.SelectOption(label="No", value="No", description="I cannot join a private server", emoji="❌"),
                        ]
                    )
                    async def can_join_select(self, interaction: discord.Interaction, select: discord.ui.Select):
                        if interaction.user.id != self.author.id:
                            await interaction.response.send_message("This select is for the person who opened the modal.", ephemeral=True)
                            return

                        can_join_value = select.values[0]
                        await interaction.response.defer(ephemeral=True)

                        # Create channel using collected modal values + can_join_value
                        settings = get_settings()
                        guild = interaction.guild
                        user = self.author

                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(read_messages=False),
                            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
                            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
                        }

                        support_role_id = settings.get("support_role_id")
                        if support_role_id:
                            support_role = guild.get_role(int(support_role_id))
                            if support_role:
                                overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

                        category_id = settings.get("default_category_id")
                        category = guild.get_channel(int(category_id)) if category_id else None

                        channel_name = f"ticket-{user.name.lower().replace(' ', '-') }"

                        try:
                            ticket_channel = await guild.create_text_channel(
                                name=channel_name,
                                category=category,
                                overwrites=overwrites,
                                reason=f"Ticket created by {user.name}"
                            )

                            embed = discord.Embed(
                                title=f"🎫 {self.selection} - {user.name}",
                                description=self.modal_values.get("details", ""),
                                color=discord.Color.blue()
                            )

                            if self.modal_values.get("timezone"):
                                embed.add_field(name="Timezones", value=self.modal_values.get("timezone"), inline=False)
                            if self.modal_values.get("usernames"):
                                embed.add_field(name="Usernames", value=self.modal_values.get("usernames"), inline=False)
                            embed.add_field(name="Can join private server?", value=can_join_value, inline=False)

                            await ticket_channel.send(content=f"{user.mention}", embed=embed)

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

                        # disable the view to prevent reuse
                        for child in self.children:
                            child.disabled = True
                        try:
                            await interaction.message.edit(view=self)
                        except Exception:
                            pass

                view = CanJoinView(author, modal_values, selection)
                await modal_interaction.response.send_message("Please choose: Can you join a private server?", view=view, ephemeral=True)

        modal = TicketModal(interaction.user, selection)
        await interaction.response.send_modal(modal)


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
