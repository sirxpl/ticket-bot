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
        outer_view = self

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
                                def __init__(self, cog):
                                    super().__init__(timeout=60)
                                    self.cog = cog

                                @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
                                async def confirm(self, inter: discord.Interaction, btn: discord.ui.Button):
                                    await inter.response.defer(ephemeral=True)
                                    # proceed to close
                                    try:
                                        await self.cog.do_close(inter.channel, inter.user, "Closed via button")
                                    except Exception:
                                        await inter.followup.send("❌ Failed to close ticket.", ephemeral=True)
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

                            await interaction.response.send_message("Are you sure you want to close this ticket?", view=ConfirmView(self.cog), ephemeral=True)

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
                author = f"{m.author}"
                content = (m.content or "")
                attachments = ' '.join(a.url for a in m.attachments) if m.attachments else ''
                messages.append({'ts': ts, 'author': author, 'content': content, 'attachments': attachments})

            import html, os, datetime, asyncio
            transcript_html = ['<html><head><meta charset="utf-8"><title>Transcript</title></head><body>']
            transcript_html.append(f"<h2>Transcript for {channel.name} ({channel.id})</h2>")
            transcript_html.append(f"<p>Generated at {datetime.datetime.utcnow().isoformat()}Z</p>")
            transcript_html.append('<div style="font-family: monospace;">')
            for m in messages:
                escaped_author = html.escape(m['author'])
                escaped_ts = html.escape(m['ts'])
                escaped_content = html.escape(m['content']).replace('\n', '<br/>')
                transcript_html.append(f'<div style="margin-bottom:8px;"><strong>{escaped_author}</strong> <em>{escaped_ts}</em><div>{escaped_content}</div>')
                if m['attachments']:
                    transcript_html.append(f"<div>Attachments: {html.escape(m['attachments'])}</div>")
                transcript_html.append("</div>")
            transcript_html.append('</div></body></html>')

            filename = f"ticket-{channel.id}.html"
            transcripts_dir = __import__('utils.storage', fromlist=['TRANSCRIPTS_DIR']).TRANSCRIPTS_DIR
            os.makedirs(transcripts_dir, exist_ok=True)
            path = os.path.join(transcripts_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write('\n'.join(transcript_html))

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

            # announce and wait 5 seconds before deleting
            try:
                await channel.send("This ticket will be deleted in 5 seconds.")
            except Exception:
                pass
            await asyncio.sleep(5)
            try:
                await channel.delete(reason=f"Ticket closed: {reason}")
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
            messages = []
            async for m in channel.history(limit=1000, oldest_first=True):
                ts = m.created_at.isoformat()
                author = f"{m.author}"
                content = (m.content or "")
                attachments = ' '.join(a.url for a in m.attachments) if m.attachments else ''
                messages.append({'ts': ts, 'author': author, 'content': content, 'attachments': attachments})

            import html, os, datetime
            transcript_html = ['<html><head><meta charset="utf-8"><title>Transcript</title></head><body>']
            transcript_html.append(f"<h2>Transcript for {channel.name} ({channel.id})</h2>")
            transcript_html.append(f"<p>Generated at {datetime.datetime.utcnow().isoformat()}Z</p>")
            transcript_html.append('<div style="font-family: monospace;">')
            for m in messages:
                escaped_author = html.escape(m['author'])
                escaped_ts = html.escape(m['ts'])
                escaped_content = html.escape(m['content']).replace('\n', '<br/>')
                transcript_html.append(f'<div style="margin-bottom:8px;"><strong>{escaped_author}</strong> <em>{escaped_ts}</em><div>{escaped_content}</div>')
                if m['attachments']:
                    transcript_html.append(f"<div>Attachments: {html.escape(m['attachments'])}</div>")
                transcript_html.append("</div>")
            transcript_html.append('</div></body></html>')

            filename = f"ticket-{channel.id}.html"
            transcripts_dir = __import__('utils.storage', fromlist=['TRANSCRIPTS_DIR']).TRANSCRIPTS_DIR
            os.makedirs(transcripts_dir, exist_ok=True)
            path = os.path.join(transcripts_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write('\n'.join(transcript_html))

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


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
