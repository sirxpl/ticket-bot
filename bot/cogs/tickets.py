import discord
from discord.ext import commands
from utils.storage import get_settings
from utils.storage import add_active_ticket, remove_active_ticket, add_cooldown, is_on_cooldown, get_blacklist_data, add_to_blacklist

import html
import datetime
import os


def build_discord_like_transcript(messages, channel_name, ticket_meta, generated_at_iso, filename):
    """Render a dark-themed Discord-like HTML transcript.
    messages: list of dicts with keys: ts, author_name, author_id, avatar_url, content, attachments, is_bot
    ticket_meta: dict with fields like timezone, display_name, can_join, creator (dict)
    """
    safe = html.escape
    parts = []
    parts.append('<!doctype html>')
    parts.append('<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">')
    parts.append(f'<title>Transcript - {safe(channel_name)}</title>')
    parts.append('<style>body{background:#0f1114;color:#e6eef8;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif;margin:0} .container{max-width:900px;margin:20px auto;padding:18px} .embed{background:#2f3136;border-left:4px solid #2a9df4;padding:12px;border-radius:6px;margin-bottom:16px} .embed h2{margin:0 0 6px 0} .field{margin:6px 0;padding:6px 10px;background:#222326;border-radius:6px} .msg{display:flex;gap:12px;padding:10px;border-radius:8px;background:linear-gradient(180deg,#0f1114,#0f1114);margin-bottom:6px} .avatar{width:42px;height:42px;border-radius:50%;flex:0 0 42px} .msg-body{flex:1} .meta{color:#9aa5b1;font-size:13px;margin-bottom:6px} .content{white-space:pre-wrap;color:#dbe7ef} .attachments{margin-top:6px} .footer{margin-top:18px;padding:10px;color:#9aa5b1;font-size:13px;border-top:1px solid #1b1d20}</style>')
    parts.append('</head><body>')
    parts.append('<div class="container">')
    # ticket embed
    parts.append('<div class="embed">')
    parts.append(f'<h2>🎫 {safe(channel_name)}</h2>')
    creator = ticket_meta.get('creator') if ticket_meta else None
    if creator:
        parts.append(f'<div style="font-size:14px;color:#9aa5b1">Created by: {safe(creator.get("name",""))}</div>')
    parts.append('<div style="margin-top:8px;display:flex;gap:10px;flex-wrap:wrap">')
    fields = ticket_meta.get('fields') if ticket_meta else {}
    if fields:
        for k in ('timezone','display_name','can_join'):
            v = fields.get(k)
            if v:
                parts.append(f'<div class="field"><strong>{safe(k.replace("_"," ").title())}:</strong> {safe(v)}</div>')
    parts.append('</div>')
    parts.append('</div>')

    # messages
    parts.append('<div>')
    for m in messages:
        parts.append('<div class="msg">')
        parts.append(f'<img class="avatar" src="{safe(m.get("avatar_url") or "https://cdn.discordapp.com/embed/avatars/0.png")}" alt="avatar"/>')
        parts.append('<div class="msg-body">')
        parts.append(f'<div class="meta"><strong>{safe(m.get("author_name","Unknown"))}</strong> <span style="margin-left:8px">{safe(m.get("ts",""))}</span></div>')
        parts.append(f'<div class="content">{safe(m.get("content",""))}</div>')
        if m.get('attachments'):
            parts.append('<div class="attachments">')
            # attachments is a space-separated list of URLs
            for a in str(m.get('attachments')).split():
                if a.lower().endswith(('.png','.jpg','.jpeg','.gif','.webp')):
                    parts.append(f'<div><img src="{safe(a)}" style="max-width:360px;border-radius:6px;margin-top:6px"/></div>')
                else:
                    parts.append(f'<div><a href="{safe(a)}" target="_blank">{safe(a)}</a></div>')
            parts.append('</div>')
        parts.append('</div>')
        parts.append('</div>')
    parts.append('</div>')

    # footer with generated time and download link
    parts.append('<div class="footer">')
    parts.append(f'Transcript generated on {safe(generated_at_iso)}')
    # download link
    parts.append(f'&nbsp; • &nbsp;<a href="{safe(filename)}" download>Download HTML</a>')
    parts.append('</div>')

    parts.append('</div></body></html>')
    return '\n'.join(parts)


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
        outer_view = self

        # check blacklist
        try:
            bl = get_blacklist_data()
            blacklisted = [str(u) for u in bl.get('blacklisted_users', [])]
            if str(interaction.user.id) in blacklisted:
                # send embed explaining blocked role
                guild = interaction.guild
                role = discord.utils.get(guild.roles, name='Ticket Blacklist') if guild else None
                role_mention = role.mention if role else '@Ticket Blacklist'
                emb = discord.Embed(title='❌ Blocked Role', description=f'You cannot create tickets on this panel because you have the {role_mention} role.', color=discord.Color.red())
                emb.set_footer(text='Tickety | Tickety.top')
                await interaction.response.send_message(embed=emb, ephemeral=True)
                return
        except Exception:
            pass

        # check cooldown
        try:
            on_cd, cd = is_on_cooldown(str(interaction.user.id))
            if on_cd and cd:
                try:
                    expires_ts = int(cd.get('expires_ts'))
                    # Discord dynamic timestamps render in each user's timezone and show relative time
                    await interaction.response.send_message(f"⏳ You are on cooldown until <t:{expires_ts}:F> (<t:{expires_ts}:R>).", ephemeral=True)
                except Exception:
                    await interaction.response.send_message(f"⏳ You are on cooldown until {cd.get('expires_at')}.", ephemeral=True)
                return
        except Exception:
            pass

        class TicketModal(discord.ui.Modal, title=f"{selection}"):
            def __init__(self, author, selection):
                super().__init__()
                self.author = author
                self.selection = selection

                # Fields matching the second image: timezone, display name, can-join
                self.timezone = discord.ui.TextInput(
                    label="⏰ Which country and timezone are you from? *",
                    placeholder="e.g. UTC, PST, CET",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=100
                )

                self.display_name = discord.ui.TextInput(
                    label="🎮 What is your roblox display name? *",
                    placeholder="Provide your display name (not username)",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=100
                )

                self.can_join = discord.ui.TextInput(
                    label="🎲 Are you able to join a private server? *",
                    placeholder="Yes or No",
                    required=True,
                    style=discord.TextStyle.short,
                    max_length=10
                )

                self.add_item(self.timezone)
                self.add_item(self.display_name)
                self.add_item(self.can_join)

            async def on_submit(self, modal_interaction: discord.Interaction):
                settings = get_settings()
                guild = modal_interaction.guild
                user = self.author

                await modal_interaction.response.defer(ephemeral=True)

                # 2. Setup Private Permissions
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
                        description="",
                        color=discord.Color.blue()
                    )

                    # Include form fields as embed fields for clarity
                    if self.timezone.value:
                        embed.add_field(name="Timezone", value=self.timezone.value, inline=False)
                    if self.display_name.value:
                        embed.add_field(name="Display name", value=self.display_name.value, inline=False)
                    embed.add_field(name="Can join private server?", value=self.can_join.value, inline=False)

                    # Send ticket embed and a Close button that prompts for confirmation
                    await ticket_channel.send(content=f"{user.mention}", embed=embed)

                    # create a close button view attached to a message in the ticket channel
                    class CloseConfirmView(discord.ui.View):
                        def __init__(self, cog):
                            super().__init__(timeout=None)
                            self.cog = cog

                        @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
                        async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                            # only allow users with manage_channels or the ticket creator to confirm
                            settings = get_settings()
                            allowed_category = settings.get("default_category_id")
                            if allowed_category and interaction.channel.category and str(interaction.channel.category.id) != str(allowed_category):
                                await interaction.response.send_message("❌ This command only works in ticket channels.", ephemeral=True)
                                return

                            perms = interaction.user.guild_permissions
                            # find creator id from logs
                            from utils.storage import get_logs_for_ticket
                            creator_id = None
                            try:
                                logs = get_logs_for_ticket(str(interaction.channel.id))
                                created = next((l for l in logs if l.get('action') == 'created'), None)
                                if created and created.get('creator'):
                                    creator_id = created.get('creator').get('id')
                            except Exception:
                                creator_id = None

                            if not (perms.manage_channels or (creator_id and str(interaction.user.id) == str(creator_id))):
                                await interaction.response.send_message("❌ You don't have permission to close this ticket.", ephemeral=True)
                                return

                            # send a confirmation prompt with Confirm/Cancel buttons
                            class ConfirmView(discord.ui.View):
                                def __init__(self, cog, target_channel):
                                    super().__init__(timeout=60)
                                    self.cog = cog
                                    self.target_channel = target_channel

                                @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
                                async def confirm(self, inter: discord.Interaction, btn: discord.ui.Button):
                                    await inter.response.defer(ephemeral=True)
                                    # proceed to close using the target channel (ticket channel)
                                    try:
                                        await self.cog.do_close(self.target_channel, inter.user, "Closed via button")
                                    except Exception as e:
                                        try:
                                            await inter.followup.send(f"❌ Failed to close ticket: {e}", ephemeral=True)
                                        except Exception:
                                            pass
                                    # disable buttons
                                    for child in self.children:
                                        child.disabled = True
                                    try:
                                        await inter.message.edit(view=self)
                                    except Exception:
                                        pass

                                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                                async def cancel(self, inter: discord.Interaction, btn: discord.ui.Button):
                                    await inter.response.edit_message(content="Cancelled.", view=None, ephemeral=True)

                            # if cog missing, notify the user
                            if not self.cog:
                                await interaction.response.send_message("❌ Close functionality is not available right now.", ephemeral=True)
                                return

                            await interaction.response.send_message("Are you sure you want to close this ticket?", view=ConfirmView(self.cog, interaction.channel), ephemeral=True)

                    # attach the close button view message in the ticket channel for staff
                    tickets_cog = outer_view.bot.get_cog('TicketsCog')
                    if not tickets_cog:
                        await ticket_channel.send("⚠️ Close button unavailable (Tickets cog missing).")
                    else:
                        await ticket_channel.send(content=None, embed=None, view=CloseConfirmView(tickets_cog))

                    # Log ticket creation to disk for dashboard viewing
                    try:
                        from utils.storage import append_ticket_log, get_tickets_data
                        import datetime
                        ticket_id = str(ticket_channel.id)
                        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
                        # increment counter in tickets file (best effort)
                        tickets_data = get_tickets_data()
                        tickets_data["ticket_counter"] = tickets_data.get("ticket_counter", 0) + 1
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
                            "creator": {"id": str(user.id), "name": str(user)} ,
                            "fields": {
                                "timezone": getattr(self, 'timezone', None) and getattr(self.timezone, 'value', None) or None,
                                "display_name": getattr(self, 'display_name', None) and getattr(self.display_name, 'value', None) or None,
                                "can_join": getattr(self, 'can_join', None) and getattr(self.can_join, 'value', None) or None
                            }
                        })

                        # mark active ticket (ticket number set from tickets_data)
                        try:
                            ticket_number = tickets_data.get("ticket_counter")
                            add_active_ticket(str(ticket_channel.id), str(ticket_channel.id), str(user.id), ticket_number)
                        except Exception:
                            pass

                    except Exception:
                        pass

                    await modal_interaction.followup.send(
                        f"✅ Ticket created! Please head over to {ticket_channel.mention}.",
                        ephemeral=True
                    )

                except discord.Forbidden:
                    await modal_interaction.followup.send(
                        "❌ I lack permissions to create channels or set permissions in this server.", 
                        ephemeral=True
                    )
                except Exception as e:
                    await modal_interaction.followup.send(
                        f"❌ Failed to create ticket channel: {str(e)}", 
                        ephemeral=True
                    )

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

    async def do_close(self, channel: discord.abc.GuildChannel, executor: discord.abc.Snowflake, reason: str = 'No reason provided.'):
        """Generate transcript, log close, DM creator, then delete the channel after 5 seconds."""
        try:
            # collect messages
            messages = []
            async for m in channel.history(limit=1000, oldest_first=True):
                ts = m.created_at.isoformat()
                author_name = str(m.author)
                author_id = getattr(m.author, 'id', None)
                # avatar fallback
                try:
                    avatar_url = m.author.display_avatar.url
                except Exception:
                    avatar_url = getattr(m.author, 'avatar_url', None) or "https://cdn.discordapp.com/embed/avatars/0.png"
                content = (m.content or "")
                attachments = ' '.join(a.url for a in m.attachments) if m.attachments else ''
                messages.append({
                    'ts': ts,
                    'author_name': author_name,
                    'author_id': str(author_id) if author_id else None,
                    'avatar_url': avatar_url,
                    'content': content,
                    'attachments': attachments,
                    'is_bot': getattr(m.author, 'bot', False)
                })

            # build transcript HTML (Discord-like)
            transcripts_dir = __import__('utils.storage', fromlist=['TRANSCRIPTS_DIR']).TRANSCRIPTS_DIR
            os.makedirs(transcripts_dir, exist_ok=True)
            filename = f"ticket-{channel.id}.html"
            generated_at = datetime.datetime.utcnow().isoformat() + 'Z'
            # try to find ticket meta from logs
            ticket_meta = {}
            try:
                from utils.storage import get_logs_for_ticket
                logs = get_logs_for_ticket(str(channel.id))
                created = next((l for l in logs if l.get('action') == 'created'), None)
                if created:
                    ticket_meta = created
            except Exception:
                ticket_meta = {}

            html_out = build_discord_like_transcript(messages, channel.name, ticket_meta, generated_at, filename)
            path = os.path.join(transcripts_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html_out)

            # append log
            timestamp = datetime.datetime.utcnow().isoformat() + 'Z'
            from utils.storage import append_ticket_log, get_logs_for_ticket
            # find creator id from logs
            creator_id = None
            try:
                logs = get_logs_for_ticket(str(channel.id))
                created = next((l for l in logs if l.get('action') == 'created'), None)
                if created and created.get('creator'):
                    creator_id = created.get('creator').get('id')
            except Exception:
                pass

            append_ticket_log({
                'ticket_id': str(channel.id),
                'ticket_name': channel.name,
                'action': 'closed',
                'timestamp': timestamp,
                'executor': {'id': str(getattr(executor, 'id', executor)), 'name': str(executor)},
                'reason': reason,
                'transcript_file': filename,
                'allowed_user_id': creator_id
            })

            # apply cooldown when the ticket is closed (start cooldown now)
            try:
                if creator_id:
                    add_cooldown(str(creator_id), hours=8)
            except Exception:
                pass

            # remove from active tickets
            try:
                remove_active_ticket(str(channel.id))
            except Exception:
                pass

            # DM creator with a short-lived signed transcript URL and a link button
            from utils.storage import generate_transcript_url
            signed_url = generate_transcript_url(filename, expires_seconds=3600)

            if creator_id:
                try:
                    user = await self.bot.fetch_user(int(creator_id))
                    try:
                        # send a message with a link button
                        class LinkView(discord.ui.View):
                            def __init__(self, url):
                                super().__init__(timeout=None)
                                self.add_item(discord.ui.Button(label="View Transcript", url=url))
                        await user.send(content=f"Your ticket '{channel.name}' has been closed. The transcript is available for 1 hour.", view=LinkView(signed_url))
                    except Exception:
                        await user.send(f"Your ticket '{channel.name}' has been closed. You can view the transcript here: {signed_url}")
                except Exception:
                    pass

            # announce and wait 5 seconds before deleting (if possible)
            try:
                await channel.send("This ticket will be deleted in 5 seconds.")
            except Exception:
                pass

            # check bot permissions to delete
            try:
                me = channel.guild.me if channel.guild else None
                can_delete = False
                if me:
                    perms = channel.permissions_for(me)
                    can_delete = perms.manage_channels
                # wait then delete if allowed
                await asyncio.sleep(5)
                # helper timestamp for logs
                import datetime as _dt
                ts_now = _dt.datetime.utcnow().isoformat() + 'Z'
                if can_delete:
                    try:
                        await channel.delete(reason=f"Ticket closed: {reason}")
                        # log successful deletion
                        try:
                            append_ticket_log({
                                'ticket_id': str(channel.id),
                                'ticket_name': channel.name,
                                'action': 'deleted',
                                'timestamp': ts_now,
                                'executor': {'id': str(getattr(executor, 'id', executor)), 'name': str(executor)}
                            })
                        except Exception:
                            pass
                    except Exception as e:
                        # log delete failure
                        try:
                            append_ticket_log({
                                'ticket_id': str(channel.id),
                                'ticket_name': channel.name,
                                'action': 'delete_failed',
                                'timestamp': ts_now,
                                'executor': {'id': str(getattr(executor, 'id', executor)), 'name': str(executor)},
                                'error': str(e)
                            })
                        except Exception:
                            pass
                        # fallback: try to archive by revoking default role and renaming
                        try:
                            await channel.send("⚠️ Failed to delete channel; archiving instead.")
                        except Exception:
                            pass
                        try:
                            await channel.set_permissions(channel.guild.default_role, read_messages=False)
                            await channel.edit(name=f"closed-{channel.name}")
                            try:
                                append_ticket_log({
                                    'ticket_id': str(channel.id),
                                    'ticket_name': channel.name,
                                    'action': 'archived',
                                    'timestamp': ts_now
                                })
                            except Exception:
                                pass
                        except Exception as e2:
                            try:
                                append_ticket_log({
                                    'ticket_id': str(channel.id),
                                    'ticket_name': channel.name,
                                    'action': 'archive_failed',
                                    'timestamp': ts_now,
                                    'error': str(e2)
                                })
                            except Exception:
                                pass
                else:
                    # can't delete: archive channel (hide from @everyone) and rename
                    try:
                        append_ticket_log({
                            'ticket_id': str(channel.id),
                            'ticket_name': channel.name,
                            'action': 'no_delete_permission',
                            'timestamp': ts_now
                        })
                    except Exception:
                        pass
                    try:
                        await channel.send("⚠️ I lack permission to delete this channel; archiving instead.")
                    except Exception:
                        pass
                    try:
                        await channel.set_permissions(channel.guild.default_role, read_messages=False)
                        await channel.edit(name=f"closed-{channel.name}")
                        try:
                            append_ticket_log({
                                'ticket_id': str(channel.id),
                                'ticket_name': channel.name,
                                'action': 'archived',
                                'timestamp': ts_now
                            })
                        except Exception:
                            pass
                    except Exception as e:
                        try:
                            append_ticket_log({
                                'ticket_id': str(channel.id),
                                'ticket_name': channel.name,
                                'action': 'archive_failed',
                                'timestamp': ts_now,
                                'error': str(e)
                            })
                        except Exception:
                            pass
            except Exception:
                # final fallback: do nothing (but try to log)
                try:
                    append_ticket_log({
                        'ticket_id': str(channel.id),
                        'ticket_name': getattr(channel, 'name', ''),
                        'action': 'close_flow_failed',
                        'timestamp': _dt.datetime.utcnow().isoformat() + 'Z'
                    })
                except Exception:
                    pass

        except Exception:
            pass

    @discord.app_commands.command(name='close', description='Close the current ticket and generate transcript')
    @discord.app_commands.describe(reason='Optional reason for closing the ticket')
    async def close(self, interaction: discord.Interaction, reason: str = 'No reason provided.'):
        """Slash command to close a ticket, generate transcript, log it, DM the creator, and archive channel."""
        # ensure this is used inside a ticket channel
        channel = interaction.channel
        settings = get_settings()
        allowed_category = settings.get('default_category_id')
        # Only enforce ticket-category restriction if configured
        if allowed_category:
            if not channel or not channel.category or str(channel.category.id) != str(allowed_category):
                await interaction.response.send_message('❌ This command can only be used inside a ticket channel (ticket category).', ephemeral=True)
                return

        # permission check: manager or ticket creator
        perms = interaction.user.guild_permissions
        if not perms.manage_channels:
            # check creator
            from utils.storage import get_logs_for_ticket
            try:
                logs = get_logs_for_ticket(str(channel.id))
                created = next((l for l in logs if l.get('action') == 'created'), None)
                creator_id = created.get('creator').get('id') if created and created.get('creator') else None
            except Exception:
                creator_id = None
            if not (creator_id and str(interaction.user.id) == str(creator_id)):
                await interaction.response.send_message('❌ You do not have permission to run this command.', ephemeral=True)
                return

        from utils.storage import get_logs_for_ticket, append_ticket_log
        logs = []
        try:
            logs = get_logs_for_ticket(str(channel.id))
            if not logs:
                all_logs = __import__('utils.storage', fromlist=['get_ticket_logs']).get_ticket_logs()
                for l in all_logs:
                    if l.get('ticket_name') == channel.name:
                        logs.append(l)
                        break
        except Exception:
            logs = []

        creator_id = None
        created_log = next((l for l in logs if l.get('action') == 'created'), None)
        if created_log:
            creator = created_log.get('creator')
            if creator:
                creator_id = creator.get('id')

        await interaction.response.defer(ephemeral=True)

        try:
            # collect messages
            messages = []
            async for m in channel.history(limit=1000, oldest_first=True):
                ts = m.created_at.isoformat()
                author_name = str(m.author)
                author_id = getattr(m.author, 'id', None)
                try:
                    avatar_url = m.author.display_avatar.url
                except Exception:
                    avatar_url = getattr(m.author, 'avatar_url', None) or "https://cdn.discordapp.com/embed/avatars/0.png"
                content = (m.content or "")
                attachments = ' '.join(a.url for a in m.attachments) if m.attachments else ''
                messages.append({
                    'ts': ts,
                    'author_name': author_name,
                    'author_id': str(author_id) if author_id else None,
                    'avatar_url': avatar_url,
                    'content': content,
                    'attachments': attachments,
                    'is_bot': getattr(m.author, 'bot', False)
                })

            transcripts_dir = __import__('utils.storage', fromlist=['TRANSCRIPTS_DIR']).TRANSCRIPTS_DIR
            os.makedirs(transcripts_dir, exist_ok=True)
            filename = f"ticket-{channel.id}.html"
            generated_at = datetime.datetime.utcnow().isoformat() + 'Z'
            ticket_meta = {}
            try:
                from utils.storage import get_logs_for_ticket
                logs = get_logs_for_ticket(str(channel.id))
                created = next((l for l in logs if l.get('action') == 'created'), None)
                if created:
                    ticket_meta = created
            except Exception:
                ticket_meta = {}

            html_out = build_discord_like_transcript(messages, channel.name, ticket_meta, generated_at, filename)
            path = os.path.join(transcripts_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html_out)

            timestamp = datetime.datetime.utcnow().isoformat() + 'Z'
            append_ticket_log({
                'ticket_id': str(channel.id),
                'ticket_name': channel.name,
                'action': 'closed',
                'timestamp': timestamp,
                'executor': {'id': str(interaction.user.id), 'name': str(interaction.user)},
                'reason': reason,
                'transcript_file': filename,
                'allowed_user_id': creator_id
            })

            # remove active ticket entry
            try:
                remove_active_ticket(str(channel.id))
            except Exception:
                pass

            # DM creator with a short-lived signed transcript URL and a link button
            from utils.storage import generate_transcript_url
            signed_url = generate_transcript_url(filename, expires_seconds=3600)

            if creator_id:
                try:
                    user = await self.bot.fetch_user(int(creator_id))
                    try:
                        class LinkView(discord.ui.View):
                            def __init__(self, url):
                                super().__init__(timeout=None)
                                self.add_item(discord.ui.Button(label="View Transcript", url=url))
                        await user.send(content=f"Your ticket '{channel.name}' has been closed. The transcript is available for 1 hour.", view=LinkView(signed_url))
                    except Exception:
                        await user.send(f"Your ticket '{channel.name}' has been closed. You can view the transcript here: {signed_url}")
                except Exception:
                    pass

            try:
                await channel.set_permissions(interaction.guild.default_role, read_messages=False)
                await channel.edit(name=f"closed-{channel.name}")
            except Exception:
                pass

            await interaction.followup.send(f"✅ Ticket closed and transcript generated. {('DM sent to creator.' if creator_id else '')}", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Failed to generate transcript: {e}", ephemeral=True)

    # Ticket management group commands (legacy blacklist command still present)
    # simple admin commands: /ticket_last and /ticket_set
    @discord.app_commands.command(name='ticket_last', description='Show the last ticket number used')
    async def ticket_last(self, interaction: discord.Interaction):
        tickets = __import__('utils.storage', fromlist=['get_tickets_data']).get_tickets_data()
        num = tickets.get('ticket_counter', 0)
        await interaction.response.send_message(f'📌 Last ticket number: {num}', ephemeral=True)

    @discord.app_commands.command(name='ticket_set', description='Set the last ticket number (admin only)')
    @discord.app_commands.describe(number='Ticket number to set')
    async def ticket_set(self, interaction: discord.Interaction, number: int):
        perms = interaction.user.guild_permissions
        if not perms.manage_guild and not perms.manage_roles:
            await interaction.response.send_message('❌ You do not have permission to set ticket number.', ephemeral=True)
            return
        try:
            from utils.storage import get_tickets_data, _save_tickets_data
            data = get_tickets_data()
            data['ticket_counter'] = int(number)
            _save_tickets_data(data)
            await interaction.response.send_message(f'✅ Ticket counter set to {number}', ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'❌ Failed to set ticket number: {e}', ephemeral=True)


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
