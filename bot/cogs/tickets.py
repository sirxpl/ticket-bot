import asyncio
import datetime
import html
import json
import logging
import os
import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import (
    add_active_ticket,
    add_cooldown,
    add_to_blacklist,
    get_blacklist_data,
    get_settings,
    is_on_cooldown,
    remove_active_ticket,
)

# logger for Render stdout/stderr so platform logs capture ticket close/delete events
logger = logging.getLogger("tickets")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)



# --- PERSISTENT CLOSE BUTTON & CONFIRMATION ---
class ConfirmView(discord.ui.View):
    def __init__(self, cog, target_channel):
        super().__init__(timeout=60)
        self.cog = cog
        self.target_channel = target_channel

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, inter: discord.Interaction, btn: discord.ui.Button
    ):
        await inter.response.defer(ephemeral=True)
        try:
            await self.cog.do_close(
                self.target_channel, inter.user, "Closed via button"
            )
        except Exception as e:
            try:
                await inter.followup.send(
                    f"❌ Failed to close ticket: {e}", ephemeral=True
                )
            except Exception:
                pass

        for child in self.children:
            child.disabled = True
        try:
            await inter.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, inter: discord.Interaction, btn: discord.ui.Button
    ):
        await inter.response.edit_message(
            content="Cancelled.", view=None, ephemeral=True
        )


class CloseConfirmView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close_btn",
        emoji="🔒",
    )
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        cog = self.bot.get_cog("TicketsCog")
        if not cog:
            await interaction.response.send_message(
                "❌ Close functionality is not available right now.",
                ephemeral=True,
            )
            return

        settings = get_settings()
        allowed_category = settings.get("default_category_id")
        if (
            allowed_category
            and interaction.channel.category
            and str(interaction.channel.category.id) != str(allowed_category)
        ):
            await interaction.response.send_message(
                "❌ This command only works in ticket channels.",
                ephemeral=True,
            )
            return

        perms = interaction.user.guild_permissions
        from utils.storage import get_logs_for_ticket

        creator_id = None
        try:
            logs = get_logs_for_ticket(str(interaction.channel.id))
            created = next(
                (l for l in logs if l.get("action") == "created"), None
            )
            if created and created.get("creator"):
                creator_id = created.get("creator").get("id")
        except Exception:
            creator_id = None

        if not (
            perms.manage_channels
            or (creator_id and str(interaction.user.id) == str(creator_id))
        ):
            await interaction.response.send_message(
                "❌ You don't have permission to close this ticket.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Are you sure you want to close this ticket?",
            view=ConfirmView(cog, interaction.channel),
            ephemeral=True,
        )


# --- MAIN TICKET CREATION SELECT MENU ---
class TicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.select(
        placeholder="Choose ticket type...",
        min_values=1,
        max_values=1,
        custom_id="create_ticket_select",
        options=[
            discord.SelectOption(
                label="General Support",
                description="General help or questions",
                emoji="❓",
            ),
            discord.SelectOption(
                label="Report a User",
                description="Report another user",
                emoji="⚠️",
            ),
            discord.SelectOption(
                label="Appeal / Ban Review",
                description="Appeal moderation action",
                emoji="📝",
            ),
        ],
    )
    async def ticket_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        settings = get_settings()
        if not settings.get("tickets_enabled", True):
            await interaction.response.send_message(
                "🚫 Ticket creation is currently disabled by administrators.",
                ephemeral=True,
            )
            return

        selection = select.values[0] if select.values else "General Support"
        outer_view = self

        # check blacklist
        try:
            bl = get_blacklist_data()
            blacklisted = [str(u) for u in bl.get("blacklisted_users", [])]
            if str(interaction.user.id) in blacklisted:
                guild = interaction.guild
                role = (
                    discord.utils.get(guild.roles, name="Ticket Blacklist")
                    if guild
                    else None
                )
                role_mention = role.mention if role else "@Ticket Blacklist"
                emb = discord.Embed(
                    title="❌ Blocked Role",
                    description=f"You cannot create tickets on this panel because you have the {role_mention} role.",
                    color=discord.Color.red(),
                )
                emb.set_footer(text="Tickety | Tickety.top")
                await interaction.response.send_message(
                    embed=emb, ephemeral=True
                )
                return
        except Exception:
            pass

        # check cooldown
        try:
            on_cd, cd = is_on_cooldown(str(interaction.user.id))
            if on_cd and cd:
                try:
                    expires_ts = int(cd.get("expires_ts"))
                    await interaction.response.send_message(
                        f"⏳ You are on cooldown until <t:{expires_ts}:F> (<t:{expires_ts}:R>).",
                        ephemeral=True,
                    )
                except Exception:
                    await interaction.response.send_message(
                        f"⏳ You are on cooldown until {cd.get('expires_at')}.",
                        ephemeral=True,
                    )
                return
        except Exception:
            pass

        class TicketModal(discord.ui.Modal, title=f"{selection}"):
            def __init__(self, author, selection):
                super().__init__()
                self.author = author
                self.selection = selection

                self.timezone = discord.ui.TextInput(
                    label="⏰ Which country and timezone are you from? *",
                    placeholder="e.g. UTC, PST, CET",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=100,
                )

                self.display_name = discord.ui.TextInput(
                    label="🎮 What is your roblox display name? *",
                    placeholder="Provide your display name (not username)",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=100,
                )

                self.can_join = discord.ui.TextInput(
                    label="🎲 Are you able to join a private server? *",
                    placeholder="Yes or No",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=10,
                )

                self.add_item(self.timezone)
                self.add_item(self.display_name)
                self.add_item(self.can_join)

            async def on_submit(
                self, modal_interaction: discord.Interaction
            ):
                settings = get_settings()
                guild = modal_interaction.guild
                user = self.author

                await modal_interaction.response.defer(ephemeral=True)

                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(
                        read_messages=False
                    ),
                    user: discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        attach_files=True,
                    ),
                    guild.me: discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        manage_channels=True,
                    ),
                }

                support_role_id = settings.get("support_role_id")
                if support_role_id:
                    support_role = guild.get_role(int(support_role_id))
                    if support_role:
                        overwrites[support_role] = discord.PermissionOverwrite(
                            read_messages=True, send_messages=True
                        )

                category_id = settings.get("default_category_id")
                category = (
                    guild.get_channel(int(category_id))
                    if category_id
                    else None
                )

                channel_name = f"ticket-{user.name.lower().replace(' ', '-')}"

                try:
                    ticket_channel = await guild.create_text_channel(
                        name=channel_name,
                        category=category,
                        overwrites=overwrites,
                        reason=f"Ticket created by {user.name}",
                    )

                    embed = discord.Embed(
                        title=f"🎫 {self.selection} - {user.name}",
                        description="",
                        color=discord.Color.blue(),
                    )

                    if self.timezone.value:
                        embed.add_field(
                            name="Timezone",
                            value=self.timezone.value,
                            inline=False,
                        )
                    if self.display_name.value:
                        embed.add_field(
                            name="Display name",
                            value=self.display_name.value,
                            inline=False,
                        )
                    embed.add_field(
                        name="Can join private server?",
                        value=self.can_join.value,
                        inline=False,
                    )

                    await ticket_channel.send(
                        content=f"{user.mention}", embed=embed
                    )

                    # Attach close button view
                    await ticket_channel.send(
                        view=CloseConfirmView(outer_view.bot)
                    )

                    # Log creation
                    try:
                        from utils.storage import (
                            append_ticket_log,
                            get_tickets_data,
                        )

                        ticket_id = str(ticket_channel.id)
                        timestamp = datetime.datetime.utcnow().isoformat() + "Z"

                        tickets_data = get_tickets_data()
                        tickets_data["ticket_counter"] = (
                            tickets_data.get("ticket_counter", 0) + 1
                        )
                        ticket_number = tickets_data["ticket_counter"]
                        try:
                            with open("data/tickets.json", "w") as tf:
                                json.dump(tickets_data, tf, indent=2)
                        except Exception:
                            pass

                        append_ticket_log({
                            "ticket_id": ticket_id,
                            "ticket_name": ticket_channel.name,
                            "action": "created",
                            "timestamp": timestamp,
                            "ticket_number": ticket_number,
                            "creator": {
                                "id": str(user.id),
                                "name": str(user),
                            },
                            "fields": {
                                "timezone": self.timezone.value,
                                "display_name": self.display_name.value,
                                "can_join": self.can_join.value,
                            },
                        })

                        add_active_ticket(
                            str(ticket_channel.id),
                            str(ticket_channel.id),
                            str(user.id),
                            ticket_number,
                        )
                    except Exception:
                        pass

                    await modal_interaction.followup.send(
                        f"✅ Ticket created! Please head over to {ticket_channel.mention}.",
                        ephemeral=True,
                    )

                except discord.Forbidden:
                    await modal_interaction.followup.send(
                        "❌ I lack permissions to create channels or set permissions in this server.",
                        ephemeral=True,
                    )
                except Exception as e:
                    await modal_interaction.followup.send(
                        f"❌ Failed to create ticket channel: {str(e)}",
                        ephemeral=True,
                    )

        modal = TicketModal(interaction.user, selection)
        await interaction.response.send_modal(modal)


# --- TICKETS COG & SLASH COMMANDS ---
class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_ticket_view(self):
        return TicketView(self.bot)

    # Slash Command: /close
    @app_commands.command(
        name="close",
        description="Close and delete the current ticket channel",
    )
    async def close_command(self, interaction: discord.Interaction):
        if not interaction.channel.name.startswith("ticket-"):
            await interaction.response.send_message(
                "❌ This command can only be used inside an active ticket channel.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Are you sure you want to close this ticket?",
            view=ConfirmView(self, interaction.channel),
            ephemeral=True,
        )

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
        fields: list = None,
    ):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return False, f"Channel ID {channel_id} could not be found."

        settings = get_settings()
        if category_id:
            settings["default_category_id"] = category_id
        if support_role_id:
            settings["support_role_id"] = support_role_id

        from utils.storage import SETTINGS_FILE

        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=4)

        try:
            hex_val = color.lstrip("#")
            embed_color = discord.Color(int(hex_val, 16))
        except Exception:
            embed_color = discord.Color.blue()

        embed = discord.Embed(
            title=title, description=description, color=embed_color
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
                        name=field_name, value=field_value, inline=is_inline
                    )

        try:
            await channel.send(embed=embed, view=self.get_ticket_view())
            return True, "Panel deployed successfully!"
        except discord.Forbidden:
            return (
                False,
                "Bot lacks permission to send messages in that channel.",
            )
        except Exception as e:
            return False, f"Failed to send embed: {str(e)}"

    async def do_close(
        self,
        channel: discord.abc.GuildChannel,
        executor: discord.abc.Snowflake,
        reason: str = "No reason provided.",
    ):
        """Generate transcript, log close, DM creator, then delete the channel after 5 seconds."""
        try:
            try:
                logger.info(
                    f"do_close invoked for channel={getattr(channel,'id',None)} by executor={getattr(executor,'id',executor)} reason={reason}"
                )
            except Exception:
                pass

            messages = []
            async for m in channel.history(limit=1000, oldest_first=True):
                ts = m.created_at.isoformat()
                author_name = str(m.author)
                author_id = getattr(m.author, "id", None)
                try:
                    avatar_url = m.author.display_avatar.url
                except Exception:
                    avatar_url = (
                        getattr(m.author, "avatar_url", None)
                        or "https://cdn.discordapp.com/embed/avatars/0.png"
                    )
                    content = m.content or ""

                attachments_data = []
                for a in m.attachments:
                    is_image = bool(
                        a.content_type and a.content_type.startswith("image/")
                    ) or a.filename.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".gif", ".webp")
                    )
                    attachments_data.append({
                        "url": a.url,
                        "filename": a.filename,
                        "is_image": is_image,
                    })

                embeds_data = []
                for e in m.embeds:
                    embeds_data.append({
                        "title": e.title,
                        "description": e.description,
                        "url": e.url if e.url else None,
                        "color": e.color.value if e.color else None,
                        "author_name": e.author.name if e.author else None,
                        "author_icon": e.author.icon_url if e.author else None,
                        "thumbnail_url": e.thumbnail.url if e.thumbnail else None,
                        "image_url": e.image.url if e.image else None,
                        "footer_text": e.footer.text if e.footer else None,
                        "footer_icon": e.footer.icon_url if e.footer else None,
                        "fields": [
                            {
                                "name": f.name,
                                "value": f.value,
                                "inline": f.inline,
                            }
                            for f in e.fields
                        ],
                    })

                components_data = []
                for row in m.components:
                    row_buttons = []
                    for child in getattr(row, "children", []):
                        if isinstance(child, discord.Button):
                            row_buttons.append({
                                "label": child.label,
                                "style": str(child.style).split(".")[-1],
                                "emoji": str(child.emoji) if child.emoji else None,
                                "url": child.url,
                            })
                    if row_buttons:
                        components_data.append(row_buttons)

                messages.append({
                    "ts": ts,
                    "author_name": author_name,
                    "author_id": str(author_id) if author_id else None,
                    "avatar_url": avatar_url,
                    "content": content,
                    "attachments": attachments_data,
                    "embeds": embeds_data,
                    "components": components_data,
                    "is_bot": getattr(m.author, "bot", False),
                })

            transcripts_dir = __import__(
                "utils.storage", fromlist=["TRANSCRIPTS_DIR"]
            ).TRANSCRIPTS_DIR
            os.makedirs(transcripts_dir, exist_ok=True)
            filename = f"ticket-{channel.id}.html"
            generated_at = datetime.datetime.utcnow().isoformat() + "Z"

            ticket_meta = {}
            try:
                from utils.storage import get_logs_for_ticket

                logs = get_logs_for_ticket(str(channel.id))
                created = next(
                    (l for l in logs if l.get("action") == "created"), None
                )
                if created:
                    ticket_meta = created
            except Exception:
                ticket_meta = {}

            html_out = build_discord_like_transcript(
                messages, channel.name, ticket_meta, generated_at, filename
            )
            path = os.path.join(transcripts_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html_out)

            timestamp = datetime.datetime.utcnow().isoformat() + "Z"
            from utils.storage import append_ticket_log, get_logs_for_ticket

            creator_id = None
            try:
                logs = get_logs_for_ticket(str(channel.id))
                created = next(
                    (l for l in logs if l.get("action") == "created"), None
                )
                if created and created.get("creator"):
                    creator_id = created.get("creator").get("id")
            except Exception:
                pass

            append_ticket_log({
                "ticket_id": str(channel.id),
                "ticket_name": channel.name,
                "action": "closed",
                "timestamp": timestamp,
                "executor": {
                    "id": str(getattr(executor, "id", executor)),
                    "name": str(executor),
                },
                "reason": reason,
                "transcript_file": filename,
                "allowed_user_id": creator_id,
            })

            # apply cooldown when the ticket is closed
            try:
                if creator_id:
                    add_cooldown(str(creator_id), hours=8)
            except Exception:
                pass

            try:
                remove_active_ticket(str(channel.id))
            except Exception:
                pass

            # DM creator transcript
            from utils.storage import generate_transcript_url

            signed_url = generate_transcript_url(
                filename, expires_seconds=3600
            )

            if creator_id:
                try:
                    user = await self.bot.fetch_user(int(creator_id))

                    class LinkView(discord.ui.View):

                        def __init__(self, url):
                            super().__init__(timeout=None)
                            self.add_item(
                                discord.ui.Button(
                                    label="View Transcript", url=url
                                )
                            )

                    await user.send(
                        content=f"Your ticket '{channel.name}' has been closed. The transcript is available for 1 hour.",
                        view=LinkView(signed_url),
                    )
                except Exception as e:
                    logger.exception(f"Failed DM user: {e}")

            try:
                await channel.send("This ticket will be deleted in 5 seconds.")
            except Exception:
                pass

            me = None
            if channel.guild:
                me = channel.guild.get_member(self.bot.user.id)
                if not me:
                    try:
                        me = await channel.guild.fetch_member(
                            self.bot.user.id
                        )
                    except Exception:
                        me = None

            can_delete = False
            if me:
                perms = channel.permissions_for(me)
                guild_perms = getattr(me, "guild_permissions", None)
                can_delete = bool(
                    (guild_perms and guild_perms.manage_channels)
                    or (perms and perms.manage_channels)
                )

            await asyncio.sleep(5)
            ts_now = datetime.datetime.utcnow().isoformat() + "Z"

            if can_delete:
                try:
                    await channel.delete(reason=f"Ticket closed: {reason}")
                    append_ticket_log({
                        "ticket_id": str(channel.id),
                        "ticket_name": channel.name,
                        "action": "deleted",
                        "timestamp": ts_now,
                        "executor": {
                            "id": str(getattr(executor, "id", executor)),
                            "name": str(executor),
                        },
                    })
                except Exception as e:
                    append_ticket_log({
                        "ticket_id": str(channel.id),
                        "ticket_name": channel.name,
                        "action": "delete_failed",
                        "timestamp": ts_now,
                        "executor": {
                            "id": str(getattr(executor, "id", executor)),
                            "name": str(executor),
                        },
                        "error": str(e),
                    })
                    try:
                        await channel.send(
                            "⚠️ Failed to delete channel; archiving instead."
                        )
                        await channel.set_permissions(
                            channel.guild.default_role, read_messages=False
                        )
                        await channel.edit(name=f"closed-{channel.name}")
                    except Exception:
                        pass
        except Exception as global_err:
            logger.exception(f"Error in do_close: {global_err}")


async def setup(bot):
    cog = TicketsCog(bot)
    await bot.add_cog(cog)
    bot.add_view(TicketView(bot))
    bot.add_view(CloseConfirmView(bot))
